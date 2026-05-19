# SPDX-License-Identifier: Apache-2.0
"""CRITERIA Fancy #5: sandbox-prod divergence detection + halt-new.

Inject sandbox=patched + prod-host=vulnerable post-apply. Verifier:

1. **Cleanup** — wipe any prior cve_rem_plan_quarantine row for this
   plan_hash so the test starts honest.
2. **Run A: divergence path** — drive pipeline; sandbox returns
   patched (real probes); after ProgressiveExecute (which writes the
   patch), kill the patch on every host via
   ``reset_cargonet_to_vulnerable``; VerifyImmediateNode then sees
   sandbox=patched + per-host=vulnerable; the divergence branch must
   set sandbox_prod_divergence=True, oncall_paged=True, persist
   plan-quarantine + GEPA rows, halt CR at review.
3. **Run B: halt-new** — re-run the SAME CVE; PlanQuarantineGateNode
   reads cve_rem_plan_quarantine, sets plan_quarantined=True,
   ProgressiveExecute halts before any rollout, oncall_paged work-note
   on CR.

Run::

    set -a; source demos/cve_remediation/.env; set +a
    HARBOR_SERVICENOW_LIVE=1 \
    uv run --no-project python -m demos.cve_remediation.scripts.verify_F5_sandbox_prod_divergence
"""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from typing import Any

import asyncpg

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
    PlanQuarantineGateNode,
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
from demos.cve_remediation.scripts.seed_cargonet_vulnerable import (
    reset_cargonet_to_vulnerable,
)

DEFAULT_CVE = os.environ.get("F5_CVE", "CVE-2024-26130")


async def _drive_to_apply(cve_id: str, label: str) -> CveRemState:
    """Drive pipeline up through ProgressiveExecuteNode (apply done)."""
    state = CveRemState(cve_id=cve_id, run_id=f"verify-F5-{label}")
    ctx = SimpleNamespace(run_id=state.run_id)
    pre = (
        IntakeFetchNode(),
        CanonicalizeTrustedNode(),
        ExtractTrustedNode(),
        EnrichCveTrustedNode(),
        CorrelateAssetsBrokerNode(),
        PlannerNode(),
        PlanQuarantineGateNode(),
        SandboxDispatchNode(),
        SandboxRunNode(),
        CodeWriterNode(),
        CreateChangeRequestNode(),
        AttachAllArtifactsNode(),
        HitlChangeApprovalNode(),
        ProgressiveExecuteNode(),
    )
    state = state.model_copy(update={"validation_passed": True})
    for node in pre:
        delta = await node.execute(state, ctx)
        if delta:
            state = state.model_copy(update=delta)
    return state


async def _drive_after_apply(state: CveRemState) -> CveRemState:
    ctx = SimpleNamespace(run_id=state.run_id)
    rest = (
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
    for node in rest:
        delta = await node.execute(state, ctx)
        if delta:
            state = state.model_copy(update=delta)
    return state


async def _wipe_quarantine(plan_hash: str) -> None:
    if not plan_hash:
        return
    dsn = os.environ.get("POSTGRES_DSN", "").strip()
    if not dsn:
        return
    conn = await asyncpg.connect(dsn)
    try:
        for stmt in (
            "DELETE FROM cve_rem_plan_quarantine WHERE plan_hash = $1",
            "DELETE FROM cve_rem_gepa_divergence WHERE plan_hash = $1",
        ):
            try:
                await conn.execute(stmt, plan_hash)
            except asyncpg.exceptions.UndefinedTableError:
                # Table not yet created on first run; nothing to wipe.
                pass
    finally:
        await conn.close()


async def _fetch_quarantine(plan_hash: str) -> dict[str, Any]:
    dsn = os.environ.get("POSTGRES_DSN", "").strip()
    if not (dsn and plan_hash):
        return {}
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            """
            SELECT plan_hash, cve_id, reason, recorded_at
            FROM cve_rem_plan_quarantine
            WHERE plan_hash = $1
            """,
            plan_hash,
        )
        return dict(row) if row else {}
    finally:
        await conn.close()


