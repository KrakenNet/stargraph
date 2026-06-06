# SPDX-License-Identifier: Apache-2.0
"""GapCheck — turns the gaps-pack output into a typed list[Question]."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from stargraph.skills.shipwright.nodes.interview import GapCheck
from stargraph.skills.shipwright.state import SpecSlot, State

if TYPE_CHECKING:
    from stargraph.nodes.base import ExecutionContext


@pytest.mark.integration
async def test_gap_check_emits_required_questions_for_empty_graph_spec() -> None:
    state = State(
        kind="graph",
        slots={"kind": SpecSlot(name="kind", value="graph", origin="llm")},
    )
    out = await GapCheck().execute(
        state, cast("ExecutionContext", SimpleNamespace(run_id="r-test"))
    )
    qs = {q.slot for q in out["open_questions"]}
    assert {"purpose", "nodes", "state_fields", "stores", "triggers"}.issubset(qs)
    assert all(q.origin == "rule" for q in out["open_questions"])
    assert all(q.kind == "required" for q in out["open_questions"])


@pytest.mark.integration
async def test_gap_check_silent_when_all_required_filled() -> None:
    slots = {
        s: SpecSlot(name=s, value="x", origin="user")
        for s in ("kind", "purpose", "nodes", "state_fields", "stores", "triggers")
    }
    slots["kind"] = SpecSlot(name="kind", value="graph", origin="user")
    state = State(kind="graph", slots=slots)
    out = await GapCheck().execute(
        state, cast("ExecutionContext", SimpleNamespace(run_id="r-test"))
    )
    assert out["open_questions"] == []
