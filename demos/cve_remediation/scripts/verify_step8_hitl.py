# SPDX-License-Identifier: Apache-2.0
"""CRITERIA.md Step 8 verification: HITL gate actually blocks.

Two scenarios drive the full pipeline against a live PDI and assert
that ProgressiveExecuteNode does NOT fire when the gate emits a
block / reject decision:

* **Reject path** -- ``CVE_REM_HITL_DECISION=reject`` forces the gate
  to mark cr_status="rejected"; progressive_execute must short-circuit,
  no canary-ok / stage-ok / fleet-ok work_notes appear, CR stays at
  scheduled (or earlier).
* **Tier-mandated block** -- ssvc_tier is forced to
  ``act_hitl_required`` after the Planner and ``HARBOR_SERVICENOW_LIVE``
  is set; the gate emits hitl_blocked_at="change_approval" with no
  response and no real approver lands → progressive_execute is held.

Run::

    set -a; source demos/cve_remediation/.env; set +a
    HARBOR_SERVICENOW_LIVE=1 \\
    uv run --no-project python -m demos.cve_remediation.scripts.verify_step8_hitl
"""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from typing import Any

import httpx

from demos.cve_remediation.graph.real_nodes import (
    AttachAllArtifactsNode,
    CanonicalizeTrustedNode,
    CloseChangeRequestNode,
    CodeWriterNode,
    CorrelateAssetsBrokerNode,
    CreateChangeRequestNode,
    EnrichCveTrustedNode,
    ExtractTrustedNode,
    HitlChangeApprovalNode,
    IntakeFetchNode,
    PlannerNode,
    ProgressiveExecuteNode,
    SandboxDispatchNode,
    SandboxRunNode,
    VerifyImmediateNode,
)
from demos.cve_remediation.graph.state import CveRemState

DEFAULT_CVE = os.environ.get("STEP8_CVE", "CVE-2024-26130")


async def _fetch_cr(cr_sys_id: str) -> dict[str, Any]:
    base_url = os.environ.get("SERVICENOW_BASE_URL", "").rstrip("/")
    username = os.environ.get("SERVICENOW_USERNAME", "")
    password = os.environ.get("SERVICENOW_PASSWORD", "")
    if not (base_url and username and password and cr_sys_id):
        return {}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{base_url}/api/now/table/change_request/{cr_sys_id}",
            params={"sysparm_display_value": "all"},
            auth=(username, password),
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return {}
        return resp.json().get("result", {})


async def _fetch_journal(cr_sys_id: str) -> list[dict[str, Any]]:
    base_url = os.environ.get("SERVICENOW_BASE_URL", "").rstrip("/")
    username = os.environ.get("SERVICENOW_USERNAME", "")
    password = os.environ.get("SERVICENOW_PASSWORD", "")
    if not (base_url and username and password and cr_sys_id):
        return []
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{base_url}/api/now/table/sys_journal_field",
            params={
                "sysparm_query": (
                    f"name=change_request^element_id={cr_sys_id}"
                    "^element=work_notes"
                ),
                "sysparm_limit": "100",
                "sysparm_fields": "value,sys_created_on",
            },
            auth=(username, password),
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return []
        return resp.json().get("result", []) or []


async def _drive(
    *,
    cve_id: str,
    label: str,
    force_tier: str | None = None,
) -> tuple[CveRemState, str]:
    state = CveRemState(cve_id=cve_id)
    ctx = SimpleNamespace(run_id=f"verify-step8-{label}")
    pre_hitl = (
        IntakeFetchNode(),
        CanonicalizeTrustedNode(),
        ExtractTrustedNode(),
        EnrichCveTrustedNode(),
        CorrelateAssetsBrokerNode(),
        PlannerNode(),
        CodeWriterNode(),
        SandboxDispatchNode(),
        SandboxRunNode(),
        CreateChangeRequestNode(),
        AttachAllArtifactsNode(),
    )
    post_hitl = (
        ProgressiveExecuteNode(),
        VerifyImmediateNode(),
        CloseChangeRequestNode(),
    )
    state = state.model_copy(update={"validation_passed": True})
    for node in pre_hitl:
        delta = await node.execute(state, ctx)
        if delta:
            state = state.model_copy(update=delta)
    if force_tier:
        state = state.model_copy(update={"ssvc_tier": force_tier})
    delta = await HitlChangeApprovalNode().execute(state, ctx)
    if delta:
        state = state.model_copy(update=delta)
    for node in post_hitl:
        delta = await node.execute(state, ctx)
        if delta:
            state = state.model_copy(update=delta)
    sn_resp = state.servicenow_response or {}
    result = sn_resp.get("result") or {}
    cr_sys_id = (
        str(result.get("sys_id", "") or "")
        if isinstance(result, dict)
        else ""
    )
    return state, cr_sys_id


