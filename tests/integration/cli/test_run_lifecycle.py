# SPDX-License-Identifier: Apache-2.0
"""Lifecycle integration tests for `stargraph run` -- exit codes, flag interactions, etc."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stargraph.cli import app

SAMPLE_GRAPH = Path(__file__).resolve().parents[2] / "fixtures" / "sample-graph.yaml"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.mark.integration
def test_simple_run_exits_zero_with_summary(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            str(SAMPLE_GRAPH),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "done" in result.stdout.lower()


@pytest.mark.integration
def test_quiet_no_summary_produces_minimal_output(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            str(SAMPLE_GRAPH),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
            "--quiet",
            "--no-summary",
        ],
    )
    assert result.exit_code == 0
    assert len(result.stdout) < 200


@pytest.mark.integration
def test_summary_json_emits_parseable_json(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            str(SAMPLE_GRAPH),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
            "--quiet",
            "--summary-json",
        ],
    )
    assert result.exit_code == 0
    lines = [ln for ln in result.stdout.splitlines() if ln.strip().startswith("{")]
    assert lines, f"no JSON found in: {result.stdout!r}"
    payload = json.loads(lines[-1])
    assert payload["status"] == "done"
    assert "duration_ms" in payload
    assert "step_count" in payload


@pytest.mark.integration
def test_quiet_and_verbose_combo_rejected(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            str(SAMPLE_GRAPH),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
            "--quiet",
            "--verbose",
        ],
    )
    assert result.exit_code != 0
    combined = (result.output or "") + (result.stderr or "")
    assert "quiet" in combined.lower()


@pytest.mark.integration
def test_unknown_input_key_fails(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            str(SAMPLE_GRAPH),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
            "--inputs",
            "bogus_key=42",
        ],
    )
    assert result.exit_code != 0
    combined = (result.output or "") + (result.stderr or "")
    assert "unknown input" in combined.lower() or "bogus_key" in combined


@pytest.mark.integration
def test_inspect_mode_unchanged(runner: CliRunner) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            str(SAMPLE_GRAPH),
            "--inspect",
        ],
    )
    assert result.exit_code == 0
    assert "graph_hash" in result.stdout
    assert "rule_firings" in result.stdout
