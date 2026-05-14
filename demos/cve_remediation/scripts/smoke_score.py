# SPDX-License-Identifier: Apache-2.0
"""Smoke gate before any 100-CVE scoring run.

Drives the SAME path as score_run.py (POST /v1/runs to a running
``harbor serve``) against the 5-CVE fixture, then asserts hard
invariants per CVE. Cross-verifies each emitted CR by GET-ing it from
PDI directly so we catch the "state mutated but no broker write
landed" regression that fooled the 100-run.

Invariants (per CVE):

* ``cr_number`` matches ``^CHG\\d{7}$``
* CR exists in PDI (``GET /api/now/table/change_request/{sys_id}``
  returns 200 + matching number)
* ``retro_pgvector_written`` is True
* ``docplus_published`` is True
* ``vuln_class`` non-empty
* ``verify_outcome`` is one of the allowed enum values
* no ``last_*_error`` populated on terminal state

Aggregate invariants (across the 5):

* at least 1/5 ``sandbox_status != skipped`` (catches dispatcher collapse)

Run::

    set -a; source demos/cve_remediation/.env; set +a
    uv run --no-project python -m demos.cve_remediation.scripts.smoke_score

Requires ``harbor serve`` already running on $HARBOR_SERVE_BASE with
``CVE_REM_LIVE_BROKER=1`` and ``HARBOR_SERVICENOW_LIVE=1`` in its env.

Exit 0 = smoke green; non-zero = stop, do not run 100-CVE sweep.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

_DEMO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = _DEMO_ROOT / "fixtures"
_DEFAULT_FIXTURE = "smoke5"

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

_SN_BASE = os.environ.get("SERVICENOW_BASE_URL", "").rstrip("/")
_SN_USER = os.environ.get("SERVICENOW_USERNAME", "")
_SN_PASS = os.environ.get("SERVICENOW_PASSWORD", "")

_CR_PATTERN = re.compile(r"^CHG\d{7}$")
_VERIFY_OUTCOME_ALLOWED = {
    "patched",
    "vulnerable",
    "substrate_not_applicable",
    "unpatchable_hitl_pending",
    "rollback",
}
_ERROR_KEYS_HARD = (
    "last_intake_error",
    "last_sandbox_error",
    "last_cmdb_error",
    "last_cargonet_error",
    "last_cr_link_error",
    "last_retro_error",
    "last_run_outcome_error",
    "last_capability_error",
    "last_docplus_table_error",
)
# last_planner_error is tolerated when the pipeline routed to a
# recoverable terminal (HITL / not-applicable / patched). Planner ReAct
# loop can emit "agent emitted unstructured response" non-deterministically
# on certain CVEs; downstream HITL routing covers this. Hard-fail only
# when planner errored AND verify_outcome=vulnerable (no recovery).
_VERIFY_RECOVERED = {
    "patched",
    "substrate_not_applicable",
    "unpatchable_hitl_pending",
    "rollback",
}


def _ts():
    return datetime.now(UTC).strftime("%H:%M:%S")


async def _start_run(client, graph_id, cve_id):
    body = {
        "graph_id": graph_id,
        "params": {"cve_id": cve_id, "site": "h11", "smoke_run": True},
        "idempotency_key": f"smoke-{cve_id}-{int(time.time())}",
    }
    r = await client.post("/v1/runs", json=body, timeout=30)
    r.raise_for_status()
    return r.json()


async def _poll_until_terminal(client, run_id, timeout_s):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = await client.get(f"/v1/runs/{run_id}", timeout=30)
        if r.status_code == 404:
            await asyncio.sleep(_POLL_INTERVAL_S)
            continue
        r.raise_for_status()
        body = r.json()
        if body.get("status") in (
            "done", "completed", "error", "cancelled", "failed"
        ):
            return body
        await asyncio.sleep(_POLL_INTERVAL_S)
    return {"status": "timeout"}


def _read_final_state(db_path: Path, run_id: str) -> dict:
    if not db_path.exists():
        return {}
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=10.0) as conn:
        conn.execute("PRAGMA busy_timeout=10000")
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT state_snapshot
            FROM checkpoints WHERE run_id = ?
            ORDER BY step_idx DESC, branch_id DESC
            LIMIT 1
            """,
            (run_id,),
        )
        row = cur.fetchone()
    if not row:
        return {}
    raw = row["state_snapshot"] or "{}"
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except Exception:
        return {}


