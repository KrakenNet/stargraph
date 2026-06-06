# SPDX-License-Identifier: Apache-2.0
"""Smoke test that the HITL fixture YAML validates as IRDocument."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from stargraph.ir import IRDocument


@pytest.mark.integration
def test_hitl_fixture_validates() -> None:
    fixture = Path(__file__).resolve().parents[2] / "fixtures" / "cli" / "hitl-graph.yaml"
    raw = yaml.safe_load(fixture.read_text(encoding="utf-8"))
    ir = IRDocument.model_validate(raw)
    assert ir.id == "graph:cli-hitl-fixture"
    assert {n.id for n in ir.nodes} == {"pre", "post"}
    assert len(ir.rules) == 1
    rule = ir.rules[0]
    assert rule.then[0].kind == "interrupt"
    assert rule.then[1].kind == "goto"
