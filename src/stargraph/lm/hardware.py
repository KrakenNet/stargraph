# SPDX-License-Identifier: Apache-2.0
"""Detect the accelerator, check the sglang runtime against it, fetch weights.

``stargraph run`` can spawn an SGLang server (:mod:`stargraph.lm.sglang`), and
that only works when three things line up: the machine has an accelerator, the
interpreter we spawn has an sglang build *for that accelerator*, and the
weights are on disk. Each has its own failure mode, and left to itself sglang
reports all three as the same opaque subprocess death.

The split that matters here is **hardware vs runtime**. ``torch.cuda.is_available()``
answers "was this torch built with CUDA", not "does this box have a GPU" -- a
CPU-only wheel on a machine with two L40S reports ``False``. So hardware is
detected from the vendor tools (``nvidia-smi``, ``rocm-smi``, ``xpu-smi``,
``npu-smi``), and the runtime is probed separately inside the target
interpreter. A mismatch between the two is the interesting case, and the one
that produces an actionable install command.

What this module will *not* do:

* **Install kernel drivers.** A missing or too-old CUDA/ROCm driver is a
  system-level, root-owned concern. It is reported, never repaired.
* **Install anything without being asked.** Repair runs only under
  ``--install-runtime``; otherwise the plan is printed and the run stops.
* **Rewrite the model.** A graph names the weights it runs (replay depends on
  it). An unservable format is refused with the servable equivalent named, not
  silently swapped.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from stargraph.errors import LMServerError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from stargraph.ir import SGLangServer

__all__ = [
    "Accelerator",
    "InstallPlan",
    "Runtime",
    "check_model_format",
    "detect_accelerator",
    "ensure_runtime",
    "ensure_weights",
    "plan_runtime_install",
    "probe_runtime",
]

Vendor = Literal["nvidia", "amd", "intel", "ascend", "apple", "none"]

_PROBE_TIMEOUT_S = 20.0
_INSTALL_TIMEOUT_S = 3600.0

# CUDA 13 wheels need a r580+ driver (13.0 minimum is 580.65.06 on Linux); an
# older driver has to stay on the CUDA 12 wheel set.
_CUDA13_MIN_DRIVER_MAJOR = 580


@dataclass(frozen=True, slots=True)
class Accelerator:
    """What the *machine* has, as reported by the vendor tooling."""

    vendor: Vendor
    devices: tuple[str, ...] = ()
    arch: str = ""
    """Compute capability (NVIDIA, e.g. ``8.9``) or gfx target (AMD)."""
    driver: str = ""

    @property
    def count(self) -> int:
        return len(self.devices)

    def describe(self) -> str:
        if self.vendor == "none":
            return "no accelerator detected"
        if not self.devices:
            return self.vendor
        name = self.devices[0]
        suffix = f" x{self.count}" if self.count > 1 else ""
        arch = f" ({self.arch})" if self.arch else ""
        driver = f", driver {self.driver}" if self.driver else ""
        return f"{name}{suffix}{arch}{driver}"


@dataclass(frozen=True, slots=True)
class Runtime:
    """What the *interpreter we would spawn* has installed."""

    python: str
    sglang: str | None = None
    torch: str | None = None
    torch_cuda: str | None = None
    """``torch.version.cuda`` -- set on CUDA builds."""
    torch_hip: str | None = None
    """``torch.version.hip`` -- set on ROCm builds."""
    torch_xpu: bool = False
    device_count: int = 0

    @property
    def backend(self) -> str:
        """The accelerator family this torch build can actually drive."""
        if self.torch_hip:
            return "rocm"
        if self.torch_cuda:
            return "cuda"
        if self.torch_xpu:
            return "xpu"
        return "cpu"

    def describe(self) -> str:
        if self.torch is None:
            return "torch not installed"
        return f"torch {self.torch} ({self.backend} build), {self.device_count} device(s) visible"


@dataclass(frozen=True, slots=True)
class InstallPlan:
    """How to repair ``Runtime`` for the detected :class:`Accelerator`."""

    reason: str
    """Why the current runtime cannot serve -- shown to the operator verbatim."""
    commands: tuple[tuple[str, ...], ...] = ()
    """argv sequences to run, in order. Empty when repair is not automatable."""
    manual: str = ""
    """Doc pointer for platforms with no documented pip path."""
    notes: tuple[str, ...] = field(default=())

    @property
    def automatable(self) -> bool:
        return bool(self.commands)


def _run(argv: Sequence[str], *, timeout: float = _PROBE_TIMEOUT_S) -> str | None:
    """stdout of ``argv``, or ``None`` if the binary is absent or it failed.

    Every hardware probe is best-effort: a missing vendor tool means "not this
    vendor", never an error.
    """
    if shutil.which(argv[0]) is None:
        return None
    try:
        completed = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _detect_nvidia() -> Accelerator | None:
    out = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,compute_cap,driver_version",
            "--format=csv,noheader",
        ]
    )
    if not out or not out.strip():
        return None
    names: list[str] = []
    arch = ""
    driver = ""
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        names.append(parts[0])
        arch, driver = parts[1], parts[2]
    if not names:
        return None
    return Accelerator(vendor="nvidia", devices=tuple(names), arch=arch, driver=driver)


def _detect_amd() -> Accelerator | None:
    out = _run(["rocm-smi", "--showproductname", "--csv"])
    if not out or not out.strip():
        return None
    names = [
        line.split(",")[1].strip()
        for line in out.strip().splitlines()[1:]
        if len(line.split(",")) > 1 and line.split(",")[1].strip()
    ]
    if not names:
        return None
    info = _run(["rocminfo"]) or ""
    arch = next(
        (
            tok
            for line in info.splitlines()
            if "gfx" in line
            for tok in line.split()
            if "gfx" in tok
        ),
        "",
    )
    return Accelerator(vendor="amd", devices=tuple(names), arch=arch)


def _detect_intel() -> Accelerator | None:
    out = _run(["xpu-smi", "discovery"])
    if not out or "Device" not in out:
        return None
    names = [
        line.split("|", 2)[-1].strip() for line in out.splitlines() if "Device Name" in line
    ] or ["Intel XPU"]
    return Accelerator(vendor="intel", devices=tuple(names))


def _detect_ascend() -> Accelerator | None:
    out = _run(["npu-smi", "info"])
    if not out or "NPU" not in out:
        return None
    return Accelerator(vendor="ascend", devices=("Ascend NPU",))


def detect_accelerator() -> Accelerator:
    """What this machine has, from the vendor tools -- never from torch.

    Order is by how unambiguous the probe is; a box with two vendors' tools
    installed is vanishingly rare next to the cost of guessing wrong.
    """
    for probe in (_detect_nvidia, _detect_amd, _detect_intel, _detect_ascend):
        found = probe()
        if found is not None:
            return found
    if platform.system() == "Darwin" and platform.machine() in {"arm64", "aarch64"}:
        return Accelerator(vendor="apple", devices=(f"Apple silicon ({platform.machine()})",))
    return Accelerator(vendor="none")


_RUNTIME_PROBE = """
import json, importlib.util
out = {"sglang": None, "torch": None, "torch_cuda": None, "torch_hip": None,
       "torch_xpu": False, "device_count": 0}
