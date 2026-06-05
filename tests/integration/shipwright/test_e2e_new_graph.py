# SPDX-License-Identifier: Apache-2.0
"""End-to-end: brief → typed slots → no gaps → synthesize → verify all-pass → fix_loop says land."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from harbor.skills.shipwright.nodes.fix import FixLoop
from harbor.skills.shipwright.nodes.interview import GapCheck, ProposeQuestions
from harbor.skills.shipwright.nodes.parse import ParseBrief
from harbor.skills.shipwright.nodes.synthesize import SynthesizeGraph
from harbor.skills.shipwright.nodes.triage import TriageGate
from harbor.skills.shipwright.nodes.verify import VerifyStatic, VerifyTests
from harbor.skills.shipwright.state import SpecSlot, State

if TYPE_CHECKING:
    from pathlib import Path

    from harbor.nodes.base import ExecutionContext


_PARSED = {
    "kind": "graph",
    "purpose": "triage SOC alerts",
    "node_hints": ["classify", "act"],
}


def _stub_parse(self: ParseBrief, brief: str) -> dict[str, Any]:
    return _PARSED


def _stub_propose(
    self: ProposeQuestions, slots: dict[str, Any], existing: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return []


def _stub_node_bodies(self: SynthesizeGraph, slots: dict[str, Any]) -> dict[str, str]:
    return {n: "return {}" for n in slots["nodes"]}


@pytest.mark.integration
async def test_new_graph_happy_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ParseBrief, "_call_predictor", _stub_parse)
    monkeypatch.setattr(ProposeQuestions, "_call_predictor", _stub_propose)
    monkeypatch.setattr(SynthesizeGraph, "_call_predictor", _stub_node_bodies)

    ctx = cast("ExecutionContext", SimpleNamespace(run_id="r-e2e"))
    state = State(mode="new", brief="a triage graph that classifies SOC alerts")

    # 1. triage
    state = state.model_copy(update=await TriageGate().execute(state, ctx))

    # 2. parse_brief
    state = state.model_copy(update=await ParseBrief().execute(state, ctx))
    state = state.model_copy(update={"kind": state.slots["kind"].value})

    # 3. interview — fill in the remaining required slots manually
    extras = {
        "name": "triage",
        "nodes": ["classify", "act"],
        "state_fields": [{"name": "alert", "type": "str", "annotated": True}],
        "stores": {"doc": "sqlite:./.docs"},
        "triggers": [{"type": "manual"}],
    }
    merged = dict(state.slots)
    for n, v in extras.items():
        merged[n] = SpecSlot(name=n, value=v, origin="user")
    state = state.model_copy(update={"slots": merged})

    # gap_check should now report no required gaps
    gap_out = await GapCheck().execute(state, ctx)
    state = state.model_copy(update=gap_out)
    propose_out = await ProposeQuestions().execute(state, ctx)
    state = state.model_copy(update=propose_out)
    assert all(q.kind != "required" for q in state.open_questions)

    # 4. synthesize
    synth_out = await SynthesizeGraph().execute(state, ctx)
    state = state.model_copy(update=synth_out)
    assert set(state.artifact_files) == {"state.py", "harbor.yaml", "tests/test_smoke.py"}

    # 5. verify_static + verify_tests (smoke skipped — would need harbor CLI)
    state = state.model_copy(update=await VerifyStatic(work_dir=tmp_path).execute(state, ctx))
    state = state.model_copy(update=await VerifyTests(work_dir=tmp_path).execute(state, ctx))

    statics = [r for r in state.verifier_results if r.kind == "static"]
    tests = [r for r in state.verifier_results if r.kind == "tests"]
    assert statics[-1].passed is True, statics[-1].findings
    assert tests[-1].passed is True, tests[-1].findings

    # 6. fix_loop should advance to landing_summary
    fix_out = await FixLoop().execute(state, ctx)
    assert fix_out["next_node"] == "landing_summary"
