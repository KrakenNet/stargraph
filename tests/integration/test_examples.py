# SPDX-License-Identifier: Apache-2.0
"""Golden tests for the runnable graphs under examples/.

Every example must run end-to-end via `stargraph run` and reach
status=done. This is what keeps the examples (and the getting-started
docs that reference them) from rotting: if an example breaks, CI fails.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stargraph.cli import app

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"

# Examples that demonstrate LLM nodes: excluded from the no-LM golden run and
# driven below under a scripted DummyLM instead (CliRunner shares the process,
# so a dspy.context around invoke() is the stub seam). Live runs pass
# --lm-url/--lm-model.
_LM_EXAMPLES = {"research-bot.yaml"}
EXAMPLE_GRAPHS = sorted(p for p in EXAMPLES_DIR.glob("*.yaml") if p.name not in _LM_EXAMPLES)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_examples_dir_is_not_empty() -> None:
    assert EXAMPLE_GRAPHS, f"no example graphs found under {EXAMPLES_DIR}"


@pytest.mark.integration
@pytest.mark.parametrize("graph", EXAMPLE_GRAPHS, ids=lambda p: p.name)
def test_example_runs_to_done(graph: Path, runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            str(graph),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
            "--inputs",
            "message=hello",
            "--quiet",
            "--summary-json",
        ],
    )
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.stdout.splitlines() if ln.strip().startswith("{")]
    assert lines, f"no JSON summary in output for {graph.name}: {result.stdout!r}"
    payload = json.loads(lines[-1])
    assert payload["status"] == "done", f"{graph.name} did not reach done: {payload}"


@pytest.mark.integration
def test_research_bot_loop_with_scripted_lm(runner: CliRunner, tmp_path: Path) -> None:
    """research-bot.yaml (authoring format) runs one fail->refine->pass loop.

    The DummyLM script finishes the ReAct step without a tool call (no
    network in CI); the judge fails the first draft, the brief template
    re-injects the rationale, and the second draft passes -- proving the
    authored verdict routes fire live through ``stargraph run``.
    """
    import dspy  # pyright: ignore[reportMissingTypeStubs]
    from dspy.utils import DummyLM  # pyright: ignore[reportMissingTypeStubs]

    lm = DummyLM(
        [
            # round 1: react finishes immediately, judge fails the draft
            {"next_thought": "I know this.", "next_tool_name": "finish", "next_tool_args": {}},
            {"reasoning": "first try", "answer": "draft-1"},
            {"reasoning": "too vague, name the engine", "verdict": "fail", "score": "0.2"},
            # round 2: refined draft passes
            {"next_thought": "Refined.", "next_tool_name": "finish", "next_tool_args": {}},
            {"reasoning": "second try", "answer": "draft-2 names CLIPS"},
            {"reasoning": "specific now", "verdict": "pass", "score": "0.9"},
        ]
    )
    with dspy.context(lm=lm):  # pyright: ignore[reportUnknownMemberType]
        result = runner.invoke(
            app,
            [
                "run",
                str(EXAMPLES_DIR / "research-bot.yaml"),
                "--checkpoint",
                str(tmp_path / "ck.sqlite"),
                "--inputs",
                "question=what engine routes stargraph?",
                "--quiet",
                "--summary-json",
            ],
        )

    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.stdout.splitlines() if ln.strip().startswith("{")]
    assert lines, f"no JSON summary in output: {result.stdout!r}"
    payload = json.loads(lines[-1])
    assert payload["status"] == "done"
    state = payload["state_summary"]
    assert state["verdict"] == "pass"
    assert state["answer"] == "draft-2 names CLIPS"  # second round's answer won
    # The judge's round-1 rationale was re-injected into the round-2 brief.
    assert "too vague, name the engine" in state["brief"]
