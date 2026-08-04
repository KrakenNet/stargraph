# SPDX-License-Identifier: Apache-2.0
"""Tests for ``stargraph skills compile|list`` (P2 CLI surface)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from stargraph.cli import app

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "claude-skills"

pytestmark = pytest.mark.integration


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_skills_compile_vendored_claude_skill(runner: CliRunner) -> None:
    result = runner.invoke(app, ["skills", "compile", str(FIXTURES / "init-project")])
    assert result.exit_code == 0, result.output
    envelope: dict[str, Any] = json.loads(result.output)
    assert envelope["ok"] is True
    assert envelope["spec"]["name"] == "init-project"
    assert envelope["spec"]["namespace"] == "local"
    assert envelope["warnings"] == []


def test_skills_compile_invalid_exits_1_with_errors(runner: CliRunner, tmp_path: Path) -> None:
    bad = tmp_path / "SKILL.md"
    bad.write_text("---\ndescription: no name\n---\nbody\n", encoding="utf-8")
    result = runner.invoke(app, ["skills", "compile", str(bad)])
    assert result.exit_code == 1
    envelope: dict[str, Any] = json.loads(result.output)
    assert envelope["ok"] is False
    assert "missing the required 'name'" in envelope["errors"][0]["actual"]


def test_skills_compile_missing_path_exits_1(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["skills", "compile", str(tmp_path / "nope")])
    assert result.exit_code == 1
    envelope: dict[str, Any] = json.loads(result.output)
    assert envelope["ok"] is False


def test_skills_list_reports_discovered_skills(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.delenv("STARGRAPH_SKILLS_DIR", raising=False)
    result = runner.invoke(app, ["skills", "list", "--skills-dir", str(FIXTURES)])
    assert result.exit_code == 0, result.output
    payload: dict[str, Any] = json.loads(result.output)
    assert payload["count"] == 3
    ids = {row["id"] for row in payload["skills"]}
    assert ids == {
        "local/caveman-stats@0.1.0",
        "local/init-project@0.1.0",
        "local/ruflo-doctor@0.1.0",
    }
