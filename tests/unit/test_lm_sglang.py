# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :mod:`stargraph.lm.sglang` -- probe, attach, argv, teardown.

No SGLang and no sockets here: :func:`served_models` is monkeypatched so the
attach/spawn decision is exercised in isolation. The real spawn -> ready ->
teardown path runs against a stub HTTP server in
``tests/integration/test_lm_sglang_spawn.py``.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, NoReturn

import pytest

from stargraph.errors import LMServerError
from stargraph.ir import SGLangServer
from stargraph.lm import sglang as sg

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

pytestmark = pytest.mark.unit


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
