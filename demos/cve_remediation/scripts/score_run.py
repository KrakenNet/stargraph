# SPDX-License-Identifier: Apache-2.0
"""Run-driver: POST 100 cve_remediation runs to a live ``harbor serve`` and
capture per-run telemetry from its sqlite checkpoint store.

Pre-requisites the operator launches once before this script:

1. ``harbor serve --port 9001 --db /tmp/score-checkpoints.sqlite \
        --graph demos/cve_remediation/graph/harbor.yaml \
        --allow-side-effects``
2. h11 topology deployed + 25 vuln recipes installed
   (``deploy_h11.py`` + ``install_h11_vulns.py``)
3. CMDB seeded (``seed_h11_cmdb.py``)

For each CVE in ``fixtures/scoring_cves_v1.json``:

* POST ``/v1/runs`` with ``graph_id="graph:cve-rem-pipeline"`` and
  ``params={"cve_id": <id>, "site": "h11"}``.
* Poll ``GET /v1/runs/{run_id}`` until terminal status (``done``,
  ``error``, ``cancelled``, ``failed``).
* Open the sqlite checkpoint DB; pull all rows for the run_id;
  aggregate per-node (steps, duration, last state delta) and
  pull the final state_snapshot.
* Emit one JSONL record per CVE to
  ``$HARBOR_ARTIFACTS_ROOT/scorecard/run_<ts>.jsonl``.

Sequential by default (cve-rem nodes write through shared SN/PG/CargoNet
state; parallel runs would race on the same h11 hosts).

Run::

    set -a; source demos/cve_remediation/.env; set +a
    uv run --no-project python -m demos.cve_remediation.scripts.score_run \\
        --serve-base http://127.0.0.1:9001 \\
        --checkpoint-db /tmp/score-checkpoints.sqlite \\
        --limit 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

_DEMO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = _DEMO_ROOT / "fixtures"
_CVES_PATH = _FIXTURES / "scoring_cves_v1.json"
_ARTIFACTS_ROOT = Path(
    os.environ.get("HARBOR_ARTIFACTS_ROOT", ".harbor/artifacts")
)
_OUT_DIR = _ARTIFACTS_ROOT / "scorecard"

_DEFAULT_SERVE_BASE = os.environ.get(
    "HARBOR_SERVE_BASE", "http://127.0.0.1:9001"
)
_DEFAULT_GRAPH_ID = os.environ.get(
    "CVE_REM_GRAPH_ID", "graph:cve-rem-pipeline"
)
_DEFAULT_CHECKPOINT_DB = Path(
    os.environ.get("CVE_REM_CHECKPOINT_DB", "/tmp/score-checkpoints.sqlite")
)
_DEFAULT_RUN_TIMEOUT_S = int(os.environ.get("CVE_REM_RUN_TIMEOUT_S", "600"))
_POLL_INTERVAL_S = 3.0


async def _start_run(client, graph_id, cve_id):
    body = {
        "graph_id": graph_id,
        "params": {"cve_id": cve_id, "site": "h11", "scoring_run": True},
        "idempotency_key": f"score-{cve_id}-{int(time.time())}",
    }
    r = await client.post("/v1/runs", json=body, timeout=30)
    r.raise_for_status()
    return r.json()


async def _poll_until_terminal(client, run_id, timeout_s):
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        r = await client.get(f"/v1/runs/{run_id}", timeout=30)
        if r.status_code == 404:
            await asyncio.sleep(_POLL_INTERVAL_S)
            continue
        r.raise_for_status()
        body = r.json()
        last = body
        status = body.get("status", "")
        if status in ("done", "error", "cancelled", "failed", "completed"):
            return body
        await asyncio.sleep(_POLL_INTERVAL_S)
    return {"status": "timeout", "last_observed": last}


def _fetch_checkpoints(db_path: Path, run_id: str):
    """Read checkpoint rows for ``run_id``.

    The DB is concurrently written by ``harbor serve`` (WAL mode);
    short-lived I/O errors during a WAL checkpoint roll-forward are
    retried with backoff. Connection opens in read-only URI mode +
    a generous busy timeout so the reader doesn't trip the writer's
    lock window.
    """
    if not db_path.exists():
        return []
    last_err: Exception | None = None
    for attempt in range(5):
        try:
            uri = f"file:{db_path}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=10.0) as conn:
                conn.execute("PRAGMA busy_timeout=10000")
                conn.row_factory = sqlite3.Row
                cur = conn.execute(
                    """
                    SELECT step_idx, branch_id, ts, last_node,
                           state_snapshot
                    FROM checkpoints WHERE run_id = ?
                    ORDER BY step_idx, branch_id
                    """,
                    (run_id,),
                )
                return [dict(row) for row in cur.fetchall()]
        except sqlite3.OperationalError as exc:
            last_err = exc
            time.sleep(0.5 * (attempt + 1))
    print(
        f"! _fetch_checkpoints exhausted retries for {run_id}: {last_err}",
        file=sys.stderr,
    )
    return []


def _per_node_summary(checkpoints):
    by_node = defaultdict(lambda: {"steps": 0, "first_ts": None, "last_ts": None})
    for c in checkpoints:
        b = by_node[c["last_node"]]
        b["steps"] += 1
        if b["first_ts"] is None or c["ts"] < b["first_ts"]:
            b["first_ts"] = c["ts"]
        if b["last_ts"] is None or c["ts"] > b["last_ts"]:
            b["last_ts"] = c["ts"]
    out = {}
    for node, b in by_node.items():
        try:
            f = datetime.fromisoformat(b["first_ts"])
            l = datetime.fromisoformat(b["last_ts"])
            dur = (l - f).total_seconds()
        except Exception:
            dur = 0.0
        out[node] = {
            "steps": b["steps"],
            "duration_s": round(dur, 3),
            "first_ts": b["first_ts"],
            "last_ts": b["last_ts"],
        }
    return out


def _final_state_outcome(checkpoints):
    if not checkpoints:
        return {}
    final = checkpoints[-1]
    raw = final.get("state_snapshot") or "{}"
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    state = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(state, dict):
        state = {}
    keys_of_interest = (
        # classification
        "cve_id", "vuln_class", "ssvc_tier", "source_class",
        "source_trust_violation", "cwe_class",
        # CMDB correlation (workflow's predictions vs ground truth)
        "cmdb_software_sys_id", "cmdb_software_name",
        "matched_candidate_product", "candidate_products",
        "cmdb_match_correct", "cmdb_query_count", "affected_host_names",
        "task_ci_link_count",
        # Correlation hardening (2026-05-08)
        "cmdb_match_score", "cmdb_match_quality",
        # Topology correlation
        "cargonet_correlation_map", "cargonet_node_count",
        "cargonet_lab_ref", "cargonet_writeback_done",
        # Sandbox + verification
        "sandbox_runtime", "sandbox_status", "sandbox_quarantined",
        "sandbox_quarantine_reason", "sandbox_prod_divergence",
        "sandbox_probe_steps", "sandbox_probe_latency_ms",
        "verify_outcome", "verify_probe_method", "fleet_passed",
        "per_host_apply_results", "per_host_verify_results",
        # Planner
        "plan_quality_score_bp", "plan_rationale", "plan_hash",
        "planner_verifier_passed", "planner_citation_findings",
        "planner_latency_ms",
        # CR lifecycle
        "cr_status", "cr_lifecycle_states", "cr_self_validation_passed",
        "change_task_count",
        # Retros + outcomes
        "retro_id", "retro_outcome", "retro_pgvector_written",
        "retro_suggestion_count", "prior_retro_count",
        # Gates + ops
        "plan_quarantined", "halt_new_active", "halt_reason",
        "rollback_triggered", "run_outcome_written",
        "drift_child_run_id", "shamir_quorum", "oncall_paged",
        # Advisory + version
        "vulnerability_status", "fixed_version", "exact_affected_versions",
        "affected_version_ranges",
        "install_channel", "osv_package_name",
        # Phase E (2026-05-11): version-range gate observability.
        "cmdb_ci_version", "cmdb_version_gate_status",
        # RemediationDiscoveryNode output
        "recommended_actions", "recommendation_provenance",
        # Doc+ table integration (CR, collection, doc, m2m)
        "docplus_published", "docplus_attachment_sys_id",
        "docplus_collection_sys_id", "docplus_doc_sys_id",
        "docplus_doc_attachment_sys_id", "docplus_m2m_sys_id",
        "last_docplus_table_error",
        # Retro failure analysis (cross-run learning)
        "retro_failure_signals", "retro_failure_analysis",
        "retro_prevention_suggestions", "retro_analysis_error",
        # Retro round #A-#E (mitigation_only path + retry gate)
        "mitigation_only", "verify_vulnerable_attempts",
        "static_detection_per_host", "sandbox_retry_attempts",
        # Tier 1 (2026-05-08)
        "mitigation_probe_passed", "mitigation_probe_issues",
        "planner_schema_retries",
        # LM-bundle wiring acceptance test surfaces (2026-05-08)
        "bundle", "code_runtime",
        # Phase F (2026-05-11): structured 4-tuple plan + critic deficits.
        "plan_spec", "plan_spec_deficits", "critic_deficits",
        # Phase F+ (2026-05-11): substrate guard audit.
        "cve_vendor", "cve_product", "substrate_filter",
        "disposition",
        # Doctrine fallback (2026-05-14): FrameworkMappingNode output.
        "framework_controls", "attack_patterns",
        "framework_mapping_status", "last_framework_mapping_error",
        # Errors (any non-empty key signals a failed step)
        "last_intake_error", "last_planner_error", "last_sandbox_error",
        "last_cmdb_error", "last_cargonet_error", "last_cr_link_error",
        "last_retro_error", "last_run_outcome_error",
        "last_capability_error",
    )
    outcome = {k: state[k] for k in keys_of_interest if k in state}
    # Surface CR sys_id / number from the nested servicenow_response
    # envelope so the JSONL row carries them at top-level (matching
    # what the verify_step7_cr.py human path expects).
    sn_resp = state.get("servicenow_response") or {}
    if isinstance(sn_resp, dict):
        result = sn_resp.get("result") or {}
        if isinstance(result, dict):
            cr_sys_id = str(result.get("sys_id", "") or "")
            cr_number = str(result.get("number", "") or "")
            if cr_sys_id:
                outcome["cr_sys_id"] = cr_sys_id
            if cr_number:
                outcome["cr_number"] = cr_number
    outcome["state_keys"] = sorted(state.keys()) if isinstance(state, dict) else []
    outcome["state_size_bytes"] = len(json.dumps(state, default=str))
    return outcome


async def _run_one(client, db_path, *, cve_id, graph_id, timeout_s):
    started = time.monotonic()
    try:
        start = await _start_run(client, graph_id, cve_id)
    except Exception as exc:
        return {
            "cve_id": cve_id, "phase": "start", "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_s": round(time.monotonic() - started, 2),
        }
    run_id = start.get("run_id")
    poll = await _poll_until_terminal(client, run_id, timeout_s)
    elapsed = round(time.monotonic() - started, 2)
    checkpoints = _fetch_checkpoints(db_path, run_id) if run_id else []
    return {
        "cve_id": cve_id, "run_id": run_id,
        "terminal_status": poll.get("status"),
        "ok": poll.get("status") in ("done", "completed"),
        "elapsed_s": elapsed,
        "checkpoint_count": len(checkpoints),
        "per_node": _per_node_summary(checkpoints),
        "outcome": _final_state_outcome(checkpoints),
        "started_at": datetime.now(UTC).isoformat(),
    }


async def _amain(args):
    if args.fixture:
        candidate = _FIXTURES / f"scoring_{args.fixture}.json"
        fixture_path = candidate if candidate.is_file() else Path(args.fixture)
    else:
        fixture_path = _CVES_PATH
    if not fixture_path.is_file():
        print(f"! fixture not found: {fixture_path}", file=sys.stderr)
        return 2
    cves = json.loads(fixture_path.read_text())["cves"]
    if args.cve:
        cves = [c for c in cves if c["cve_id"] == args.cve]
    if args.bucket:
        cves = [c for c in cves if c["vuln_class"] == args.bucket]
    if args.limit:
        cves = cves[: args.limit]
    print(f"=== score_run ===")
    print(f"  serve   : {args.serve_base}")
    print(f"  graph   : {args.graph_id}")
    print(f"  ckpt db : {args.checkpoint_db}")
    print(f"  cves    : {len(cves)}")
    print(f"  timeout : {args.timeout}s per run")

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = _OUT_DIR / f"run_{ts}.jsonl"
    print(f"  output  : {out_path}\n")

    async with httpx.AsyncClient(base_url=args.serve_base) as client:
        try:
            r = await client.get("/v1/runs?limit=1", timeout=5)
            r.raise_for_status()
        except Exception as exc:
            print(f"! cannot reach harbor serve at {args.serve_base}: {exc}",
                  file=sys.stderr)
            return 2

        with out_path.open("w") as fh:
            for i, cve in enumerate(cves, 1):
                cid = cve["cve_id"]
                print(f"  [{i:3d}/{len(cves)}] {cid:18} ", end="", flush=True)
                rec = await _run_one(
                    client, args.checkpoint_db,
                    cve_id=cid, graph_id=args.graph_id,
                    timeout_s=args.timeout,
                )
                rec["vuln_class"] = cve["vuln_class"]
                rec["vendor"] = cve["vendor"]
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                flag = "OK  " if rec["ok"] else f"{rec.get('terminal_status','??')[:4]:4}"
                nodes = len(rec.get("per_node", {}))
                print(f"{flag} t={rec['elapsed_s']:6.1f}s nodes={nodes:3d} ckpts={rec.get('checkpoint_count', 0):3d}")

    print(f"\n=== done ===")
    print(f"  artifact: {out_path}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="drive 100 cve_remediation runs")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap number of runs (0=all)")
    ap.add_argument(
        "--fixture",
        help=(
            "fixture name (e.g. 'smoke5') resolving to "
            "fixtures/scoring_<name>.json, OR an absolute/relative path. "
            "Default: fixtures/scoring_cves_v1.json (100 CVE sweep)."
        ),
    )
    ap.add_argument("--cve", help="run only this CVE (smoke test)")
    ap.add_argument("--bucket", help="filter cves by vuln_class field")
    ap.add_argument("--serve-base", default=_DEFAULT_SERVE_BASE)
    ap.add_argument("--graph-id", default=_DEFAULT_GRAPH_ID)
    ap.add_argument("--checkpoint-db", type=Path,
                    default=_DEFAULT_CHECKPOINT_DB)
    ap.add_argument("--timeout", type=int, default=_DEFAULT_RUN_TIMEOUT_S)
    args = ap.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
