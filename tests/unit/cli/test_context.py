# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``stargraph context dump`` (the AI grounding pack)."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from stargraph.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.mark.unit
def test_context_dump_emits_grounding_pack(runner: CliRunner) -> None:
    result = runner.invoke(app, ["context", "dump"])
    assert result.exit_code == 0, result.output
    pack = json.loads(result.stdout)

    # Core contracts an author needs to ground itself.
    assert pack["stargraph_version"]
    assert "Graph" in pack["public_api"]
    assert {"echo", "halt"}.issubset(pack["node_kinds"])
    assert "StargraphError" in pack["errors"]
    assert pack["ir_schema"]["path"].endswith("ir-v1.json")
    assert "stargraph.*" in pack["fact_namespaces"]


@pytest.mark.unit
def test_context_dump_compact_is_single_line(runner: CliRunner) -> None:
    result = runner.invoke(app, ["context", "dump", "--compact"])
    assert result.exit_code == 0, result.output
    assert len(result.stdout.strip().splitlines()) == 1
    json.loads(result.stdout)  # still valid JSON


@pytest.mark.unit
def test_context_rejects_unknown_action(runner: CliRunner) -> None:
    result = runner.invoke(app, ["context", "frobnicate"])
    assert result.exit_code != 0