def _grade(
    state: CveRemState,
    cr_live: dict[str, Any],
    journal: list[dict[str, Any]],
    *,
    label: str,
) -> bool:
    ok = True
    print(f"[{label}] cr_status={state.cr_status!r} "
          f"hitl_blocked_at={state.hitl_blocked_at!r} "
          f"halt_reason={state.halt_reason!r}")
    canary_done = bool(state.canary_passed)
    stage_done = bool(state.stage_passed)
    fleet_done = bool(state.fleet_passed)
    print(f"[{label}] canary/stage/fleet passed: "
          f"{canary_done}/{stage_done}/{fleet_done}")
    if canary_done or stage_done or fleet_done:
        print(f"[{label}] ! progressive_execute fired despite HITL block")
        ok = False
    bodies = [str(r.get("value", "") or "") for r in journal]
    fired_markers = [
        m for m in ("canary-ok", "stage-ok", "fleet-ok", "verify-ok", "[closed]")
        if any(m in b for b in bodies)
    ]
    print(f"[{label}] post-HITL markers in journal: {fired_markers}")
    if fired_markers:
        print(f"[{label}] ! markers leaked past the gate: {fired_markers}")
        ok = False
    halt_marker = any("[halt]" in b for b in bodies)
    print(f"[{label}] [halt] work_note present: {halt_marker}")
    if not halt_marker:
        print(f"[{label}] ! no [halt] work_note recorded")
        ok = False
    state_field = cr_live.get("state", "")
    if isinstance(state_field, dict):
        state_label = str(
            state_field.get("display_value", "") or state_field.get("value", "")
        )
    else:
        state_label = str(state_field)
    print(f"[{label}] live CR state: {state_label!r}")
    if state_label.lower() in ("closed", "implement", "review"):
        print(f"[{label}] ! CR advanced past Scheduled despite block")
        ok = False
    return ok


async def main() -> int:
    overall = True
    print("=== STEP 8 VERIFICATION (HITL gate halts progressive_execute) ===\n")
    if not os.environ.get("HARBOR_SERVICENOW_LIVE"):
        print(
            "! HARBOR_SERVICENOW_LIVE unset — CR will be dry-run; "
            "live PDI checks will be skipped.\n"
        )

    # Scenario 1: explicit auto-reject.
    print("--- Scenario 1: CVE_REM_HITL_DECISION=reject ---")
    os.environ["CVE_REM_HITL_DECISION"] = "reject"
    state, cr_sys = await _drive(
        cve_id=DEFAULT_CVE, label="reject", force_tier=None
    )
    cr_live = await _fetch_cr(cr_sys) if cr_sys else {}
    journal = await _fetch_journal(cr_sys) if cr_sys else []
    if not _grade(state, cr_live, journal, label="reject"):
        overall = False

    # Scenario 2: tier-mandated block (act_hitl_required).
    print("\n--- Scenario 2: tier=act_hitl_required (uncircumventable) ---")
    os.environ.pop("CVE_REM_HITL_DECISION", None)
    state2, cr_sys2 = await _drive(
        cve_id=DEFAULT_CVE,
        label="block",
        force_tier="act_hitl_required",
    )
    cr_live2 = await _fetch_cr(cr_sys2) if cr_sys2 else {}
    journal2 = await _fetch_journal(cr_sys2) if cr_sys2 else []
    if not _grade(state2, cr_live2, journal2, label="block"):
        overall = False

    print()
    print("=== OVERALL: %s ===" % ("PASS" if overall else "FAIL"))
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
