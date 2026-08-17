# SPDX-License-Identifier: Apache-2.0
"""Accelerator detection, runtime planning, format refusal (``stargraph.lm.hardware``).

The point of the module under test is that *hardware* and *runtime* are
separate questions, so the tests keep them separate too: detection is driven
by canned vendor-tool output, and planning is driven by explicit
:class:`Runtime` values. Nothing here needs a GPU, and nothing installs
anything -- the one test that exercises the install path asserts on the argv
it would have run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from stargraph.errors import LMServerError
from stargraph.ir import SGLangServer
from stargraph.lm import hardware as hw

if TYPE_CHECKING:
    from collections.abc import Sequence

pytestmark = pytest.mark.unit

# Real `nvidia-smi --query-gpu=name,compute_cap,driver_version --format=csv,noheader`
# output from a two-L40S box.
_NVIDIA_SMI = "NVIDIA L40S, 8.9, 580.173.02\nNVIDIA L40S, 8.9, 580.173.02\n"
_ROCM_SMI = "device,Card series\ncard0,Instinct MI300X\n"


def _linux() -> str:
    return "Linux"


def _darwin() -> str:
    return "Darwin"


def _arm64() -> str:
    return "arm64"


def _fake_run(
    monkeypatch: pytest.MonkeyPatch, table: dict[str, str | None]
) -> list[tuple[str, ...]]:
    """Route :func:`hardware._run` through ``table`` keyed by argv[0]; record calls."""
    seen: list[tuple[str, ...]] = []

    def _run(argv: Sequence[str], *, timeout: float = 0.0) -> str | None:
        del timeout
        seen.append(tuple(argv))
        return table.get(argv[0])

    monkeypatch.setattr(hw, "_run", _run)
    return seen


# --------------------------------------------------------------------------- #
# Detection                                                                     #
# --------------------------------------------------------------------------- #


def test_nvidia_is_detected_with_arch_and_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run(monkeypatch, {"nvidia-smi": _NVIDIA_SMI})
    accel = hw.detect_accelerator()
    assert (accel.vendor, accel.count, accel.arch, accel.driver) == (
        "nvidia",
        2,
        "8.9",
        "580.173.02",
    )
    assert accel.describe() == "NVIDIA L40S x2 (8.9), driver 580.173.02"


def test_amd_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run(monkeypatch, {"rocm-smi": _ROCM_SMI, "rocminfo": "  Name:  gfx942"})
    accel = hw.detect_accelerator()
    assert (accel.vendor, accel.devices, accel.arch) == ("amd", ("Instinct MI300X",), "gfx942")


def test_no_vendor_tool_means_no_accelerator(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CPU-only case must not be reported as a vendor with zero devices."""
    _fake_run(monkeypatch, {})
    monkeypatch.setattr(hw.platform, "system", _linux)
    accel = hw.detect_accelerator()
    assert accel.vendor == "none"
    assert accel.describe() == "no accelerator detected"


