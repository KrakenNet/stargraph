# SPDX-License-Identifier: Apache-2.0
"""CRITERIA Fancy #4 verification: sandbox quarantine blocks apply + pages on-call.

The 4-step probe (baseline / apply / rollback / re-apply) must
quarantine the plan when *observed* status diverges from *expected*
on any phase. Quarantine MUST block ``progressive_execute`` and emit
a ``[oncall-page]`` work-note on the CR.

Two scenarios:

* **Positive (clean run)** — full pipeline; sandbox not quarantined,
  ``progressive_execute`` runs, no ``[oncall-page]`` work-note.

* **Negative (poisoned plan: apply observed=vulnerable)** — after
  SandboxRunNode runs, poison ``sandbox_probe_steps['apply']`` so
  observed=vulnerable / expected=patched, then re-evaluate quarantine
  on the poisoned dict. Run the rest of the pipeline; assert:
    - ``state.sandbox_quarantined == True``
    - ``state.oncall_paged == True``
    - ``state.canary_passed / stage_passed / fleet_passed`` all False
    - ``per_host_apply_results`` empty (no rollout fired)
    - SN journal contains an ``[oncall-page]`` entry on the CR
    - CR live state did NOT advance to Closed

Run::

    set -a; source demos/cve_remediation/.env; set +a
    HARBOR_SERVICENOW_LIVE=1 \\
    uv run --no-project python -m demos.cve_remediation.scripts.verify_F4_quarantine
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
    CargoNetWritebackNode,
    CloseChangeRequestNode,
    CodeWriterNode,
    CorrelateAssetsBrokerNode,
    CreateChangeRequestNode,
    DriftWatchSpawnNode,
    EmitDocxArchiveNode,
    EmitRetroPayloadNode,
    EnrichCveTrustedNode,
    ExtractTrustedNode,
    HitlChangeApprovalNode,
    HitlRetrospectiveReviewNode,
    IntakeFetchNode,
    PlanKgWritebackNode,
    PlannerNode,
    ProgressiveExecuteNode,
    PublishDocPlusNode,
    RenderDocxNode,
    SandboxDispatchNode,
    SandboxRunNode,
    VerifyImmediateNode,
    WriteRetrospectiveNode,
)
from demos.cve_remediation.graph.state import CveRemState

DEFAULT_CVE = os.environ.get("F4_CVE", "CVE-2024-26130")


async def _drive(
    *,
    cve_id: str,
    label: str,
    poison_apply_observed: bool = False,
) -> tuple[CveRemState, str]:
    state = CveRemState(cve_id=cve_id, run_id=f"verify-F4-{label}")
    ctx = SimpleNamespace(run_id=state.run_id)
    pre = (
        IntakeFetchNode(),
        CanonicalizeTrustedNode(),
        ExtractTrustedNode(),
        EnrichCveTrustedNode(),
        CorrelateAssetsBrokerNode(),
        PlannerNode(),
        CodeWriterNode(),
        SandboxDispatchNode(),
        SandboxRunNode(),
    )
    rest = (
        CreateChangeRequestNode(),
        AttachAllArtifactsNode(),
        HitlChangeApprovalNode(),
        ProgressiveExecuteNode(),
        VerifyImmediateNode(),
        WriteRetrospectiveNode(),
        HitlRetrospectiveReviewNode(),
        EmitRetroPayloadNode(),
        RenderDocxNode(),
        EmitDocxArchiveNode(),
        PublishDocPlusNode(),
        CargoNetWritebackNode(),
        PlanKgWritebackNode(),
        CloseChangeRequestNode(),
        DriftWatchSpawnNode(),
    )
    state = state.model_copy(update={"validation_passed": True})
    for node in pre:
        delta = await node.execute(state, ctx)
        if delta:
            state = state.model_copy(update=delta)
    if poison_apply_observed:
        # Inject observed != expected on the apply phase. Re-runs the
        # quarantine evaluator (mirrors SandboxRunNode._evaluate_quarantine
        # logic) so the rest of the pipeline sees the quarantine flags.
        steps = dict(state.sandbox_probe_steps or {})
        apply_meta = dict(steps.get("apply", {}) or {})
        apply_meta["status"] = "vulnerable"
        # Make sure expected is "patched" so the divergence is honest;
        # SandboxRunNode normally sets this, but defensively pin it.
        apply_meta.setdefault("expected", "patched")
        steps["apply"] = apply_meta
        # Re-run the SAME quarantine eval the node uses. Find the
        # first phase with observed != expected.
        quarantined = False
        reason = ""
        for phase, entry in steps.items():
            if not isinstance(entry, dict):
                continue
            obs = entry.get("status", "")
            exp = entry.get("expected", "")
            if obs and exp and obs != exp:
                quarantined = True
                reason = f"phase={phase} observed={obs!r} expected={exp!r}"
                break
        state = state.model_copy(update={
            "sandbox_probe_steps": steps,
            "sandbox_quarantined": quarantined,
            "sandbox_quarantine_reason": reason,
            "sandbox_status": "quarantined" if quarantined else state.sandbox_status,
        })
    for node in rest:
        delta = await node.execute(state, ctx)
        if delta:
            state = state.model_copy(update=delta)
    sn = state.servicenow_response or {}
    cr_sys_id = str(
        (sn.get("result") or {}).get("sys_id", "") or ""
    )
    return state, cr_sys_id


async def _fetch_journal(cr_sys_id: str) -> list[str]:
    base_url = os.environ.get("SERVICENOW_BASE_URL", "").rstrip("/")
    user = os.environ.get("SERVICENOW_USERNAME", "")
    pw = os.environ.get("SERVICENOW_PASSWORD", "")
    if not (base_url and user and pw and cr_sys_id):
        return []
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{base_url}/api/now/table/sys_journal_field",
            params={
                "sysparm_query": (
                    f"name=change_request^element_id={cr_sys_id}"
                    "^element=work_notes"
                ),
                "sysparm_limit": "200",
                "sysparm_fields": "value",
            },
            auth=(user, pw),
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return []
        rows = (resp.json() or {}).get("result", []) or []
    return [str(r.get("value", "") or "") for r in rows]


async def _fetch_cr_state(cr_sys_id: str) -> str:
    base_url = os.environ.get("SERVICENOW_BASE_URL", "").rstrip("/")
    user = os.environ.get("SERVICENOW_USERNAME", "")
    pw = os.environ.get("SERVICENOW_PASSWORD", "")
    if not (base_url and user and pw and cr_sys_id):
        return ""
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{base_url}/api/now/table/change_request/{cr_sys_id}",
            params={"sysparm_display_value": "all", "sysparm_fields": "state"},
            auth=(user, pw),
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return ""
        sv = (resp.json() or {}).get("result", {}).get("state", {})
        if isinstance(sv, dict):
            return str(sv.get("display_value", "") or sv.get("value", ""))
        return str(sv)


def _grade_positive(state: CveRemState, journal: list[str]) -> bool:
    ok = True
    print(f"  [positive] sandbox_quarantined : {state.sandbox_quarantined}")
    print(f"  [positive] oncall_paged        : {state.oncall_paged}")
    print(f"  [positive] canary/stage/fleet  : "
          f"{state.canary_passed}/{state.stage_passed}/{state.fleet_passed}")
    print(f"  [positive] per_host_apply rows : {len(state.per_host_apply_results)}")
    if state.sandbox_quarantined:
        print("  [positive] ! sandbox_quarantined=True on a clean run")
        ok = False
    if state.oncall_paged:
        print("  [positive] ! oncall_paged=True on a clean run")
        ok = False
    if any("[oncall-page]" in b for b in journal):
        print("  [positive] ! [oncall-page] note appeared on a clean run")
        ok = False
    return ok


def _grade_negative(
    state: CveRemState, journal: list[str], live_state: str
) -> bool:
    ok = True
    print(f"  [negative] sandbox_quarantined : {state.sandbox_quarantined}")
    print(f"  [negative] reason              : {state.sandbox_quarantine_reason!r}")
    print(f"  [negative] oncall_paged        : {state.oncall_paged}")
    print(f"  [negative] canary/stage/fleet  : "
          f"{state.canary_passed}/{state.stage_passed}/{state.fleet_passed}")
    print(f"  [negative] per_host_apply rows : {len(state.per_host_apply_results)}")
    print(f"  [negative] halt_reason         : {state.halt_reason!r}")
    print(f"  [negative] live CR state       : {live_state!r}")

    if not state.sandbox_quarantined:
        print("  [negative] ! sandbox_quarantined=False; quarantine eval failed")
        ok = False
    if not state.oncall_paged:
        print("  [negative] ! oncall_paged=False; on-call routing not fired")
        ok = False
    if state.canary_passed or state.stage_passed or state.fleet_passed:
        print("  [negative] ! rollout fired despite quarantine")
        ok = False
    if state.per_host_apply_results:
        print("  [negative] ! per_host_apply_results non-empty; CargoNet "
              "exec ran despite quarantine")
        ok = False
    if "quarantine" not in (state.halt_reason or "").lower():
        print(f"  [negative] ! halt_reason {state.halt_reason!r} does not "
              "mention quarantine")
        ok = False
    if not any("[oncall-page]" in b for b in journal):
        print("  [negative] ! [oncall-page] work-note missing on CR")
        ok = False
    if live_state.lower() == "closed":
        print("  [negative] ! CR closed despite quarantine")
        ok = False
    return ok


async def main() -> int:
    overall = True
    print("=== F4 VERIFICATION (sandbox quarantine blocks apply) ===\n")
    if not os.environ.get("HARBOR_SERVICENOW_LIVE"):
        print("! HARBOR_SERVICENOW_LIVE unset; CR work-notes are dry-run.\n")
    os.environ.setdefault("CVE_REM_VERIFY_PROBE", "cargonet")
    os.environ["CVE_REM_SKIP_SEED"] = "1"

    print("--- Scenario 1: positive (clean sandbox, apply runs) ---")
    s1, cr1 = await _drive(cve_id=DEFAULT_CVE, label="positive")
    j1 = await _fetch_journal(cr1)
    if not _grade_positive(s1, j1):
        overall = False

    print("\n--- Scenario 2: negative (poisoned apply, "
          "quarantine MUST block) ---")
    s2, cr2 = await _drive(
        cve_id=DEFAULT_CVE, label="negative",
        poison_apply_observed=True,
    )
    j2 = await _fetch_journal(cr2)
    live2 = await _fetch_cr_state(cr2)
    if not _grade_negative(s2, j2, live2):
        overall = False

    print()
    print("=== OVERALL: %s ===" % ("PASS" if overall else "FAIL"))
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