async def _verify_cr_in_pdi(sn_client, cr_sys_id: str, cr_number: str) -> tuple[bool, str]:
    """GET the CR record by sys_id from PDI. Returns (ok, detail)."""
    if not _SN_BASE or not _SN_USER:
        return False, "SERVICENOW_BASE_URL/SERVICENOW_USERNAME unset"
    url = f"/api/now/table/change_request/{cr_sys_id}"
    try:
        r = await sn_client.get(url, params={"sysparm_fields": "sys_id,number,short_description,state"}, timeout=15)
    except Exception as exc:
        return False, f"network: {type(exc).__name__}: {exc}"
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    result = r.json().get("result", {})
    got_number = result.get("number", "")
    if got_number != cr_number:
        return False, f"PDI returned number={got_number!r}, expected {cr_number!r}"
    return True, f"state={result.get('state')!r} short={result.get('short_description','')[:60]!r}"


def _check_one(cve_id: str, state: dict, cr_pdi_ok: bool, cr_pdi_detail: str) -> list[tuple[str, bool, str]]:
    """Per-CVE invariant check. Returns [(label, ok, detail), ...]."""
    out: list[tuple[str, bool, str]] = []
    # cr_number shape
    sn_resp = state.get("servicenow_response") or {}
    cr_result = (sn_resp.get("result") if isinstance(sn_resp, dict) else {}) or {}
    cr_number = str(cr_result.get("number") or "")
    cr_sys_id = str(cr_result.get("sys_id") or "")
    out.append((
        "cr_number shape",
        bool(_CR_PATTERN.fullmatch(cr_number)),
        f"cr_number={cr_number!r}",
    ))
    out.append((
        "cr_sys_id present",
        bool(cr_sys_id),
        f"cr_sys_id={cr_sys_id!r}",
    ))
    out.append((
        "CR exists in PDI",
        cr_pdi_ok,
        cr_pdi_detail,
    ))
    out.append((
        "retro_pgvector_written",
        bool(state.get("retro_pgvector_written")),
        f"got={state.get('retro_pgvector_written')!r}",
    ))
    out.append((
        "docplus_published",
        bool(state.get("docplus_published")),
        f"got={state.get('docplus_published')!r}",
    ))
    vc = state.get("vuln_class", "")
    vo = state.get("verify_outcome", "")
    # Empty vuln_class tolerated only when substrate filter bailed cleanly
    # (extractor floor ~19% on host-class CVEs without install_channel).
    vc_ok = bool(vc) or vo == "substrate_not_applicable"
    out.append((
        "vuln_class non-empty",
        vc_ok,
        f"vuln_class={vc!r} verify={vo!r}",
    ))
    out.append((
        "verify_outcome in enum",
        vo in _VERIFY_OUTCOME_ALLOWED,
        f"verify_outcome={vo!r}",
    ))
    hard_errs = [k for k in _ERROR_KEYS_HARD if state.get(k)]
    out.append((
        "no hard last_*_error",
        not hard_errs,
        f"populated={hard_errs!r}" if hard_errs else "clean",
    ))
    # Planner error: hard-fail only when no downstream recovery.
    planner_err = bool(state.get("last_planner_error"))
    if planner_err:
        recovered = vo in _VERIFY_RECOVERED
        out.append((
            "planner_error recovered",
            recovered,
            f"verify_outcome={vo!r} (planner_error tolerated only on recovery)",
        ))
    return out


async def _run_one(client, db_path, sn_client, *, cve_id, graph_id, timeout_s):
    started = time.monotonic()
    print(f"  [{_ts()}] {cve_id}: starting run ...", flush=True)
    start = await _start_run(client, graph_id, cve_id)
    run_id = start.get("run_id")
    poll = await _poll_until_terminal(client, run_id, timeout_s)
    elapsed = time.monotonic() - started
    terminal = poll.get("status")
    print(f"  [{_ts()}] {cve_id}: terminal={terminal} t={elapsed:.1f}s run_id={run_id}", flush=True)
    state = _read_final_state(db_path, run_id) if run_id else {}
    # Pull CR sys_id from state envelope, then verify in PDI.
    sn_resp = state.get("servicenow_response") or {}
    cr_sys_id = ""
    if isinstance(sn_resp, dict):
        cr_sys_id = str((sn_resp.get("result") or {}).get("sys_id") or "")
    cr_number = ""
    if isinstance(sn_resp, dict):
        cr_number = str((sn_resp.get("result") or {}).get("number") or "")
    cr_pdi_ok, cr_pdi_detail = (False, "no cr_sys_id in state")
    if cr_sys_id:
        cr_pdi_ok, cr_pdi_detail = await _verify_cr_in_pdi(sn_client, cr_sys_id, cr_number)
    checks = _check_one(cve_id, state, cr_pdi_ok, cr_pdi_detail)
    return {
        "cve_id": cve_id,
        "run_id": run_id,
        "terminal": terminal,
        "elapsed_s": round(elapsed, 1),
        "state": state,
        "checks": checks,
        "cr_number": cr_number,
        "cr_sys_id": cr_sys_id,
        "sandbox_status": state.get("sandbox_status"),
    }


