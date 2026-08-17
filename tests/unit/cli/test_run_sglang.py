# SPDX-License-Identifier: Apache-2.0
"""``stargraph run --sglang-*`` / graph ``lm:`` block wiring.

Covers spec resolution (flags over the graph's block), the conflict with
``--lm-url``/``--lm-model``, and the end-to-end contract: the endpoint is
held open around the whole run and its URL + model configure the DSPy LM.
The launcher itself is stubbed here -- see ``tests/unit/test_lm_sglang.py``
and ``tests/integration/test_lm_sglang_spawn.py`` for that.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import typer
from typer.testing import CliRunner

from stargraph.cli.run import _resolve_sglang, cmd  # pyright: ignore[reportPrivateUsage]
from stargraph.ir import IRDocument, SGLangServer

if TYPE_CHECKING:
    from collections.abc import Generator

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_GRAPH = REPO_ROOT / "tests" / "fixtures" / "sample-graph.yaml"

_GRAPH_BODY = """\
id: sglang-demo
state:
  message: str
nodes:
  work:
    kind: echo
routes:
  work: done
"""

_GRAPH_WITH_LM = (
    _GRAPH_BODY
    + """\
lm:
  provider: sglang
  model: Qwen/Qwen3-8B
  port: 41002
"""
)

_GRAPH_WITH_LM_ARGS = _GRAPH_WITH_LM + '  args: ["--trust-remote-code"]\n'

_GRAPH_WITH_REMOTE_LM = (
    _GRAPH_BODY
    + """\
lm:
  provider: sglang
  model: Qwen/Qwen3-8B
  host: evil.example.com
