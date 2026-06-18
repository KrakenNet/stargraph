# SPDX-License-Identifier: Apache-2.0
"""Triage skill — the rule pack actually runs under Fathom, no LLM.

Journey: given an incoming item (subject + body + signals), the node asserts the
signals and keyword tokens as facts, fires the bundled CLIPS rules, and reads
back the category / route / priority decision plus the names of the rules that
fired. A high-severity EDR signal escalates; a billing keyword goes to finance;
anything unremarkable falls through to the default queue.
"""

from __future__ import annotations

import pytest

from stargraph.skills.triage import TRIAGE, TriageState
from stargraph.skills.triage.nodes.triage import Triage

pytestmark = pytest.mark.integration


class _Ctx:
    run_id = "triage-test"


async def test_high_severity_security_escalates() -> None:
    node = Triage()
    state = TriageState(
        subject="Suspicious login from new device",
        body="EDR flagged a credential-stuffing attempt",
        signals={"severity": "high", "source": "edr"},
    )
    out = await node.execute(state, _Ctx())
    assert out["category"] == "security"
    assert out["route"] == "escalate"
    assert out["priority"] == "p1"
    assert "triage-security-high-severity" in out["matched_rules"]


async def test_billing_keyword_routes_to_finance() -> None:
    node = Triage()
    state = TriageState(
        subject="Question about my billing statement",
        body="The charge on my account looks wrong",
        signals={},
    )
    out = await node.execute(state, _Ctx())
    assert out["category"] == "billing"
    assert out["route"] == "finance-queue"
    assert out["priority"] == "p3"
    assert "triage-billing-keyword" in out["matched_rules"]


async def test_unremarkable_item_falls_through_to_default() -> None:
    node = Triage()
    state = TriageState(
        subject="Hello there",
        body="Just checking in on the project status",
        signals={"severity": "low"},
    )
    out = await node.execute(state, _Ctx())
    assert out["category"] == "general"
    assert out["route"] == "queue"
    assert out["priority"] == "p3"
    assert out["matched_rules"] == ["triage-default-queue"]


def test_skill_declares_only_state_channels() -> None:
    assert TRIAGE.kind.value == "workflow"
    assert TRIAGE.site_id == "triage@0.1.0"
    assert TRIAGE.declared_output_keys == frozenset(
        {"subject", "body", "signals", "category", "route", "priority", "matched_rules"}
    )
