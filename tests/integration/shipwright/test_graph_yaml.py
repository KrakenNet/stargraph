# SPDX-License-Identifier: Apache-2.0
"""End-to-end check that the canonical Shipwright IR YAML loads and runs.

Exercises the three Plan-1.5 affordances together:

  1. ``state_class``           — the IR points at ``State`` (a real BaseModel
                                 with rich Pydantic fields), not the
                                 ``dict[str, str]`` placeholder schema.
  2. ``module:Class`` kinds    — every node kind is resolved via importlib
                                 against ``stargraph.skills.shipwright.nodes.*``.
  3. ``stargraph run --inspect``  — drives ``Graph.simulate`` so the whole rule
                                 chain fires without needing a live LLM.

A live-LLM end-to-end test (driving the actual node bodies via ollama) lives
in :mod:`test_e2e_with_ollama`; this test is the no-network smoke that pins
the YAML's wiring on every CI run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from stargraph.cli import app
from stargraph.graph.definition import Graph
from stargraph.ir._models import IRDocument

SHIPWRIGHT_GRAPH = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "stargraph"
    / "skills"
    / "shipwright"
    / "graph.yaml"
)

EXPECTED_NODE_IDS = (
    "triage_gate",
    "parse_brief",
    "gap_check",
    "propose_questions",
    "synthesize_graph",
    "verify_static",
    "verify_tests",
    "verify_smoke",
    "fix_loop",
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.mark.integration
def test_graph_yaml_compiles_with_state_class() -> None:
    """IR loads, ``state_class`` resolves to ``State``, all nodes import."""
    ir_dict = yaml.safe_load(SHIPWRIGHT_GRAPH.read_text(encoding="utf-8"))
    ir = IRDocument.model_validate(ir_dict)

    assert ir.state_class == "stargraph.skills.shipwright.state:State"
    assert ir.state_schema == {}
    assert tuple(n.id for n in ir.nodes) == EXPECTED_NODE_IDS

    g = Graph(ir)

    from stargraph.skills.shipwright.state import State

    assert g.state_schema is State
    assert isinstance(g.graph_hash, str) and len(g.graph_hash) == 64


@pytest.mark.integration
def test_inspect_mode_drives_all_rules(runner: CliRunner) -> None:
    """``stargraph run --inspect`` walks the rule chain end-to-end on the IR."""
    result = runner.invoke(app, ["run", str(SHIPWRIGHT_GRAPH), "--inspect"])

    assert result.exit_code == 0, result.output
    assert "graph_hash=" in result.stdout

    # One firing per rule (9 rules in the YAML — see header for topology).
    firing_lines = [
        line for line in result.stdout.splitlines() if line.lstrip().startswith("rule=")
    ]
    assert len(firing_lines) >= 9, result.output

    # Every named node id should appear in the matched-nodes column at least
    # once (the chain walks all of them) — except triage_gate, which is the
    # entry node and so never appears in a `?n <- (node-id ...)` LHS.
    matched_blob = "\n".join(firing_lines)
    for node_id in EXPECTED_NODE_IDS:
        if node_id == "triage_gate":
            continue
        assert node_id in matched_blob, f"{node_id} not in rule firings:\n{matched_blob}"
