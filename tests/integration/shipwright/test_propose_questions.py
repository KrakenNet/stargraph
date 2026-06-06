# SPDX-License-Identifier: Apache-2.0
"""ProposeQuestions — LLM ceiling that surfaces edge-case/soft questions."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from stargraph.skills.shipwright.nodes.interview import ProposeQuestions
from stargraph.skills.shipwright.state import Question, SpecSlot, State

if TYPE_CHECKING:
    from stargraph.nodes.base import ExecutionContext


@pytest.mark.integration
async def test_propose_questions_dedups_against_open_questions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_call(
        self: ProposeQuestions, slots: dict[str, Any], existing: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return [
            {"slot": "purpose", "prompt": "what about timeouts?", "kind": "edge_case"},
            {"slot": "budget_cap", "prompt": "no budget cap?", "kind": "soft"},
        ]

    monkeypatch.setattr(ProposeQuestions, "_call_predictor", fake_call)

    existing = [
        Question(slot="purpose", prompt="?", kind="required", schema={}, origin="rule"),
    ]
    state = State(
        kind="graph",
        slots={"purpose": SpecSlot(name="purpose", value="x", origin="user")},
        open_questions=existing,
    )
    out = await ProposeQuestions().execute(
        state, cast("ExecutionContext", SimpleNamespace(run_id="r-test"))
    )

    slots = [q.slot for q in out["open_questions"]]
    origins = {q.slot: q.origin for q in out["open_questions"]}
    assert slots == ["purpose", "budget_cap"]
    assert origins == {"purpose": "rule", "budget_cap": "llm"}


@pytest.mark.integration
async def test_propose_questions_noop_with_no_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_call(
        self: ProposeQuestions, slots: dict[str, Any], existing: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(ProposeQuestions, "_call_predictor", fake_call)
    out = await ProposeQuestions().execute(
        State(), cast("ExecutionContext", SimpleNamespace(run_id="r-test"))
    )
    assert out == {"open_questions": []}
