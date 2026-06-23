# SPDX-License-Identifier: Apache-2.0
"""Approval skill — the deterministic gate, with the human seam stubbed.

Journey: a proposed action enters the gate. A policy may pre-approve it;
otherwise it sits ``pending`` until a human verdict resolves it. The verdict is
injected, so no live HITL pause is involved, and deny is the default posture —
``approved`` is only ``True`` on an explicit yes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from stargraph.errors import StargraphRuntimeError
from stargraph.skills.approval import APPROVAL, ApprovalState
from stargraph.skills.approval.nodes.decide import ApplyDecision
from stargraph.skills.approval.nodes.request import RequestApproval

if TYPE_CHECKING:
    from pydantic import BaseModel

pytestmark = pytest.mark.integration


class _Ctx:
    run_id = "approval-test"


async def test_policy_auto_approves() -> None:
    node = RequestApproval()
    state = ApprovalState(action="rotate read-only token", auto_approve=True)
    out = await node.execute(state, _Ctx())
    assert out["status"] == "approved"
    assert out["approved"] is True
    assert out["decided_by"] == "policy"
    assert out["reason"] == "auto-approved by policy"


async def test_no_policy_leaves_pending() -> None:
    node = RequestApproval()
    out = await node.execute(ApprovalState(action="delete prod database"), _Ctx())
    assert out == {"status": "pending"}


async def test_request_on_blank_action_raises() -> None:
    node = RequestApproval()
    with pytest.raises(ValueError, match="action is required"):
        await node.execute(ApprovalState(action="  "), _Ctx())


async def test_human_approves() -> None:
    node = ApplyDecision(decider=lambda _state: (True, "looks safe"))
    state = ApprovalState(action="delete prod database", status="pending")
    out = await node.execute(state, _Ctx())
    assert out["status"] == "approved"
    assert out["approved"] is True
    assert out["decided_by"] == "human"
    assert out["reason"] == "looks safe"


async def test_human_denies() -> None:
    node = ApplyDecision(decider=lambda _state: (False, "too risky for prod"))
    state = ApprovalState(action="delete prod database", status="pending")
    out = await node.execute(state, _Ctx())
    assert out["status"] == "denied"
    assert out["approved"] is False
    assert out["decided_by"] == "human"
    assert out["reason"] == "too risky for prod"


async def test_policy_approval_passes_through_decide() -> None:
    # Decider must never be consulted once policy has pre-approved upstream.
    def _boom(_state: BaseModel) -> tuple[bool, str]:
        raise AssertionError("decider should not run on a policy-approved gate")

    node = ApplyDecision(decider=_boom)
    state = ApprovalState(action="rotate read-only token", status="approved")
    out = await node.execute(state, _Ctx())
    assert out == {}


async def test_decide_without_decider_raises() -> None:
    node = ApplyDecision()  # default seam — no human wired
    state = ApprovalState(action="delete prod database", status="pending")
    with pytest.raises(StargraphRuntimeError, match="no decider wired"):
        await node.execute(state, _Ctx())


def test_skill_declares_only_state_channels() -> None:
    assert APPROVAL.kind.value == "workflow"
    assert APPROVAL.site_id == "approval@0.1.0"
    assert APPROVAL.declared_output_keys == frozenset(
        {"action", "payload", "auto_approve", "status", "approved", "decided_by", "reason"}
    )