"""
)


def _make_app() -> typer.Typer:
    app = typer.Typer()
    app.command()(cmd)
    return app


def _ir(lm: SGLangServer | None = None) -> IRDocument:
    return IRDocument(ir_version="1.0.0", id="run:t", nodes=[], lm=lm)


def _resolve(ir: IRDocument, **overrides: Any) -> SGLangServer | None:
    kwargs: dict[str, Any] = {
        "model": None,
        "host": None,
        "port": None,
        "args": None,
        "timeout": None,
    }
    kwargs.update(overrides)
    return _resolve_sglang(ir, **kwargs)


# --------------------------------------------------------------------------
# spec resolution
# --------------------------------------------------------------------------


def test_no_block_and_no_flags_means_no_server() -> None:
    assert _resolve(_ir()) is None


def test_flags_alone_declare_a_server() -> None:
    spec = _resolve(_ir(), model="Qwen/Qwen3-8B", port=41002, args=["--tp", "2"])

    assert spec is not None
    assert (spec.model, spec.port, spec.args) == ("Qwen/Qwen3-8B", 41002, ["--tp", "2"])
    assert spec.host == "127.0.0.1"


def test_graph_block_alone_declares_a_server() -> None:
    spec = _resolve(_ir(SGLangServer(model="phi-4", port=41002)))

    assert spec is not None
    assert (spec.model, spec.port) == ("phi-4", 41002)


def test_flags_override_the_graph_block_field_by_field() -> None:
    declared = SGLangServer(model="phi-4", port=41002, startup_timeout_s=900)

    spec = _resolve(_ir(declared), port=41010)

    assert spec is not None
    assert spec.port == 41010
    assert (spec.model, spec.startup_timeout_s) == ("phi-4", 900)  # untouched


def test_sglang_flag_without_a_model_is_loud() -> None:
    with pytest.raises(typer.BadParameter, match="--sglang-model"):
        _resolve(_ir(), port=41002)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _stub_launcher(monkeypatch: pytest.MonkeyPatch, calls: list[Any]) -> None:
    import stargraph.lm.sglang as sglang_mod

    @contextlib.contextmanager
    def _fake(spec: SGLangServer, **_kwargs: Any) -> Generator[str]:
        calls.append(("enter", spec))
        try:
            yield "http://stub:41002/v1"
        finally:
            calls.append(("exit", spec))

    monkeypatch.setattr(sglang_mod, "sglang_server", _fake)


def _stub_dspy(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]) -> None:
    import dspy  # pyright: ignore[reportMissingTypeStubs]

    class FakeLM:
        def __init__(self, model: str, **kwargs: Any) -> None:
            captured["model"] = model
            captured["lm_kwargs"] = kwargs

    def _configure(**kwargs: Any) -> None:
        captured["configured"] = kwargs

    monkeypatch.setattr(dspy, "configure", _configure)
    monkeypatch.setattr(dspy, "LM", FakeLM)


def test_sglang_model_flag_binds_the_derived_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[Any] = []
    captured: dict[str, Any] = {}
    _stub_launcher(monkeypatch, calls)
    _stub_dspy(monkeypatch, captured)

    result = CliRunner().invoke(
        _make_app(),
        [
            str(SAMPLE_GRAPH),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
            "--sglang-model",
            "Qwen/Qwen3-8B",
            "--sglang-port",
            "41002",
            "--quiet",
            "--no-summary",
        ],
    )

    assert result.exit_code == 0, result.output
    assert [phase for phase, _ in calls] == ["enter", "exit"]
    assert calls[0][1].model == "Qwen/Qwen3-8B"
    assert captured["model"] == "openai/Qwen/Qwen3-8B"
    assert captured["lm_kwargs"]["api_base"] == "http://stub:41002/v1"


def test_graph_lm_block_is_honoured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[Any] = []
    _stub_launcher(monkeypatch, calls)
    _stub_dspy(monkeypatch, {})
    graph = tmp_path / "graph.yaml"
    graph.write_text(_GRAPH_WITH_LM, encoding="utf-8")

    result = CliRunner().invoke(
        _make_app(),
        [
            str(graph),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
            "--quiet",
            "--no-summary",
        ],
    )

    assert result.exit_code == 0, result.output
    spec = calls[0][1]
    assert (spec.model, spec.port) == ("Qwen/Qwen3-8B", 41002)


def test_no_sglang_request_never_touches_the_launcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[Any] = []
    _stub_launcher(monkeypatch, calls)

    result = CliRunner().invoke(
        _make_app(),
        [
            str(SAMPLE_GRAPH),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
            "--quiet",
            "--no-summary",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == []


@pytest.mark.parametrize(
    "conflicting",
    [["--lm-url", "http://localhost:11434/v1"], ["--lm-model", "gpt-oss:20b"]],
)
def test_lm_flags_conflict_with_sglang(
    conflicting: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[Any] = []
    _stub_launcher(monkeypatch, calls)

    result = CliRunner().invoke(
        _make_app(),
        [
            str(SAMPLE_GRAPH),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
            "--sglang-model",
            "Qwen/Qwen3-8B",
            *conflicting,
            "--quiet",
            "--no-summary",
        ],
    )

    assert result.exit_code != 0
    assert calls == []


def test_inspect_does_not_start_a_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[Any] = []
    _stub_launcher(monkeypatch, calls)
    graph = tmp_path / "graph.yaml"
    graph.write_text(_GRAPH_WITH_LM, encoding="utf-8")

    result = CliRunner().invoke(_make_app(), [str(graph), "--inspect"])

    assert result.exit_code == 0, result.output
    assert calls == []


# --------------------------------------------------------------------------
# operator-only fields on a graph-declared block
# --------------------------------------------------------------------------


def test_graph_declared_args_are_refused() -> None:
    declared = SGLangServer(model="phi-4", args=["--trust-remote-code"])

    with pytest.raises(typer.BadParameter, match="--sglang-arg"):
        _resolve(_ir(declared))


def test_graph_declared_args_are_allowed_once_restated_as_flags() -> None:
    declared = SGLangServer(model="phi-4", args=["--trust-remote-code"])

    spec = _resolve(_ir(declared), args=["--attention-backend", "triton"])

    assert spec is not None
    assert spec.args == ["--attention-backend", "triton"]


def test_graph_declared_non_loopback_host_is_refused() -> None:
    declared = SGLangServer(model="phi-4", host="evil.example.com")

    with pytest.raises(typer.BadParameter, match="--sglang-host"):
        _resolve(_ir(declared))


def test_graph_declared_non_loopback_host_is_allowed_once_restated() -> None:
    declared = SGLangServer(model="phi-4", host="evil.example.com")

    spec = _resolve(_ir(declared), host="evil.example.com")

    assert spec is not None
    assert spec.host == "evil.example.com"


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.5", "::1", "localhost"])
def test_graph_declared_loopback_hosts_pass(host: str) -> None:
    spec = _resolve(_ir(SGLangServer(model="phi-4", host=host)))

    assert spec is not None
    assert spec.host == host


def test_flag_declared_spec_may_reach_anywhere() -> None:
    """Flags are the operator speaking; no restriction applies to them."""
    spec = _resolve(_ir(), model="phi-4", host="10.0.0.9", args=["--trust-remote-code"])

    assert spec is not None
    assert (spec.host, spec.args) == ("10.0.0.9", ["--trust-remote-code"])


@pytest.mark.parametrize("graph_yaml", [_GRAPH_WITH_LM_ARGS, _GRAPH_WITH_REMOTE_LM])
def test_cli_refuses_the_untrusted_halves_of_a_graph_block(
    graph_yaml: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[Any] = []
    _stub_launcher(monkeypatch, calls)
    graph = tmp_path / "graph.yaml"
    graph.write_text(graph_yaml, encoding="utf-8")

    result = CliRunner().invoke(
        _make_app(),
        [str(graph), "--checkpoint", str(tmp_path / "ck.sqlite"), "--quiet", "--no-summary"],
    )

    assert result.exit_code != 0
    assert calls == []


def test_the_lm_is_configured_before_the_node_registry_is_built(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Endpoint first, nodes second -- ``kind: dspy`` needs a live LM to build.

    :func:`stargraph.nodes.dspy.dspy_node_from_config` raises "no LM
    configured" while it is *constructing* the node, not when the node runs.
    Resolving the endpoint after the registry was built therefore made every
    ``kind: dspy`` graph unrunnable -- with a declared ``lm:`` block *and* with
    ``--lm-url``/``--lm-model``. Asserting the call order pins the constraint
    directly, so a future reshuffle of this function fails here rather than in
    the one example that happens to use a dspy node.
    """
    import stargraph.cli.run as run_mod

    order: list[str] = []
    _stub_launcher(monkeypatch, [])

    import dspy  # pyright: ignore[reportMissingTypeStubs]

    def _fake_lm(*_args: Any, **_kwargs: Any) -> object:
        return object()

    def _record_configure(**_kwargs: Any) -> None:
        order.append("configure-lm")

    monkeypatch.setattr(dspy, "LM", _fake_lm)
    monkeypatch.setattr(dspy, "configure", _record_configure)

    real_build = run_mod.build_node_registry

    def _record_build(*args: Any, **kwargs: Any) -> Any:
        order.append("build-nodes")
        return real_build(*args, **kwargs)

    monkeypatch.setattr(run_mod, "build_node_registry", _record_build)

    result = CliRunner().invoke(
        _make_app(),
        [
            str(SAMPLE_GRAPH),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
            "--sglang-model",
            "Qwen/Qwen3-8B",
            "--quiet",
            "--no-summary",
        ],
    )

    assert result.exit_code == 0, result.output
    assert order == ["configure-lm", "build-nodes"]