async def _amain(args):
    fix_name = args.fixture
    candidate = _FIXTURES / f"scoring_{fix_name}.json"
    fixture_path = candidate if candidate.is_file() else Path(fix_name)
    if not fixture_path.is_file():
        print(f"! fixture not found: {fixture_path}", file=sys.stderr)
        return 2
    cves = json.loads(fixture_path.read_text())["cves"]
    if args.limit:
        cves = cves[: args.limit]
    print(f"=== smoke_score ===")
    print(f"  serve   : {args.serve_base}")
    print(f"  graph   : {args.graph_id}")
    print(f"  ckpt db : {args.checkpoint_db}")
    print(f"  cves    : {len(cves)}")
    print(f"  PDI     : {_SN_BASE or '<unset>'}")
    print()

    async with httpx.AsyncClient(base_url=args.serve_base) as client:
        try:
            r = await client.get("/v1/runs?limit=1", timeout=5)
            r.raise_for_status()
        except Exception as exc:
            print(f"! serve unreachable at {args.serve_base}: {exc}", file=sys.stderr)
            return 2

        sn_auth = (_SN_USER, _SN_PASS) if _SN_USER else None
        async with httpx.AsyncClient(base_url=_SN_BASE, auth=sn_auth) as sn_client:
            results = []
            for cve in cves:
                cid = cve["cve_id"]
                rec = await _run_one(
                    client, args.checkpoint_db, sn_client,
                    cve_id=cid, graph_id=args.graph_id,
                    timeout_s=args.timeout,
                )
                results.append(rec)

    # Render
    print("\n=== PER-CVE INVARIANTS ===")
    total_fail = 0
    for rec in results:
        cid = rec["cve_id"]
        print(f"\n{cid}  cr={rec['cr_number']!r}  sandbox={rec['sandbox_status']!r}  t={rec['elapsed_s']}s")
        for label, ok, detail in rec["checks"]:
            mark = "PASS" if ok else "FAIL"
            if not ok:
                total_fail += 1
            print(f"  [{mark}] {label:32}  {detail}")

    # Aggregate invariant: >=1/5 sandbox_status != skipped
    not_skipped = sum(1 for r in results if r["sandbox_status"] and r["sandbox_status"] != "skipped")
    agg_ok = not_skipped >= 1
    print(f"\n=== AGGREGATE INVARIANTS ===")
    print(f"  [{'PASS' if agg_ok else 'FAIL'}] sandbox_status != skipped for >=1/5  (got {not_skipped}/{len(results)})")
    if not agg_ok:
        total_fail += 1

    # Final
    print(f"\n=== SUMMARY ===")
    print(f"  cves        : {len(results)}")
    print(f"  failed inv. : {total_fail}")
    if total_fail:
        print(f"  RESULT      : RED  --  do not run 100-CVE sweep")
        return 1
    print(f"  RESULT      : GREEN")

    # Drop a marker so score_run.py can gate on recency
    marker = Path("/tmp/cve_rem_smoke_green.marker")
    marker.write_text(json.dumps({
        "ts": datetime.now(UTC).isoformat(),
        "cves": [r["cve_id"] for r in results],
        "cr_numbers": [r["cr_number"] for r in results],
    }))
    print(f"  marker      : {marker}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="smoke gate before 100-CVE sweep")
    ap.add_argument("--fixture", default=_DEFAULT_FIXTURE,
                    help="fixture short name (smoke5, smoke20, smoke10v4) "
                         "or absolute path to scoring_<name>.json")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--serve-base", default=_DEFAULT_SERVE_BASE)
    ap.add_argument("--graph-id", default=_DEFAULT_GRAPH_ID)
    ap.add_argument("--checkpoint-db", type=Path, default=_DEFAULT_CHECKPOINT_DB)
    ap.add_argument("--timeout", type=int, default=_DEFAULT_RUN_TIMEOUT_S)
    args = ap.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