def test_apple_silicon_is_named_rather_than_probed(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run(monkeypatch, {})
    monkeypatch.setattr(hw.platform, "system", _darwin)
    monkeypatch.setattr(hw.platform, "machine", _arm64)
    assert hw.detect_accelerator().vendor == "apple"


def test_detection_does_not_consult_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hardware comes from the vendor tools, never from whatever torch was installed.

    This is the bug the module exists for: a CPU-only torch wheel on a box with
    two L40S answers ``torch.cuda.is_available() == False``. If detection ever
    starts asking torch, this test sees ``python`` in the probe argv.
    """
    seen = _fake_run(monkeypatch, {"nvidia-smi": _NVIDIA_SMI})
    assert hw.detect_accelerator().vendor == "nvidia"
    assert all("python" not in argv[0] for argv in seen), seen


# --------------------------------------------------------------------------- #
# Runtime probe                                                                 #
# --------------------------------------------------------------------------- #


def test_probe_reports_this_interpreter_honestly() -> None:
    """No stubbing: the dev venv genuinely has no sglang, and torch is CPU-only.

    Asserting against the real interpreter keeps the probe script itself under
    test -- a syntax error or renamed key in ``_RUNTIME_PROBE`` fails here.
    """
    runtime = hw.probe_runtime()
    assert runtime.sglang is None
    assert runtime.backend in {"cpu", "cuda", "rocm", "xpu"}


def test_probe_of_a_broken_interpreter_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run(monkeypatch, {})
    runtime = hw.probe_runtime("/nonexistent/python")
    assert runtime == hw.Runtime(python="/nonexistent/python")


# --------------------------------------------------------------------------- #
# Planning                                                                      #
# --------------------------------------------------------------------------- #

_NVIDIA = hw.Accelerator(vendor="nvidia", devices=("NVIDIA L40S",), arch="8.9", driver="580.173.02")
_NVIDIA_OLD_DRIVER = hw.Accelerator(
    vendor="nvidia", devices=("NVIDIA A100",), arch="8.0", driver="550.54.15"
)
_WORKING_CUDA = hw.Runtime(
    python="/venv/bin/python", sglang="0.5.0", torch="2.13.0", torch_cuda="13.0", device_count=2
)


def _uv_on_path(_name: str) -> str:
    return "/usr/bin/uv"


def _nvidia_box() -> hw.Accelerator:
    return _NVIDIA


def _cuda_runtime(_python: str | None = None) -> hw.Runtime:
    return _WORKING_CUDA


def test_a_working_cuda_runtime_needs_no_plan() -> None:
    """Anti-vacuity: planning must be able to say "nothing to do"."""
    assert hw.plan_runtime_install(_NVIDIA, _WORKING_CUDA) is None


def test_cpu_torch_on_an_nvidia_box_is_planned_as_cuda13(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hw.shutil, "which", _uv_on_path)
    runtime = hw.Runtime(python="/venv/bin/python", sglang="0.5.0", torch="2.11.0+cpu")
    plan = hw.plan_runtime_install(_NVIDIA, runtime)
    assert plan is not None
    assert plan.automatable
    assert plan.commands == (
        ("uv", "pip", "install", "--python", "/venv/bin/python", "--prerelease=allow", "sglang"),
    )
    assert "cpu build" in plan.reason


def test_a_pre_r580_driver_gets_the_cuda12_wheel_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """CUDA 13 wheels require r580+; an older driver must not be handed them."""
    monkeypatch.setattr(hw.shutil, "which", _uv_on_path)
    plan = hw.plan_runtime_install(_NVIDIA_OLD_DRIVER, hw.Runtime(python="/venv/bin/python"))
    assert plan is not None
    joined = [" ".join(command) for command in plan.commands]
    # verbatim from https://docs.sglang.io/get_started/install.html (CUDA 12 path)
    assert any("whl/cu129" in command for command in joined), joined
    assert any("torch==2.13.0 torchaudio==2.11.0 torchvision" in command for command in joined), (
        joined
    )
    assert any("sglang-kernel" in command for command in joined), joined
    assert any("sgl-deep-gemm" in command and "--no-deps" in command for command in joined), joined


def test_cuda_torch_that_sees_no_devices_still_needs_a_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A container started without --gpus all: right wheel, no devices."""
    monkeypatch.setattr(hw.shutil, "which", _uv_on_path)
    runtime = hw.Runtime(
        python="/venv/bin/python", sglang="0.5.0", torch="2.13.0", torch_cuda="13.0", device_count=0
    )
    assert hw.plan_runtime_install(_NVIDIA, runtime) is not None


def test_amd_is_planned_but_not_automated() -> None:
    """ROCm ships as its own build, so a pip command would be a lie."""
    accel = hw.Accelerator(vendor="amd", devices=("Instinct MI300X",), arch="gfx942")
    plan = hw.plan_runtime_install(accel, hw.Runtime(python="/venv/bin/python"))
    assert plan is not None
    assert not plan.automatable
    assert "amd_gpu" in plan.manual


def test_apple_silicon_gets_the_metal_page_not_a_pip_command() -> None:
    """sglang runs on Metal/MLX, but only from a source build against Xcode."""
    accel = hw.Accelerator(vendor="apple", devices=("Apple silicon (arm64)",))
    plan = hw.plan_runtime_install(accel, hw.Runtime(python="/venv/bin/python"))
    assert plan is not None
    assert not plan.automatable
    assert "apple_metal" in plan.manual


def test_apple_silicon_with_sglang_already_installed_needs_no_plan() -> None:
    accel = hw.Accelerator(vendor="apple", devices=("Apple silicon (arm64)",))
    runtime = hw.Runtime(python="/venv/bin/python", sglang="0.5.0", torch="2.13.0")
    assert hw.plan_runtime_install(accel, runtime) is None


def test_a_cpu_box_with_sglang_installed_is_fine() -> None:
    """sglang serves on CPU; "no GPU" is not by itself a failure."""
    runtime = hw.Runtime(python="/venv/bin/python", sglang="0.5.0", torch="2.13.0")
    assert hw.plan_runtime_install(hw.Accelerator(vendor="none"), runtime) is None


# --------------------------------------------------------------------------- #
# Model format                                                                  #
# --------------------------------------------------------------------------- #


def test_gguf_is_refused_with_the_servable_repo_named() -> None:
    with pytest.raises(LMServerError) as excinfo:
        hw.check_model_format("LiquidAI/LFM2.5-1.2B-Instruct-GGUF", _NVIDIA)
    message = str(excinfo.value)
    assert "GGUF" in message
    assert "LiquidAI/LFM2.5-1.2B-Instruct'" in message


def test_a_safetensors_repo_passes_quietly() -> None:
    assert hw.check_model_format("LiquidAI/LFM2.5-1.2B-Instruct", _NVIDIA) is None


def test_fp8_on_pre_ada_hardware_warns() -> None:
    warning = hw.check_model_format("some-org/model-FP8", _NVIDIA_OLD_DRIVER)
    assert warning is not None
    assert "FP8" in warning


def test_fp8_on_ada_is_not_warned_about() -> None:
    """Mutation guard: the FP8 check must key on capability, not on the name."""
    assert hw.check_model_format("some-org/model-FP8", _NVIDIA) is None


# --------------------------------------------------------------------------- #
# ensure_runtime                                                                #
# --------------------------------------------------------------------------- #

_SPEC = SGLangServer(provider="sglang", model="LiquidAI/LFM2.5-1.2B-Instruct", port=30000)


def _plan_for(monkeypatch: pytest.MonkeyPatch, plan: hw.InstallPlan | None) -> None:
    monkeypatch.setattr(hw, "detect_accelerator", _nvidia_box)
    monkeypatch.setattr(hw, "probe_runtime", _cuda_runtime)

    def _planned(_accel: hw.Accelerator, _runtime: hw.Runtime) -> hw.InstallPlan | None:
        return plan

    monkeypatch.setattr(hw, "plan_runtime_install", _planned)


def test_without_the_flag_the_command_is_reported_not_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = hw.InstallPlan(reason="torch is a cpu build", commands=(("uv", "pip", "install", "x"),))
    _plan_for(monkeypatch, plan)
    ran: list[tuple[object, ...]] = []

    def _record(*args: object, **kwargs: object) -> None:
        ran.append((args, kwargs))

    monkeypatch.setattr(hw.subprocess, "run", _record)

    with pytest.raises(LMServerError) as excinfo:
        hw.ensure_runtime(_SPEC, install=False)

    assert "uv pip install x" in str(excinfo.value)
    assert ran == [], "nothing may be installed without --install-runtime"


def test_with_the_flag_the_plan_is_executed_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = hw.InstallPlan(
        reason="sglang is not installed",
        commands=(("uv", "pip", "install", "sglang"), ("uv", "pip", "install", "torch")),
    )
    monkeypatch.setattr(hw, "detect_accelerator", _nvidia_box)
    monkeypatch.setattr(hw, "probe_runtime", _cuda_runtime)
    plans = iter([plan, None])  # second call re-checks after installing

    def _next_plan(_accel: hw.Accelerator, _runtime: hw.Runtime) -> hw.InstallPlan | None:
        return next(plans)

    monkeypatch.setattr(hw, "plan_runtime_install", _next_plan)

    ran: list[list[str]] = []

    class _Completed:
        returncode = 0

    def _run(argv: list[str], **_kwargs: Any) -> _Completed:
        ran.append(argv)
        return _Completed()

    monkeypatch.setattr(hw.subprocess, "run", _run)
    hw.ensure_runtime(_SPEC, install=True)

    assert ran == [["uv", "pip", "install", "sglang"], ["uv", "pip", "install", "torch"]]


def test_a_failed_install_is_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = hw.InstallPlan(reason="sglang missing", commands=(("uv", "pip", "install", "sglang"),))
    _plan_for(monkeypatch, plan)

    class _Failed:
        returncode = 1

    def _failed(*_args: object, **_kwargs: object) -> _Failed:
        return _Failed()

    monkeypatch.setattr(hw.subprocess, "run", _failed)
    with pytest.raises(LMServerError, match="runtime install failed"):
        hw.ensure_runtime(_SPEC, install=True)


def test_a_usable_runtime_installs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _plan_for(monkeypatch, None)
    ran: list[tuple[object, ...]] = []

    def _record(*args: object, **kwargs: object) -> None:
        ran.append((args, kwargs))

    monkeypatch.setattr(hw.subprocess, "run", _record)
    hw.ensure_runtime(_SPEC, install=True)
    assert ran == []


def test_an_unservable_model_is_refused_before_anything_is_probed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hw, "detect_accelerator", _nvidia_box)

    def _too_late(_python: str | None = None) -> hw.Runtime:
        pytest.fail("the model format must be refused before anything is probed")

    monkeypatch.setattr(hw, "probe_runtime", _too_late)
    spec = SGLangServer(provider="sglang", model="LiquidAI/LFM2.5-1.2B-Instruct-GGUF", port=30000)
    with pytest.raises(LMServerError, match="GGUF"):
        hw.ensure_runtime(spec, install=False)


