# SPDX-License-Identifier: Apache-2.0
"""End-to-end Shipwright run against the llm-ollama Docker container.

Skipped if the container isn't reachable. The test does not assert on
specific LLM output — it asserts on *structure*: the run reaches
landing_summary, all required slots end up filled, and the verifiers
pass on the synthesized files. This is the real-LLM analogue to
test_e2e_new_graph.py.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import dspy  # type: ignore[import-untyped]
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


@pytest.mark.integration
@pytest.mark.slow
async def test_new_graph_with_real_llm(ollama_lm: dspy.LM, tmp_path: Path) -> None:  # pyright: ignore[reportUnknownParameterType]
    """Drive the full --new path with a real LLM, except for HITL.

    The interview pause is collapsed: after parse_brief + gap_check, we
    inject the missing slots as if a human had answered, then continue
    through synthesize + verify. The point is to prove parse_brief and
    propose_questions emit usable structured output from a real model.
    """
    dspy.configure(lm=ollama_lm)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    ctx = cast("ExecutionContext", SimpleNamespace(run_id="r-ollama"))

    state = State(
        mode="new",
        brief=(
            "a triage graph that classifies SOC alerts into "
            "{benign, suspicious, critical} and either auto-resolves "
            "benigns or escalates the rest"
        ),
    )

    state = state.model_copy(update=await TriageGate().execute(state, ctx))

    state = state.model_copy(update=await ParseBrief().execute(state, ctx))
    assert "kind" in state.slots, "ParseBrief failed to extract `kind`"
    assert state.slots["kind"].value in {"graph", "pack"}
    state = state.model_copy(update={"kind": state.slots["kind"].value})

    gap_out = await GapCheck().execute(state, ctx)
    state = state.model_copy(update=gap_out)
    required_before = {q.slot for q in state.open_questions if q.kind == "required"}
    assert required_before, "gap_check produced no required questions on partial spec"

    propose_out = await ProposeQuestions().execute(state, ctx)
    state = state.model_copy(update=propose_out)
    for q in state.open_questions:
        assert q.origin in {"rule", "llm"}
        assert q.slot, "question with empty slot"

    extras = {
        "name": "alert_triage",
        "nodes": ["classify", "auto_resolve", "escalate"],
        "state_fields": [
            {"name": "alert", "type": "str", "annotated": True},
            {"name": "severity", "type": "str", "annotated": True},
        ],
        "stores": {"doc": "sqlite:./.docs"},
        "triggers": [{"type": "manual"}],
    }
    merged = dict(state.slots)
    for n, v in extras.items():
        merged[n] = SpecSlot(name=n, value=v, origin="user")
    state = state.model_copy(update={"slots": merged})

    state = state.model_copy(update=await GapCheck().execute(state, ctx))
    still_required = {q.slot for q in state.open_questions if q.kind == "required"}
    assert not still_required, f"unfilled required slots: {still_required}"

    synth_out = await SynthesizeGraph().execute(state, ctx)
    state = state.model_copy(update=synth_out)
    assert {"state.py", "harbor.yaml", "tests/test_smoke.py"}.issubset(state.artifact_files)

    state = state.model_copy(update=await VerifyStatic(work_dir=tmp_path).execute(state, ctx))
    state = state.model_copy(update=await VerifyTests(work_dir=tmp_path).execute(state, ctx))
    statics = [r for r in state.verifier_results if r.kind == "static"]
    tests = [r for r in state.verifier_results if r.kind == "tests"]
    assert statics[-1].passed, statics[-1].findings
    assert tests[-1].passed, tests[-1].findings

    fix_out = await FixLoop().execute(state, ctx)
    assert fix_out["next_node"] == "landing_summary"