spec = importlib.util.find_spec("sglang")
if spec is not None:
    try:
        from importlib.metadata import version
        out["sglang"] = version("sglang")
    except Exception:
        out["sglang"] = "unknown"
if importlib.util.find_spec("torch") is not None:
    try:
        import torch
        out["torch"] = torch.__version__
        out["torch_cuda"] = torch.version.cuda
        out["torch_hip"] = torch.version.hip
        out["torch_xpu"] = bool(getattr(torch, "xpu", None) and torch.xpu.is_available())
        out["device_count"] = torch.cuda.device_count() if torch.cuda.is_available() else 0
    except Exception:
        pass
print(json.dumps(out))
"""


def probe_runtime(python: str | None = None) -> Runtime:
    """What ``python`` (default: the interpreter that would be spawned) has.

    ``sglang`` is located with :func:`importlib.util.find_spec` rather than
    imported -- importing it pulls in torch and CUDA context on a path whose
    only job is to answer "is it there".
    """
    interpreter = python or sys.executable
    out = _run([interpreter, "-c", _RUNTIME_PROBE])
    if not out:
        return Runtime(python=interpreter)
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return Runtime(python=interpreter)
    return Runtime(
        python=interpreter,
        sglang=data["sglang"],
        torch=data["torch"],
        torch_cuda=data["torch_cuda"],
        torch_hip=data["torch_hip"],
        torch_xpu=bool(data["torch_xpu"]),
        device_count=int(data["device_count"]),
    )


def _installer(python: str) -> tuple[str, ...]:
    """``uv pip install`` targeting ``python`` when uv is around, else pip."""
    if shutil.which("uv") is not None:
        return ("uv", "pip", "install", "--python", python)
    return (python, "-m", "pip", "install")


def _nvidia_plan(accel: Accelerator, runtime: Runtime, install: tuple[str, ...]) -> InstallPlan:
    """CUDA 13 wheels on a r580+ driver; the pinned CUDA 12 set below that."""
    driver_major = 0
    if accel.driver:
        head = accel.driver.split(".")[0]
        driver_major = int(head) if head.isdigit() else 0
    reason = (
        f"sglang is not installed for {runtime.python}"
        if runtime.sglang is None
        else f"torch in {runtime.python} is a {runtime.backend} build, "
        f"but {accel.describe()} needs cuda"
    )
    if driver_major >= _CUDA13_MIN_DRIVER_MAJOR:
        return InstallPlan(
            reason=reason,
            commands=((*install, "--prerelease=allow", "sglang"),),
            notes=(f"driver {accel.driver} supports the CUDA 13 wheels",),
        )
    return InstallPlan(
        reason=reason,
        commands=(
            (*install, "--prerelease=allow", "sglang"),
            (
                *install,
                "--force-reinstall",
                "torch==2.13.0",
                "torchaudio==2.11.0",
                "torchvision",
                "--index-url",
                "https://download.pytorch.org/whl/cu129",
            ),
            (
                *install,
                "--force-reinstall",
                "sglang-kernel",
                "--index-url",
                "https://docs.sglang.ai/whl/cu129/",
            ),
            (
                *install,
                "--force-reinstall",
                "sgl-deep-gemm",
                "--index-url",
                "https://docs.sglang.ai/whl/cu129/",
                "--no-deps",
            ),
        ),
        notes=(
            f"driver {accel.driver or 'unknown'} predates r{_CUDA13_MIN_DRIVER_MAJOR}, "
            "so the CUDA 12.9 wheel set is used",
        ),
    )


_INSTALL_DOC = "https://docs.sglang.io/docs/get-started/install"

_MANUAL_PLATFORMS: dict[Vendor, str] = {
    "amd": "https://docs.sglang.io/docs/hardware-platforms/amd_gpu",
    "intel": "https://docs.sglang.io/docs/hardware-platforms/xpu",
    "ascend": "https://docs.sglang.io/docs/hardware-platforms/ascend-npus/getting-started/installation",
    "apple": "https://docs.sglang.io/docs/hardware-platforms/apple_metal",
}


def plan_runtime_install(accel: Accelerator, runtime: Runtime) -> InstallPlan | None:
    """How to make ``runtime`` able to serve on ``accel`` -- ``None`` if it already can.

    Only NVIDIA has an install path SGLang documents as plain wheels. ROCm,
    XPU, Ascend NPU and Apple Metal ship through platform-specific pyprojects,
    docker images, or a source build against the Apple toolchain, so those
    return a plan that names the page instead of pretending a ``pip install``
    would work.
    """
    if runtime.sglang is not None and _backend_matches(accel, runtime):
        return None

    install = _installer(runtime.python)
    if accel.vendor == "nvidia":
        return _nvidia_plan(accel, runtime, install)
    if accel.vendor in _MANUAL_PLATFORMS:
        return InstallPlan(
            reason=(
                f"sglang is not installed for {runtime.python}"
                if runtime.sglang is None
                else f"torch in {runtime.python} is a {runtime.backend} build, "
                f"but {accel.describe()} needs {_expected_backend(accel)}"
            ),
            manual=_MANUAL_PLATFORMS[accel.vendor],
            notes=("sglang ships this platform as its own build, not a plain wheel",),
        )
    # No accelerator found. sglang can serve on CPU, so this is a warning the
    # caller decides about -- not a plan.
    if runtime.sglang is None:
        return InstallPlan(
            reason=f"sglang is not installed for {runtime.python}",
            commands=((*install, "sglang"),),
            notes=("no accelerator detected; a CPU install serves, slowly",),
        )
    return None


def _expected_backend(accel: Accelerator) -> str:
    return {"nvidia": "cuda", "amd": "rocm", "intel": "xpu", "ascend": "npu"}.get(
        accel.vendor, "cpu"
    )


def _backend_matches(accel: Accelerator, runtime: Runtime) -> bool:
    """Does this torch build drive this hardware?

    ``device_count`` is the tiebreaker: a CUDA-built torch that sees zero
    devices is as unusable as a CPU one, and that is what a container missing
    ``--gpus all`` looks like.
    """
    expected = _expected_backend(accel)
    if expected == "cpu":
        return True
    if runtime.backend != expected:
        return False
    return runtime.device_count > 0 or expected == "npu"


def check_model_format(model: str, accel: Accelerator) -> str | None:
    """Refuse formats sglang cannot serve; warn on quantization the GPU lacks.

    Returns a warning string, or ``None`` when nothing is worth saying. Raises
    :class:`~stargraph.errors.LMServerError` for a model that cannot work at
    all -- GGUF being the trap, since most on-device model families publish a
    GGUF repo beside the safetensors one and it is the format LM Studio and
    llama.cpp take.
    """
    tail = model.rsplit("/", 1)[-1]
    if tail.lower().endswith("-gguf") or ".gguf" in tail.lower():
        servable = model[: -len("-GGUF")] if tail.lower().endswith("-gguf") else model
        raise LMServerError(
            f"{model!r} is a GGUF repo; sglang serves safetensors, not GGUF",
            hint=f"use {servable!r} (or serve the GGUF with llama.cpp and pass --lm-url)",
            model=model,
        )
    if "fp8" in tail.lower() and accel.vendor == "nvidia" and accel.arch:
        try:
            capability = float(accel.arch)
        except ValueError:
            return None
        if capability < 8.9:
            return (
                f"{model} is an FP8 checkpoint but {accel.describe()} is "
                f"sm_{accel.arch.replace('.', '')}; FP8 needs sm_89 or newer, "
                "so sglang will fall back or fail"
            )
    return None


def ensure_runtime(
    spec: SGLangServer,
    *,
    python: str | None = None,
    install: bool = False,
    echo: Callable[[str], None] | None = None,
) -> None:
    """Check hardware + runtime before a spawn; optionally repair the runtime.

    Raises :class:`~stargraph.errors.LMServerError` when the runtime cannot
    serve and ``install`` is false, so the operator sees the exact command
    rather than a subprocess that dies during startup.
    """

    def _say(message: str) -> None:
        if echo is not None:
            echo(message)

    accel = detect_accelerator()
    _say(f"hardware: {accel.describe()}")
    warning = check_model_format(spec.model, accel)
    if warning is not None:
        _say(f"warning: {warning}")

    runtime = probe_runtime(python)
    _say(
        f"runtime: {runtime.describe()}" + (f", sglang {runtime.sglang}" if runtime.sglang else "")
    )
    plan = plan_runtime_install(accel, runtime)
    if plan is None:
        return

    if not plan.automatable:
        raise LMServerError(
            plan.reason,
            hint=f"sglang documents this platform at {plan.manual}",
            notes="; ".join(plan.notes),
        )
    rendered = "\n".join("  " + " ".join(command) for command in plan.commands)
    if not install:
        raise LMServerError(
            plan.reason,
            hint=f"re-run with --install-runtime, or install it yourself:\n{rendered}",
            notes="; ".join(plan.notes),
        )

    for note in plan.notes:
        _say(f"note: {note}")
    for index, command in enumerate(plan.commands, start=1):
        _say(f"installing ({index}/{len(plan.commands)}): {' '.join(command)}")
        completed = subprocess.run(list(command), timeout=_INSTALL_TIMEOUT_S, check=False)
        if completed.returncode != 0:
            raise LMServerError(
                f"runtime install failed: {' '.join(command)}",
                hint="install it manually, then re-run without --install-runtime",
                exit_code=str(completed.returncode),
            )
    repaired = probe_runtime(python)
    if plan_runtime_install(accel, repaired) is not None:
        raise LMServerError(
            f"runtime install completed but the runtime still cannot serve ({repaired.describe()})",
            hint=f"sglang documents this platform at {_INSTALL_DOC}",
            python=repaired.python,
        )
    _say(f"runtime ready: {repaired.describe()}")


_WEIGHTS_PROBE = """
import json, sys
model = sys.argv[1]
local_only = sys.argv[2] == "cached"
try:
    from huggingface_hub import snapshot_download
