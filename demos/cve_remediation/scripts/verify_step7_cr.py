# SPDX-License-Identifier: Apache-2.0
"""CRITERIA.md Step 7 verification harness.

Drives the full pipeline (intake → CR creation → progressive_execute →
verify) against a real CVE on a live ServiceNow PDI, then live-fetches
the CR + journal table to confirm:

* CR ``sys_id`` round-trips (real PDI write).
* All required spec fields populated (non-empty after CR write):
  ``short_description``, ``description``, ``justification``,
  ``implementation_plan``, ``backout_plan``, ``test_plan``,
  ``risk_impact_analysis``, ``cmdb_ci``, ``business_service``,
  ``service_offering``, ``priority``, ``risk``, ``impact``, ``type``,
  ``category``, ``assignment_group``.
* CIs attached: ``task_ci`` rows linked, count ≥ affected_host_names.
* Lifecycle transitions observable: assess / authorize / scheduled /
  implement / review / closed (or live-fetched ``state`` non-``new``).
* ``work_notes`` journal carries one entry per phase boundary:
  pre-canary (initial create), canary-ok, stage-ok, fleet-ok, verify-ok,
  closed -- 6 distinct entries minimum.

Run::

    set -a; source demos/cve_remediation/.env; set +a
    HARBOR_SERVICENOW_LIVE=1 \\
    uv run --no-project python -m demos.cve_remediation.scripts.verify_step7_cr
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


# Use a CVE that produces a live, applicable CMDB hit on the PDI seed.
# CVE-2024-26130 (cryptography) — pip-channel, full advisory data,
# OSV-derived versions, runs the 4-step probe end-to-end.
DEFAULT_TARGET = os.environ.get("STEP7_CVE", "CVE-2024-26130")

REQUIRED_SPEC_FIELDS = (
    "short_description",
    "description",
    "justification",
    "implementation_plan",
    "backout_plan",
    "test_plan",
    "risk_impact_analysis",
    "category",
    "type",
    "priority",
    "risk",
    "impact",
    "assignment_group",
    "cmdb_ci",
    "business_service",
    "service_offering",
)
EXPECTED_PHASE_NOTES = (
    "create",
    "canary-ok",
    "stage-ok",
    "fleet-ok",
    "verify-ok",
    "closed",
)


async def _run(cve_id: str) -> CveRemState:
    state = CveRemState(cve_id=cve_id)
    ctx = SimpleNamespace(run_id="verify-step7")
    pipeline = (
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
        HitlChangeApprovalNode(),
        ProgressiveExecuteNode(),
        VerifyImmediateNode(),
        # Phase 5 retrospective chain: write retro to PG/Redis/pgvector,
        # gate via HITL, render DOCX, publish to Doc+, write CargoNet +
        # Plan-KG edges. CloseChangeRequestNode last so the [closed]
        # work_note reflects real retro/docplus flags from live state.
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
    for node in pipeline:
        delta = await node.execute(state, ctx)
        if delta:
            state = state.model_copy(update=delta)
    return state


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
    """Pull every sys_journal_field row for this CR (work_notes + comments)."""
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


async def _fetch_task_ci_count(cr_sys_id: str) -> int:
    base_url = os.environ.get("SERVICENOW_BASE_URL", "").rstrip("/")
    username = os.environ.get("SERVICENOW_USERNAME", "")
    password = os.environ.get("SERVICENOW_PASSWORD", "")
    if not (base_url and username and password and cr_sys_id):
        return 0
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{base_url}/api/now/table/task_ci",
            params={
                "sysparm_query": f"task={cr_sys_id}",
                "sysparm_limit": "200",
                "sysparm_fields": "sys_id",
            },
            auth=(username, password),
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return 0
        return len(resp.json().get("result", []) or [])


def _grade_fields(cr: dict[str, Any]) -> list[str]:
    fails: list[str] = []
    for f in REQUIRED_SPEC_FIELDS:
        v = cr.get(f)
        if isinstance(v, dict):
            v = v.get("value", "") or v.get("display_value", "")
        if not v or (isinstance(v, str) and not v.strip()):
            fails.append(f"required field {f!r} empty/missing")
    return fails


def _grade_phase_notes(journal: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    fails: list[str] = []
    bodies = [str(r.get("value", "") or "") for r in journal]
    found: list[str] = []
    for marker in EXPECTED_PHASE_NOTES:
        if any(marker in b for b in bodies):
            found.append(marker)
        else:
            fails.append(f"work_notes missing marker {marker!r}")
    return fails, found


async def main() -> int:
    overall = True
    print("=== STEP 7 VERIFICATION (CR creation + lifecycle + work_notes) ===\n")

    if not os.environ.get("HARBOR_SERVICENOW_LIVE"):
        print("! HARBOR_SERVICENOW_LIVE unset — CR will be dry-run, "
              "live PDI checks will be skipped.\n")
    # CargoNet Phase 3: reset the digital-twin nodes back to a known-
    # vulnerable version BEFORE the pipeline runs. Otherwise a re-run
    # would probe the already-patched state from the previous iteration
    # and the verify would short-circuit with no real apply work.
    # Skipped only when CVE_REM_SKIP_SEED=1 (so test loops can opt out
    # for speed during development).
    if not os.environ.get("CVE_REM_SKIP_SEED"):
        print(f"--- seeding CargoNet to vulnerable for {DEFAULT_TARGET} ---")
        from demos.cve_remediation.scripts.seed_cargonet_vulnerable import (
            reset_cargonet_to_vulnerable,
        )
        seed_summary = await reset_cargonet_to_vulnerable(
            cve_id=DEFAULT_TARGET, verbose=True
        )
        if seed_summary.get("errors"):
            for e in seed_summary["errors"]:
                print(f"  ! seed: {e}")
        attempted = [
            r for r in seed_summary.get("nodes", []) if not r.get("skipped")
        ]
        if attempted and not all(r.get("ok") for r in attempted):
            print("  ! seed left at least one node off the vulnerable pin; "
                  "verify will likely show patched without doing real work")
        print()
    # CargoNet Phase 1: default verify probe to live ``cargonet`` mode
    # so per-host probes hit the digital-twin REST surface and real
    # observed versions land on each per_host_verify_results row.
    # Override with CVE_REM_VERIFY_PROBE=offline-trust for the legacy
    # synthesized stand-in when CargoNet is unreachable.
    os.environ.setdefault("CVE_REM_VERIFY_PROBE", "cargonet")
    cve = DEFAULT_TARGET
    print(f"[target] {cve}")
    try:
        state = await _run(cve)
    except Exception as exc:  # noqa: BLE001
        print(f"  EXCEPTION: {type(exc).__name__}: {exc}")
        return 1

    sn_resp = state.servicenow_response or {}
    request_body = state.cr_request_body or {}
    cr_sys_id = ""
    result = sn_resp.get("result") or {}
    if isinstance(result, dict):
        cr_sys_id = str(result.get("sys_id", "") or "")
    cr_number = ""
    if isinstance(result, dict):
        cr_number = str(result.get("number", "") or "")

    print(f"  cr_correlation_id  : {state.cr_correlation_id}")
    print(f"  cr_status          : {state.cr_status}")
    print(f"  servicenow.status  : {sn_resp.get('status', 'unknown')}")
    print(f"  cr_sys_id          : {cr_sys_id or '(none)'}")
    print(f"  cr_number          : {cr_number or '(none)'}")
    print(f"  request_body keys  : {sorted(request_body.keys())}")

    # 1) Spec fields populated in the request body (always checkable).
    body_fails = _grade_fields(request_body)
    for f in body_fails:
        print(f"  ! request_body missing/empty: {f}")
    if body_fails:
        overall = False

    # 2) Live PDI checks (only if live mode and we got a sys_id).
    if not cr_sys_id:
        print("  ! no sys_id returned — pipeline did not create live CR")
        overall = False
        print("\n=== OVERALL: FAIL ===")
        return 1

    cr_live = await _fetch_cr(cr_sys_id)
    if not cr_live:
        print("  ! live CR fetch failed (PDI unreachable / auth)")
        overall = False
        print("\n=== OVERALL: FAIL ===")
        return 1

    live_fails = _grade_fields(cr_live)
    for f in live_fails:
        print(f"  ! live CR missing/empty: {f}")
    if live_fails:
        overall = False

    # 3) State / lifecycle observable.
    state_field = cr_live.get("state", "")
    if isinstance(state_field, dict):
        state_label = str(state_field.get("display_value", "") or
                          state_field.get("value", ""))
    else:
        state_label = str(state_field)
    print(f"  live state         : {state_label!r}")
    if state_label.lower() in ("", "new", "-5", "-6"):
        print("  ! CR never advanced past 'new'")
        overall = False

    # 4) task_ci count covers affected hosts.
    affected = list(state.affected_host_names or [])
    task_ci_count = await _fetch_task_ci_count(cr_sys_id)
    print(f"  affected hosts     : {len(affected)}")
    print(f"  task_ci rows       : {task_ci_count}")
    if affected and task_ci_count < len(affected):
        print(f"  ! task_ci count {task_ci_count} < affected hosts {len(affected)}")
        overall = False

    # 5) work_notes per phase boundary.
    journal = await _fetch_journal(cr_sys_id)
    print(f"  journal rows       : {len(journal)}")
    phase_fails, found = _grade_phase_notes(journal)
    print(f"  phase markers seen : {found}")
    for f in phase_fails:
        print(f"  ! {f}")
    if phase_fails:
        overall = False

    # 6) Retrospective writeback flags. Step 8/9 territory but the
    # CloseChangeRequestNode's [closed] note depends on these being
    # set, and the retrospective chain is now part of the verify
    # pipeline. Fail-loud when any store rejects the writeback rather
    # than the previous silent-False behavior.
    retro_pg = bool(getattr(state, "retro_pg_written", False))
    retro_redis = bool(getattr(state, "retro_redis_written", False))
    retro_pgvec = bool(getattr(state, "retro_pgvector_written", False))
    docplus = bool(getattr(state, "docplus_published", False))
    docplus_attach = str(getattr(state, "docplus_attachment_sys_id", "") or "")
    cargonet_done = bool(getattr(state, "cargonet_writeback_done", False))
    plankg_done = bool(getattr(state, "plan_kg_writeback_done", False))
    last_retro_err = str(getattr(state, "last_retro_error", "") or "")
    print(f"  retro pg / redis   : {retro_pg} / {retro_redis}")
    print(f"  retro pgvector     : {retro_pgvec}")
    print(f"  docplus published  : {docplus} "
          f"(attachment sys_id={docplus_attach or 'n/a'})")
    print(f"  cargonet writeback : {cargonet_done}")
    print(f"  plan_kg writeback  : {plankg_done}")
    if last_retro_err:
        print(f"  ! retro errors     : {last_retro_err}")
    for label, ok in (
        ("retro_pg_written", retro_pg),
        ("retro_redis_written", retro_redis),
        ("retro_pgvector_written", retro_pgvec),
        ("docplus_published", docplus),
        ("cargonet_writeback_done", cargonet_done),
        ("plan_kg_writeback_done", plankg_done),
    ):
        if not ok:
            print(f"  ! retro chain: {label}=False")
            overall = False
    if docplus and not docplus_attach:
        print("  ! docplus published but no SN attachment sys_id returned")
        overall = False

    # 6b) DriftWatchSpawn round-trip (gap E): child run id + spawn path
    # surfaced; CR carries a [drift-watch] work_note.
    drift_child = str(getattr(state, "drift_child_run_id", "") or "")
    drift_path = str(getattr(state, "drift_spawn_path", "") or "")
    drift_err = str(getattr(state, "last_drift_spawn_error", "") or "")
    print(f"  drift child run id : {drift_child or '(none)'}")
    print(f"  drift spawn path   : {drift_path or '(unset)'}")
    if not drift_child:
        print("  ! drift_watch never produced a child run id")
        overall = False
    if drift_path not in ("scheduler", "http", "intent-only"):
        print("  ! drift_watch spawn_path missing/unknown")
        overall = False
    if drift_err:
        print(f"  ! drift spawn error: {drift_err}")
    drift_marker_seen = any(
        "drift-watch" in str(r.get("value", "") or "") for r in journal
    )
    print(f"  drift-watch marker : {drift_marker_seen}")
    if not drift_marker_seen:
        # Re-fetch journal because the [drift-watch] note appended after
        # the earlier journal pull (post-close).
        late_journal = await _fetch_journal(cr_sys_id)
        drift_marker_seen = any(
            "drift-watch" in str(r.get("value", "") or "") for r in late_journal
        )
        if drift_marker_seen:
            print("  drift-watch marker (late fetch): True")
        else:
            print("  ! [drift-watch] work_note not found on CR")
            overall = False

    # 6c) Per-host verify probe (gap C): every affected host must have
    # a ``per_host_verify_results`` row with ``ok=True``; the probe
    # method must be a real value (no silent default).
    per_host = list(getattr(state, "per_host_verify_results", []) or [])
    probe_method = str(getattr(state, "verify_probe_method", "") or "")
    print(f"  verify probe method: {probe_method or '(unset)'}")
    print(f"  per-host results   : {len(per_host)} (ok={sum(1 for r in per_host if r.get('ok'))})")
    if probe_method not in ("cargonet", "offline-trust", "ssh", "k8s"):
        print("  ! verify probe method must be cargonet / offline-trust / ssh / k8s")
        overall = False
    if len(per_host) != len(affected):
        print(f"  ! per_host_verify_results count {len(per_host)} != "
              f"affected hosts {len(affected)}")
        overall = False
    if any(not r.get("ok") for r in per_host):
        bad = [r.get("host") for r in per_host if not r.get("ok")]
        print(f"  ! per-host probe failed for: {bad}")
        overall = False

    # 7) Service-field lookup status (gap H): fail when business_service
    # / service_offering came from neither a live SN lookup nor an env
    # override -- those CRs land with the fields blank and the criterion
    # for "all required spec fields populated" silently fails.
    svc_status = str(getattr(state, "cr_service_lookup_status", "") or "")
    print(f"  service lookup     : {svc_status or '(unset)'}")
    if svc_status not in ("resolved_live", "resolved_env"):
        print(
            "  ! service-field lookup failed — set "
            "SERVICENOW_SERVICE_SYS_ID + SERVICENOW_SERVICE_OFFERING_SYS_ID "
            "or seed a cmdb_ci_service named 'Vulnerability Management'"
        )
        overall = False

    # 8) HITL gate tier-mandated block (gap D): synthetic state with
    # ssvc_tier="act_hitl_required" + HARBOR_SERVICENOW_LIVE set must
    # produce decision="block" / hitl_blocked_at="change_approval"
    # with no fabricated response. Run direct against the node so we
    # don't need a separate live PDI CR for the assertion.
    print()
    print("=== HITL gate (tier-mandated block) ===")
    from demos.cve_remediation.graph.real_nodes import HitlChangeApprovalNode
    from demos.cve_remediation.graph.state import CveRemState as _State
    synth = _State(
        cve_id="CVE-9999-0000-hitl-test",
        ssvc_tier="act_hitl_required",
        servicenow_response={"result": {"sys_id": "synthetic-cr-not-real"}},
    )
    hitl_node = HitlChangeApprovalNode()
    hitl_ctx = SimpleNamespace(run_id="verify-step7-hitl")
    delta = await hitl_node.execute(synth, hitl_ctx)
    blocked = delta.get("hitl_blocked_at") == "change_approval"
    no_response = "response" not in delta
    print(f"  decision blocked   : {blocked}")
    print(f"  no response emitted: {no_response}")
    if not (blocked and no_response):
        print("  ! act_hitl_required must block without emitting a response")
        overall = False
    # Negative case: same synthetic but tier=act → decision=approve
    # (offline / live indifferently when no env override).
    synth2 = synth.model_copy(update={"ssvc_tier": "act"})
    delta2 = await hitl_node.execute(synth2, hitl_ctx)
    has_response = "response" in delta2
    print(f"  act tier responded : {has_response}")
    if not has_response:
        # In live PDI mode without an env override, act tier is also
        # held at "block" by the live-broker default. The verify only
        # fails when the override path collapses entirely.
        if not os.environ.get("CVE_REM_LIVE_BROKER"):
            print("  ! act tier should auto-respond when no live broker")
            overall = False

    print()
    print("=== OVERALL: %s ===" % ("PASS" if overall else "FAIL"))
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