# --------------------------------------------------------------------------- #
# ensure_weights                                                                #
# --------------------------------------------------------------------------- #


def test_cached_weights_are_not_refetched(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def _run(argv: Sequence[str], **_kwargs: Any) -> str:
        calls.append(tuple(argv))
        return '{"status": "ok"}'

    monkeypatch.setattr(hw, "_run", _run)
    said: list[str] = []
    hw.ensure_weights(_SPEC, echo=said.append)
    assert len(calls) == 1, "a cache hit must not trigger a second, downloading probe"
    assert "already in the local hub cache" in said[0]


def test_a_cache_miss_triggers_a_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def _run(argv: Sequence[str], **_kwargs: Any) -> str:
        calls.append(tuple(argv))
        return '{"status": "miss"}' if argv[-1] == "cached" else '{"status": "ok", "path": "/w"}'

    monkeypatch.setattr(hw, "_run", _run)
    said: list[str] = []
    hw.ensure_weights(_SPEC, echo=said.append)
    assert [argv[-1] for argv in calls] == ["cached", "fetch"]
    assert any("ready at /w" in message for message in said), said


def test_a_local_model_path_is_left_alone(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    def _never(*_args: object, **_kwargs: object) -> str | None:
        pytest.fail("a local model path must not be looked up on the hub")

    monkeypatch.setattr(hw, "_run", _never)
    spec = SGLangServer(provider="sglang", model=str(tmp_path), port=30000)
    hw.ensure_weights(spec)
