# SPDX-License-Identifier: Apache-2.0
"""Smoke test: stargraph.yaml parses and references match the State schema."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

SHIPWRIGHT = Path(__file__).resolve().parents[3] / "src" / "stargraph" / "skills" / "shipwright"


@pytest.mark.integration
def test_stargraph_yaml_parses() -> None:
    raw = (SHIPWRIGHT / "stargraph.yaml").read_text()
    parsed = yaml.safe_load(raw)
    assert parsed["name"] == "shipwright"
    assert parsed["state"].endswith("state.py:State")


@pytest.mark.integration
def test_stargraph_yaml_lists_required_nodes() -> None:
    parsed = yaml.safe_load((SHIPWRIGHT / "stargraph.yaml").read_text())
    names = {n["name"] for n in parsed["nodes"]}
    expected = {
        "triage_gate",
        "parse_brief",
        "gap_check",
        "propose_questions",
        "human_input",
        "synthesize_graph",
        "verify_static",
        "verify_tests",
        "verify_smoke",
        "fix_loop",
    }
    assert expected.issubset(names)
