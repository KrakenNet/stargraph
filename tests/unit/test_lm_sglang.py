# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :mod:`stargraph.lm.sglang` -- probe, attach, argv, teardown.

No SGLang and no sockets here: :func:`served_models` is monkeypatched so the
attach/spawn decision is exercised in isolation. The real spawn -> ready ->
teardown path runs against a stub HTTP server in
``tests/integration/test_lm_sglang_spawn.py``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import pytest

from stargraph.errors import LMServerError
from stargraph.ir import SGLangServer
from stargraph.lm import sglang as sg

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _skip_preflight(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """Bypass the hardware/runtime preflight for the lifecycle tests.

    These drive a stub launcher on a box that deliberately has no sglang
    installed, which is exactly what :func:`stargraph.lm.hardware.ensure_runtime`
    exists to refuse. The preflight is covered on its own in
    ``tests/unit/test_lm_hardware.py``; here it would only assert that the dev
    venv is a dev venv.
    """

    def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(sg, "ensure_runtime", _noop)
    monkeypatch.setattr(sg, "ensure_weights", _noop)


def _spec(**overrides: object) -> SGLangServer:
    return SGLangServer.model_validate({"model": "Qwen/Qwen3-8B", **overrides})


def _probe(result: list[str] | None) -> Callable[..., list[str] | None]:
    """Stand in for :func:`served_models` with a fixed answer."""

    def _stub(_url: str, **_kwargs: object) -> list[str] | None:
        return result

    return _stub


def _never_spawn(*_args: object, **_kwargs: object) -> NoReturn:
    pytest.fail("attached run must not spawn")


def _fake_spawn(*_args: object, **_kwargs: object) -> str:
    return "proc"


def _noop_ready(*_args: object, **_kwargs: object) -> None:
    return None


def test_base_url_is_openai_compatible() -> None:
    assert sg.base_url(_spec(host="10.0.0.4", port=41002)) == "http://10.0.0.4:41002/v1"


def test_launch_argv_passes_model_port_and_extra_args() -> None:
    argv = sg._launch_argv(_spec(port=41002, args=["--attention-backend", "triton"]))  # pyright: ignore[reportPrivateUsage]

    assert argv[:3] == [sys.executable, "-m", "sglang.launch_server"]
    assert argv[3:5] == ["--model-path", "Qwen/Qwen3-8B"]
    assert "--port" in argv and argv[argv.index("--port") + 1] == "41002"
    assert argv[-2:] == ["--attention-backend", "triton"]


def test_launch_argv_serves_from_the_named_interpreter() -> None:
    """``--sglang-python``: the venv that has sglang need not be stargraph's own."""
    argv = sg._launch_argv(_spec(), "/opt/sglang-venv/bin/python")  # pyright: ignore[reportPrivateUsage]

    assert argv[:3] == ["/opt/sglang-venv/bin/python", "-m", "sglang.launch_server"]


def test_the_preflight_and_the_launch_target_the_same_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A preflight that checked a different interpreter than the one we launch is a lie."""
    seen: dict[str, object] = {}

    def _runtime(_spec: SGLangServer, **kwargs: object) -> None:
        seen["runtime_python"] = kwargs.get("python")

    def _weights(_spec: SGLangServer, **kwargs: object) -> None:
        seen["weights_python"] = kwargs.get("python")

    monkeypatch.setattr(sg, "ensure_runtime", _runtime)
    monkeypatch.setattr(sg, "ensure_weights", _weights)

    def _nothing_listening(_url: str) -> list[str] | None:
        return None

    def _argv(_spec: SGLangServer, python: str | None = None) -> list[str]:
        seen["argv_python"] = python
        return ["true"]

    monkeypatch.setattr(sg, "served_models", _nothing_listening)
    monkeypatch.setattr(sg, "_launch_argv", _argv)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise LMServerError("stop after the spawn decision")

    monkeypatch.setattr(sg, "_await_ready", _boom)
    with (
        pytest.raises(LMServerError),
        sg.sglang_server(
            _spec(port=41999), log_path=tmp_path / "log", python="/opt/sglang-venv/bin/python"
        ),
    ):
        pass

    assert seen["runtime_python"] == "/opt/sglang-venv/bin/python"
    assert seen["weights_python"] == "/opt/sglang-venv/bin/python"
    assert seen["argv_python"] == "/opt/sglang-venv/bin/python"


def test_the_child_gets_the_interpreters_bin_on_path() -> None:
    """Console scripts sglang shells out to (``ninja``) live in the venv's bin/.

    Launching by absolute interpreter path does not put that directory on
    PATH -- activation does. flashinfer's JIT then cannot find ninja, the
    child dies during startup, and the parent reports a bare SIGKILL.
    """
    env = sg._child_env("/opt/sglang-venv/bin/python")  # pyright: ignore[reportPrivateUsage]

    assert env["PATH"].split(os.pathsep)[0] == "/opt/sglang-venv/bin"


def test_a_bin_already_on_path_is_not_duplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation guard: prepending unconditionally would grow PATH every run."""
    bindir = str(Path(sys.executable).resolve().parent)
    monkeypatch.setenv("PATH", os.pathsep.join([bindir, "/usr/bin"]))

    env = sg._child_env(None)  # pyright: ignore[reportPrivateUsage]

    assert env["PATH"].split(os.pathsep).count(bindir) == 1


def test_served_models_returns_none_when_nothing_answers() -> None:
    # Port 1 is privileged and unbound: the probe must report "no server"
    # rather than raising, so the caller spawns one.
    assert sg.served_models("http://127.0.0.1:1/v1", timeout=0.25) is None


def test_attaches_to_running_server_without_spawning(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _spec(port=41002)
    monkeypatch.setattr(sg, "served_models", _probe([spec.model]))
    monkeypatch.setattr(sg, "_spawn", _never_spawn)
    messages: list[str] = []

    with sg.sglang_server(spec, echo=messages.append) as url:
        assert url == "http://127.0.0.1:41002/v1"

    assert any("attached" in m for m in messages)


def test_running_server_with_other_model_is_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sg, "served_models", _probe(["some/other-model"]))

    with pytest.raises(LMServerError, match="serves"), sg.sglang_server(_spec()):
        pytest.fail("must not yield against the wrong model")


def test_spawned_server_is_terminated_on_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _spec()
    terminated: list[object] = []
    monkeypatch.setattr(sg, "served_models", _probe(None))
    monkeypatch.setattr(sg, "_spawn", _fake_spawn)
    monkeypatch.setattr(sg, "_await_ready", _noop_ready)
    monkeypatch.setattr(sg, "_terminate", terminated.append)

    with sg.sglang_server(spec) as url:
        assert url == sg.base_url(spec)
        assert terminated == []

    assert terminated == ["proc"]


def test_spawned_server_is_terminated_when_the_body_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminated: list[object] = []
    monkeypatch.setattr(sg, "served_models", _probe(None))
    monkeypatch.setattr(sg, "_spawn", _fake_spawn)
    monkeypatch.setattr(sg, "_await_ready", _noop_ready)
    monkeypatch.setattr(sg, "_terminate", terminated.append)

    with pytest.raises(RuntimeError, match="boom"), sg.sglang_server(_spec()):
        raise RuntimeError("boom")

    assert terminated == ["proc"]


def test_log_tail_reports_last_lines(tmp_path: Path) -> None:
    log = tmp_path / "sglang.log"
    log.write_text("\n".join(f"line {i}" for i in range(50)), encoding="utf-8")

    tail = sg._log_tail(log)  # pyright: ignore[reportPrivateUsage]

    assert tail.splitlines()[0] == "line 30"
    assert tail.splitlines()[-1] == "line 49"