except Exception:
    print(json.dumps({"status": "no-hub"}))
    raise SystemExit(0)
try:
    path = snapshot_download(model, local_files_only=local_only)
except Exception as exc:
    print(json.dumps({"status": "miss", "error": type(exc).__name__}))
    raise SystemExit(0)
print(json.dumps({"status": "ok", "path": path}))
"""


def ensure_weights(
    spec: SGLangServer,
    *,
    python: str | None = None,
    echo: Callable[[str], None] | None = None,
) -> None:
    """Fetch the weights before the server starts, so the boot timeout is a boot timeout.

    sglang downloads on first use inside its own startup, which means a
    multi-gigabyte fetch is racing ``startup_timeout_s`` -- the run dies
    part-way through a download that would have succeeded. Pulling the
    snapshot first separates "weights are arriving" from "the server is
    wedged".

    Best-effort by design: a local path, an unreachable hub, or an absent
    ``huggingface_hub`` all fall through to sglang's own behaviour rather than
    blocking the run.
    """

    def _say(message: str) -> None:
        if echo is not None:
            echo(message)

    if Path(spec.model).exists():
        return
    interpreter = python or sys.executable
    cached = _run([interpreter, "-c", _WEIGHTS_PROBE, spec.model, "cached"])
    if cached and json.loads(cached).get("status") == "ok":
        _say(f"weights: {spec.model} already in the local hub cache")
        return
    _say(f"weights: fetching {spec.model} (not in the local hub cache)")
    fetched = _run(
        [interpreter, "-c", _WEIGHTS_PROBE, spec.model, "fetch"], timeout=_INSTALL_TIMEOUT_S
    )
    if not fetched:
        _say("weights: prefetch unavailable; sglang will download during startup")
        return
    result = json.loads(fetched)
    if result.get("status") == "ok":
        _say(f"weights: ready at {result['path']}")
    else:
        _say("weights: prefetch did not complete; sglang will download during startup")