def _capture_launcher(monkeypatch: pytest.MonkeyPatch, seen: dict[str, Any]) -> None:
    import stargraph.lm.sglang as sglang_mod

    @contextlib.contextmanager
    def _fake(spec: SGLangServer, **kwargs: Any) -> Generator[str]:
        seen.update(kwargs)
        seen["spec"] = spec
        yield "http://stub:41002/v1"

    monkeypatch.setattr(sglang_mod, "sglang_server", _fake)


@pytest.mark.parametrize("flag", [True, False])
def test_install_runtime_flag_reaches_the_launcher(
    flag: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--install-runtime`` is the only thing that lets a run mutate the environment.

    Parametrized rather than asserted one-way: a wiring bug that hard-codes
    ``True`` is exactly as bad as one that hard-codes ``False``, and only the
    false case catches the first.
    """
    seen: dict[str, Any] = {}
    _capture_launcher(monkeypatch, seen)
    _stub_dspy(monkeypatch, {})

    argv = [
        str(SAMPLE_GRAPH),
        "--checkpoint",
        str(tmp_path / "ck.sqlite"),
        "--sglang-model",
        "LiquidAI/LFM2.5-1.2B-Instruct",
        "--quiet",
        "--no-summary",
    ]
    if flag:
        argv.append("--install-runtime")

    result = CliRunner().invoke(_make_app(), argv)

    assert result.exit_code == 0, result.output
    assert seen["install_runtime"] is flag


def _run_with(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *extra: str
) -> tuple[Any, dict[str, Any]]:
    seen: dict[str, Any] = {}
    _capture_launcher(monkeypatch, seen)
    _stub_dspy(monkeypatch, {})
    result = CliRunner().invoke(
        _make_app(),
        [
            str(SAMPLE_GRAPH),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
            "--sglang-model",
            "LiquidAI/LFM2.5-1.2B-Instruct",
            "--quiet",
            "--no-summary",
            *extra,
        ],
    )
    return result, seen


def test_sglang_python_reaches_the_launcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The venv that has sglang is rarely the venv that has stargraph."""
    result, seen = _run_with(monkeypatch, tmp_path, "--sglang-python", sys.executable)

    assert result.exit_code == 0, result.output
    assert seen["python"] == sys.executable


def test_a_venv_directory_is_resolved_to_its_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """What an operator has in hand is the venv, not the path to bin/python."""
    venv = tmp_path / "sglang-venv"
    (venv / "bin").mkdir(parents=True)
    interpreter = venv / "bin" / "python"
    interpreter.symlink_to(sys.executable)

    result, seen = _run_with(monkeypatch, tmp_path, "--sglang-python", str(venv))

    assert result.exit_code == 0, result.output
    # The venv's own path, not the symlink target: resolving it would hand
    # sglang the base interpreter, which has none of the venv's packages.
    assert seen["python"] == str(interpreter)


def test_a_bad_interpreter_is_refused_before_the_graph_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fail on the flag, not on an ENOENT from a subprocess minutes later."""
    result, seen = _run_with(monkeypatch, tmp_path, "--sglang-python", str(tmp_path / "nope"))

    assert result.exit_code != 0
    assert "not an executable interpreter" in result.output
    assert seen == {}, "nothing may be launched once the flag is refused"


def test_no_flag_means_the_launcher_picks_the_default_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mutation guard: the default must stay None, not this process's path."""
    result, seen = _run_with(monkeypatch, tmp_path)

    assert result.exit_code == 0, result.output
    assert seen["python"] is None