async def _fetch_gepa(plan_hash: str) -> list[dict[str, Any]]:
    dsn = os.environ.get("POSTGRES_DSN", "").strip()
    if not (dsn and plan_hash):
        return []
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT id, plan_hash, sandbox_status,
                   canary_passed, stage_passed, fleet_passed,
                   recorded_at
            FROM cve_rem_gepa_divergence
            WHERE plan_hash = $1
            ORDER BY recorded_at DESC
            """,
            plan_hash,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


def _grade_divergence(state: CveRemState) -> bool:
    ok = True
    print(f"  [diverg] verify_outcome      : {state.verify_outcome!r}")
    print(f"  [diverg] sandbox_prod_diverg : {state.sandbox_prod_divergence}")
    print(f"  [diverg] oncall_paged        : {state.oncall_paged}")
    print(f"  [diverg] gepa_record_id      : {state.gepa_divergence_record_id!r}")
    if state.verify_outcome != "divergence":
        print("  [diverg] ! verify_outcome must be 'divergence'")
        ok = False
    if not state.sandbox_prod_divergence:
        print("  [diverg] ! sandbox_prod_divergence=False")
        ok = False
    if not state.oncall_paged:
        print("  [diverg] ! oncall_paged=False on divergence")
        ok = False
    if not state.gepa_divergence_record_id:
        print("  [diverg] ! GEPA divergence record id empty")
        ok = False
    return ok


async def _fetch_journal(cr_sys_id: str) -> list[str]:
    import os as _os
    import httpx as _httpx
    base = _os.environ.get("SERVICENOW_BASE_URL", "").rstrip("/")
    user = _os.environ.get("SERVICENOW_USERNAME", "")
    pw = _os.environ.get("SERVICENOW_PASSWORD", "")
    if not (base and user and pw and cr_sys_id):
        return []
    async with _httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{base}/api/now/table/sys_journal_field",
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


def _grade_haltnew(state: CveRemState) -> bool:
    ok = True
    print(f"  [haltnew] plan_quarantined     : {state.plan_quarantined}")
    print(f"  [haltnew] reason               : "
          f"{state.plan_quarantine_reason!r}")
    print(f"  [haltnew] halt_reason          : {state.halt_reason!r}")
    print(f"  [haltnew] canary/stage/fleet   : "
          f"{state.canary_passed}/{state.stage_passed}/{state.fleet_passed}")
    print(f"  [haltnew] per_host_apply rows  : "
          f"{len(state.per_host_apply_results)}")
    print(f"  [haltnew] oncall_paged         : {state.oncall_paged}")
    if not state.plan_quarantined:
        print("  [haltnew] ! plan_quarantined=False; gate did not fire")
        ok = False
    if state.canary_passed or state.stage_passed or state.fleet_passed:
        print("  [haltnew] ! rollout fired despite halt-new")
        ok = False
    if state.per_host_apply_results:
        print("  [haltnew] ! apply ran despite halt-new")
        ok = False
    if "halt-new" not in (state.halt_reason or "").lower() and \
       "plan-kg" not in (state.halt_reason or "").lower():
        print(f"  [haltnew] ! halt_reason {state.halt_reason!r} does not "
              "mention halt-new / plan-KG")
        ok = False
    if not state.oncall_paged:
        print("  [haltnew] ! oncall_paged=False on halt-new")
        ok = False
    return ok


async def main() -> int:
    overall = True
    print("=== F5 VERIFICATION (sandbox-prod divergence + halt-new) ===\n")
    if not os.environ.get("HARBOR_SERVICENOW_LIVE"):
        print("! HARBOR_SERVICENOW_LIVE unset; CR work-notes are dry-run.\n")
    if not os.environ.get("POSTGRES_DSN"):
        print("! POSTGRES_DSN unset -- cannot persist quarantine; FAIL")
        return 1
    os.environ.setdefault("CVE_REM_VERIFY_PROBE", "cargonet")
    os.environ["CVE_REM_SKIP_SEED"] = "1"

    # Stage 0: cleanup any prior quarantine row.
    plan_hash_seed = "?"
    print("--- Stage 0: cleanup prior quarantine row (best-effort) ---")
    pre = await _drive_to_apply(DEFAULT_CVE, "preview")
    plan_hash_seed = str(pre.plan_hash or "")
    print(f"  plan_hash for this CVE: {plan_hash_seed!r}")
    await _wipe_quarantine(plan_hash_seed)

    # Stage 1: drive the divergence run.
    print("\n--- Stage 1: divergence run "
          "(sandbox=patched, kill prod patch post-apply) ---")
    state = await _drive_to_apply(DEFAULT_CVE, "diverg")
    sandbox_obj = state.sandbox
    sb_status = getattr(sandbox_obj, "status", "")
    sb_apply = getattr(sandbox_obj, "apply_probe", "")
    print(f"  sandbox.status      : {sb_status!r}")
    print(f"  sandbox.apply_probe : {sb_apply!r}")
    print(f"  canary/stage/fleet  : "
          f"{state.canary_passed}/{state.stage_passed}/{state.fleet_passed}")
    if not (state.canary_passed and state.stage_passed and state.fleet_passed):
        print("  ! pre-apply did not succeed; F5 cannot proceed honestly")
        return 1

    # Kill the patch on every host (this is what 'prod=vulnerable
    # post-apply' looks like in our digital-twin: an external
    # config-drift event between apply and verify).
    print("  ... killing patch on every host post-apply ...")
    summary = await reset_cargonet_to_vulnerable(
        cve_id=DEFAULT_CVE, host_filter=None, verbose=False,
    )
    failed = [r for r in summary.get("nodes", [])
              if not r.get("ok") and not r.get("skipped")]
    if failed:
        print(f"  ! seed kill failed on hosts: {failed}")
        return 1

    state = await _drive_after_apply(state)
    if not _grade_divergence(state):
        overall = False

    # F5-6: strict CR journal assertion. Divergence branch must emit
    # an [oncall-page]-shaped marker (here: the
    # [verify-divergence] work-note that calls out the divergence;
    # plus we expect halt-new entries from the next stage to carry
    # [oncall-page] explicitly).
    cr_div = str(
        (state.servicenow_response or {}).get("result", {}).get("sys_id", "") or ""
    )
    journal_div = await _fetch_journal(cr_div)
    has_div_note = any("[verify-divergence]" in b for b in journal_div)
    print(f"  [diverg] [verify-divergence] note in CR journal: {has_div_note}")
    if not has_div_note:
        print("  [diverg] ! [verify-divergence] note missing on CR")
        overall = False

    # Stage 2: confirm PG persistence.
    print("\n--- Stage 2: confirm plan-quarantine + GEPA persistence ---")
    quar = await _fetch_quarantine(plan_hash_seed)
    gepa = await _fetch_gepa(plan_hash_seed)
    print(f"  plan_quarantine row : {quar}")
    print(f"  GEPA rows for plan  : {len(gepa)} ({gepa[:1]})")
    if not quar:
        print("  ! plan_hash not in cve_rem_plan_quarantine")
        overall = False
    if len(gepa) < 1:
        print("  ! no GEPA divergence row recorded")
        overall = False

    # Stage 3: halt-new on second attempt with same plan_hash.
    print("\n--- Stage 3: halt-new on re-run with same plan_hash ---")
    state2 = await _drive_to_apply(DEFAULT_CVE, "haltnew")
    if not _grade_haltnew(state2):
        overall = False
    # F5-6: strict [oncall-page] grep on the halt-new CR journal.
    cr_halt = str(
        (state2.servicenow_response or {}).get("result", {}).get("sys_id", "") or ""
    )
    journal_halt = await _fetch_journal(cr_halt)
    has_oncall = any("[oncall-page]" in b for b in journal_halt)
    print(f"  [haltnew] [oncall-page] note in CR journal: {has_oncall}")
    if not has_oncall:
        print("  [haltnew] ! [oncall-page] work-note missing on halt-new CR")
        overall = False

    # Stage 4: cleanup so subsequent test runs don't see persistent
    # quarantine. Reseed hosts back to patched is out of scope for this
    # verifier (operator action); we only wipe the PG quarantine row.
    print("\n--- Stage 4: cleanup quarantine row ---")
    await _wipe_quarantine(plan_hash_seed)
    print("  wiped.")

    print()
    if overall:
        print("=== OVERALL: PASS ===")
    else:
        print("=== OVERALL: FAIL ===")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
