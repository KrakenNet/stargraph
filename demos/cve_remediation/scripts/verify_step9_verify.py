# SPDX-License-Identifier: Apache-2.0
"""CRITERIA.md Step 9 verification: verify identifies remediated.

Two scenarios drive the full pipeline against a live PDI:

* **Positive** -- normal run; assert ``verify_outcome="patched"``,
  per_host_verify_results all ok, drift window in {24, 48, 72} h,
  and the [verify-ok] work_note records both probe_method and
  the sandbox idempotency check.

* **Negative (sandbox.apply forced vulnerable)** -- after Sandbox
  runs, the test poisons ``sandbox_probe_steps["apply"].status`` to
  ``"vulnerable"``. ``offline-trust`` is now coupled to sandbox
  evidence so per-host ``ok`` flips to False, ``verify_outcome``
  becomes ``"unverified"``, no [verify-ok] note appears, the CR does
  NOT close (live state stays at "Implement" or earlier).

Run::

    set -a; source demos/cve_remediation/.env; set +a
    HARBOR_SERVICENOW_LIVE=1 \\
    uv run --no-project python -m demos.cve_remediation.scripts.verify_step9_verify
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

DEFAULT_CVE = os.environ.get("STEP9_CVE", "CVE-2024-26130")
DRIFT_WINDOW_OK = (24, 48, 72)


async def _fetch_cr_state(cr_sys_id: str) -> str:
    base_url = os.environ.get("SERVICENOW_BASE_URL", "").rstrip("/")
    username = os.environ.get("SERVICENOW_USERNAME", "")
    password = os.environ.get("SERVICENOW_PASSWORD", "")
    if not (base_url and username and password and cr_sys_id):
        return ""
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{base_url}/api/now/table/change_request/{cr_sys_id}",
            params={"sysparm_display_value": "all", "sysparm_fields": "state"},
            auth=(username, password),
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return ""
        sv = (resp.json() or {}).get("result", {}).get("state", {})
        if isinstance(sv, dict):
            return str(sv.get("display_value", "") or sv.get("value", ""))
        return str(sv)


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
                "sysparm_fields": "value",
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
    poison_sandbox: bool = False,
) -> tuple[CveRemState, str]:
    state = CveRemState(cve_id=cve_id)
    ctx = SimpleNamespace(run_id=f"verify-step9-{label}")
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
    if poison_sandbox:
        steps = dict(state.sandbox_probe_steps or {})
        apply_meta = dict(steps.get("apply", {}) or {})
        apply_meta["status"] = "vulnerable"
        steps["apply"] = apply_meta
        state = state.model_copy(update={"sandbox_probe_steps": steps})
    for node in rest:
        delta = await node.execute(state, ctx)
        if delta:
            state = state.model_copy(update=delta)
    sn_resp = state.servicenow_response or {}
    result = sn_resp.get("result") or {}
    cr_sys = (
        str(result.get("sys_id", "") or "")
        if isinstance(result, dict)
        else ""
    )
    return state, cr_sys


def _grade_positive(state: CveRemState, journal_bodies: list[str], live_state: str) -> bool:
    ok = True
    print(f"[positive] verify_outcome={state.verify_outcome!r} "
          f"probe_method={state.verify_probe_method!r}")
    if state.verify_outcome != "patched":
        print("[positive] ! verify_outcome must be 'patched'")
        ok = False
    if state.verify_probe_method not in ("offline-trust", "ssh", "k8s"):
        print(f"[positive] ! probe_method invalid: {state.verify_probe_method!r}")
        ok = False
    per_host = list(state.per_host_verify_results or [])
    affected = list(state.affected_host_names or [])
    print(f"[positive] per-host: {len(per_host)} (ok={sum(1 for r in per_host if r.get('ok'))})")
    if len(per_host) != len(affected) or not all(r.get("ok") for r in per_host):
        print("[positive] ! per-host probe coverage / ok mismatch")
        ok = False
    if not all(r.get("evidence") for r in per_host):
        print("[positive] ! per-host evidence missing")
        ok = False
    drift = int(state.drift_watch_window_hours or 0)
    print(f"[positive] drift_watch_window_hours={drift}")
    if drift not in DRIFT_WINDOW_OK:
        print(f"[positive] ! drift window {drift} not in {DRIFT_WINDOW_OK}")
        ok = False
    has_verify_ok = any("[verify-ok]" in b for b in journal_bodies)
    print(f"[positive] [verify-ok] note present: {has_verify_ok}")
    if not has_verify_ok:
        print("[positive] ! [verify-ok] note missing")
        ok = False
    print(f"[positive] live state: {live_state!r}")
    if live_state.lower() != "closed":
        print("[positive] ! CR did not close")
        ok = False
    return ok


def _grade_negative(state: CveRemState, journal_bodies: list[str], live_state: str) -> bool:
    ok = True
    print(f"[negative] verify_outcome={state.verify_outcome!r}")
    if state.verify_outcome == "patched":
        print("[negative] ! verify_outcome MUST NOT be 'patched' "
              "when sandbox.apply was vulnerable")
        ok = False
    per_host = list(state.per_host_verify_results or [])
    print(f"[negative] per-host: {len(per_host)} "
          f"(ok={sum(1 for r in per_host if r.get('ok'))})")
    if any(r.get("ok") for r in per_host):
        print("[negative] ! per-host probe claimed ok despite poisoned sandbox")
        ok = False
    has_verify_ok = any("[verify-ok]" in b for b in journal_bodies)
    print(f"[negative] [verify-ok] note absent: {not has_verify_ok}")
    if has_verify_ok:
        print("[negative] ! [verify-ok] note appeared on a non-patched run")
        ok = False
    has_unverified = any("[verify-unverified]" in b for b in journal_bodies)
    print(f"[negative] [verify-unverified] note present: {has_unverified}")
    if not has_unverified:
        print("[negative] ! [verify-unverified] note missing")
        ok = False
    print(f"[negative] live state: {live_state!r}")
    if live_state.lower() == "closed":
        print("[negative] ! CR closed despite verify=unverified")
        ok = False
    return ok


async def main() -> int:
    overall = True
    print("=== STEP 9 VERIFICATION (verify identifies remediated) ===\n")
    if not os.environ.get("HARBOR_SERVICENOW_LIVE"):
        print(
            "! HARBOR_SERVICENOW_LIVE unset — CR will be dry-run; "
            "live PDI checks will be skipped.\n"
        )
    # Step 9 is the offline-trust-coupling assertion. Force the
    # synthesized probe path so the negative scenario's sandbox
    # poison actually flips per_host ok=False (the live cargonet
    # path probes the real container which would still report
    # patched even if sandbox.apply was poisoned).
    os.environ["CVE_REM_VERIFY_PROBE"] = "offline-trust"

    print("--- Scenario 1: positive (sandbox.apply=patched + fleet_passed) ---")
    s1, cr1 = await _drive(cve_id=DEFAULT_CVE, label="positive")
    journal1 = await _fetch_journal(cr1)
    bodies1 = [str(r.get("value", "") or "") for r in journal1]
    live1 = await _fetch_cr_state(cr1)
    if not _grade_positive(s1, bodies1, live1):
        overall = False

    print("\n--- Scenario 2: negative (sandbox.apply poisoned to vulnerable) ---")
    s2, cr2 = await _drive(
        cve_id=DEFAULT_CVE, label="negative", poison_sandbox=True
    )
    journal2 = await _fetch_journal(cr2)
    bodies2 = [str(r.get("value", "") or "") for r in journal2]
    live2 = await _fetch_cr_state(cr2)
    if not _grade_negative(s2, bodies2, live2):
        overall = False

    print()
    print("=== OVERALL: %s ===" % ("PASS" if overall else "FAIL"))
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
