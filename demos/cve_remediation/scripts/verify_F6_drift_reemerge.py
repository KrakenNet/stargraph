# SPDX-License-Identifier: Apache-2.0
"""CRITERIA Fancy #6: drift re-emergence creates linked child run.

After Phase 4 (verify=patched), the verifier:

1. Runs the full pipeline; asserts ``verify_outcome==patched``.
2. **Kills the patch** on exactly one CargoNet host using
   ``reset_cargonet_to_vulnerable`` (downgrades the package to a
   known vulnerable version on that one host only).
3. **Detects drift** by re-probing every previously-patched host via
   CargoNet REST; the targeted host must report
   ``observed != fix_version`` while the others stay patched.
4. **Spawns a drift_watch child run** carrying ``parent_run_id``
   via ``DriftWatchSpawnNode`` (deterministic intent-id path when no
   live runner is reachable).
5. **Persists the parent->child audit link** to
   ``cve_rem_drift_links`` so an external auditor can reconstruct
   the chain from PG without replaying the pipeline.

Run::

    set -a; source demos/cve_remediation/.env; set +a
    HARBOR_SERVICENOW_LIVE=1 \
    uv run --no-project python -m demos.cve_remediation.scripts.verify_F6_drift_reemerge
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
from harbor.tools.cargonet import (
    cargonet_exec as _cn_run,
    cargonet_find_node as _cn_find,
)

DEFAULT_CVE = os.environ.get("F6_CVE", "CVE-2024-26130")


async def _drive(cve_id: str) -> CveRemState:
    state = CveRemState(cve_id=cve_id, run_id="verify-F6-parent")
    ctx = SimpleNamespace(run_id=state.run_id)
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
        WriteRetrospectiveNode(),
        HitlRetrospectiveReviewNode(),
        EmitRetroPayloadNode(),
        RenderDocxNode(),
        EmitDocxArchiveNode(),
        PublishDocPlusNode(),
        CargoNetWritebackNode(),
        PlanKgWritebackNode(),
        CloseChangeRequestNode(),
    )
    state = state.model_copy(update={"validation_passed": True})
    for node in pipeline:
        delta = await node.execute(state, ctx)
        if delta:
            state = state.model_copy(update=delta)
    return state


def _show_command(channel: str, pkg: str) -> str:
    if channel in ("pip", "pypi"):
        return f"pip show {pkg} 2>/dev/null | grep ^Version: || true"
    if channel in ("apt", "deb"):
        return f"dpkg -s {pkg} 2>/dev/null | grep ^Version: || true"
    if channel in ("rpm", "yum", "dnf"):
        return (
            f"rpm -q --queryformat 'Version: %{{VERSION}}\\n' {pkg} "
            f"2>/dev/null || true"
        )
    if channel == "npm":
        return (
            f"npm list -g {pkg} --depth=0 --json 2>/dev/null | "
            f"python3 -c 'import sys,json;d=json.load(sys.stdin);"
            f"v=(d.get(\"dependencies\") or {{}}).get(\"{pkg}\",{{}}).get(\"version\",\"\");"
            f"print(\"Version:\",v)' || true"
        )
    return f"pip show {pkg} 2>/dev/null | grep ^Version: || true"


def _parse_version(out: str) -> str:
    for line in (out or "").splitlines():
        if line.lower().startswith("version:"):
            return line.split(":", 1)[1].strip()
    return ""


async def _probe_host(host: str, pkg: str, channel: str) -> str:
    hit = await _cn_find(name=host)
    if not hit:
        return ""
    lab_id = str(hit.get("lab_id", ""))
    node_id = str(hit.get("node_id", ""))
    if not (lab_id and node_id):
        return ""
    resp = await _cn_run(
        lab_id=lab_id, node_id=node_id,
        command=_show_command(channel, pkg), timeout=30.0,
    )
    return _parse_version(resp.get("output", ""))


async def _persist_drift_link(
    *,
    parent_run_id: str,
    child_run_id: str,
    cve_id: str,
    host: str,
    observed: str,
    fix: str,
) -> str:
    dsn = os.environ.get("POSTGRES_DSN", "").strip()
    if not dsn:
        return "POSTGRES_DSN unset"
    try:
        conn = await asyncpg.connect(dsn)
    except Exception as exc:
        return f"connect failed: {type(exc).__name__}: {exc}"
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cve_rem_drift_links (
                id SERIAL PRIMARY KEY,
                parent_run_id TEXT NOT NULL,
                child_run_id  TEXT NOT NULL,
                cve_id        TEXT NOT NULL,
                host          TEXT NOT NULL,
                observed_version TEXT,
                fix_version      TEXT,
                detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO cve_rem_drift_links
              (parent_run_id, child_run_id, cve_id, host,
               observed_version, fix_version)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            parent_run_id, child_run_id, cve_id, host, observed, fix,
        )
    except Exception as exc:
        await conn.close()
        return f"insert failed: {type(exc).__name__}: {exc}"
    await conn.close()
    return ""


async def _fetch_drift_link(
    parent_run_id: str, child_run_id: str
) -> dict[str, Any]:
    dsn = os.environ.get("POSTGRES_DSN", "").strip()
    if not dsn:
        return {}
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            """
            SELECT parent_run_id, child_run_id, cve_id, host,
                   observed_version, fix_version, detected_at
            FROM cve_rem_drift_links
            WHERE parent_run_id = $1 AND child_run_id = $2
            ORDER BY detected_at DESC
            LIMIT 1
            """,
            parent_run_id, child_run_id,
        )
        return dict(row) if row else {}
    finally:
        await conn.close()


async def main() -> int:
    overall = True
    print("=== F6 VERIFICATION (drift re-emerge -> linked child run) ===\n")
    if not os.environ.get("HARBOR_SERVICENOW_LIVE"):
        print("! HARBOR_SERVICENOW_LIVE unset; CR + journal calls dry-run.\n")
    if not os.environ.get("POSTGRES_DSN"):
        print("! POSTGRES_DSN unset -- cannot persist parent->child link; FAIL")
        return 1
    os.environ.setdefault("CVE_REM_VERIFY_PROBE", "cargonet")
    os.environ["CVE_REM_SKIP_SEED"] = "1"

    # Stage 1: full pipeline -> patched.
    print("--- Stage 1: drive pipeline to verify_outcome=patched ---")
    state = await _drive(DEFAULT_CVE)
    parent_run_id = state.run_id
    pkg = state.osv_package_name or state.matched_candidate_product
    channel = state.install_channel
    fix = state.fixed_version
    hosts = list(state.affected_host_names or [])
    print(f"  parent_run_id : {parent_run_id}")
    print(f"  package       : {pkg!r} (channel={channel!r})")
    print(f"  fix_version   : {fix!r}")
    print(f"  hosts         : {hosts}")
    print(f"  verify_outcome: {state.verify_outcome!r}")
    if state.verify_outcome != "patched":
        print("  ! parent run did not reach verify_outcome=patched; "
              "F6 cannot proceed honestly")
        return 1
    if not hosts:
        print("  ! no affected hosts on state; cannot kill the patch")
        return 1

    # Stage 2: kill the patch on exactly one host.
    target = hosts[0]
    print(f"\n--- Stage 2: kill the patch on host={target!r} ---")
    summary = await reset_cargonet_to_vulnerable(
        cve_id=DEFAULT_CVE, host_filter=[target], verbose=False,
    )
    target_row = next(
        (r for r in summary.get("nodes", [])
         if r.get("host") == target), {},
    )
    print(f"  seed package       : {summary.get('package')!r}")
    print(f"  seed vulnerable_ver: {summary.get('vulnerable_version')!r}")
    print(f"  target row         : {target_row}")
    if not target_row.get("ok"):
        print(f"  ! seed failed: {target_row.get('error', '?')}")
        overall = False

    # Stage 3: re-probe every host; targeted host should diverge.
    print("\n--- Stage 3: re-probe each host (drift detection) ---")
    drift_hits: list[dict[str, str]] = []
    for h in hosts:
        observed = await _probe_host(h, pkg, channel)
        diverged = bool(observed) and observed != fix
        marker = "DRIFT" if diverged else "ok"
        print(f"  {h:24} observed={observed!r:14} expected={fix!r:10} -> {marker}")
        if diverged:
            drift_hits.append({"host": h, "observed": observed})
    if not drift_hits:
        print("  ! no drift detected on any host; "
              "either seed missed or the kill was no-op")
        overall = False
    if any(h["host"] != target for h in drift_hits):
        print("  ! drift detected on a host we did not seed (unexpected)")
        overall = False

    # Stage 4: spawn drift_watch child run via DriftWatchSpawnNode.
    print("\n--- Stage 4: spawn drift_watch child run ---")
    spawn_state = state.model_copy(update={"run_id": parent_run_id})
    spawn_ctx = SimpleNamespace(run_id=parent_run_id)
    delta = await DriftWatchSpawnNode().execute(spawn_state, spawn_ctx)
    if delta:
        spawn_state = spawn_state.model_copy(update=delta)
    child_run_id = str(spawn_state.drift_child_run_id or "")
    spawn_path = str(spawn_state.drift_spawn_path or "")
    print(f"  child_run_id : {child_run_id!r}")
    print(f"  spawn_path   : {spawn_path!r}")
    if not child_run_id:
        print("  ! drift child not spawned; parent->child link broken")
        overall = False
    if spawn_path not in ("scheduler", "http", "intent-only"):
        print(f"  ! unexpected drift_spawn_path: {spawn_path!r}")
        overall = False

    # Stage 5: persist parent->child link in PG + read back.
    print("\n--- Stage 5: persist + read parent->child link ---")
    err = ""
    if drift_hits and child_run_id:
        err = await _persist_drift_link(
            parent_run_id=parent_run_id,
            child_run_id=child_run_id,
            cve_id=DEFAULT_CVE,
            host=drift_hits[0]["host"],
            observed=drift_hits[0]["observed"],
            fix=fix,
        )
    if err:
        print(f"  ! persist failed: {err}")
        overall = False
    row = await _fetch_drift_link(parent_run_id, child_run_id)
    print(f"  PG row: {dict(row) if row else '<none>'}")
    if not row:
        print("  ! parent->child link not in cve_rem_drift_links")
        overall = False
    else:
        if row.get("parent_run_id") != parent_run_id:
            print("  ! row parent_run_id mismatch")
            overall = False
        if row.get("child_run_id") != child_run_id:
            print("  ! row child_run_id mismatch")
            overall = False

    print()
    if overall:
        print("=== OVERALL: PASS ===")
    else:
        print("=== OVERALL: FAIL ===")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
