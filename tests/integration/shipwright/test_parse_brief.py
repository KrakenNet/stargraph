# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from stargraph.skills.shipwright.nodes.parse import ParseBrief
from stargraph.skills.shipwright.state import State

if TYPE_CHECKING:
    from stargraph.nodes.base import ExecutionContext


@pytest.mark.integration
async def test_parse_brief_returns_typed_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = {
        "kind": "graph",
        "purpose": "triage SOC alerts and either auto-resolve or escalate",
        "node_hints": ["classify", "auto_resolve", "escalate"],
    }

    def fake_call(self: ParseBrief, brief: str) -> dict[str, Any]:
        return fake

    monkeypatch.setattr(ParseBrief, "_call_predictor", fake_call)

    out = await ParseBrief().execute(
        State(brief="a triage graph that classifies SOC alerts"),
        cast("ExecutionContext", SimpleNamespace(run_id="r-test")),
    )

    slots = out["slots"]
    assert slots["kind"].value == "graph"
    assert slots["kind"].origin == "llm"
    assert slots["purpose"].value.startswith("triage SOC alerts")
    assert slots["node_hints"].value == ["classify", "auto_resolve", "escalate"]


@pytest.mark.integration
async def test_parse_brief_skips_when_brief_missing() -> None:
    out = await ParseBrief().execute(
        State(brief=None), cast("ExecutionContext", SimpleNamespace(run_id="r-test"))
    )
    assert out == {"slots": {}}
