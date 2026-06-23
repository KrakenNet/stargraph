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
EXAMPLE_GRAPHS = sorted(EXAMPLES_DIR.glob("*.yaml"))


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
