# SPDX-License-Identifier: Apache-2.0
"""Drive 100-CVE scoring sweep + generate comprehensive analytics report.

Submits all CVEs from scoring_cves_v2.json to a running harbor serve,
captures checkpoint telemetry, and generates a markdown report with:

- Per-CVE timing, outcome, and correlation quality
- Aggregate statistics (mean/median/p95, pass rates)
- CMDB + CargoNet correlation accuracy
- Sandbox/verification breakdown
- Per-node execution profile
- Error catalog
- Full JSONL audit trail

Run::

    set -a; source demos/cve_remediation/.env; set +a
    uv run --no-project python -m demos.cve_remediation.scripts.score_100 \
        --limit 5        # smoke: first 5 CVEs
    uv run --no-project python -m demos.cve_remediation.scripts.score_100
        # full 100-CVE sweep
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

_DEMO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = _DEMO_ROOT / "fixtures"
_CVES_PATH = _FIXTURES / "scoring_cves_v2.json"

_DEFAULT_SERVE_BASE = os.environ.get(
    "HARBOR_SERVE_BASE", "http://127.0.0.1:9001"
)
_DEFAULT_GRAPH_ID = os.environ.get(
    "CVE_REM_GRAPH_ID", "graph:cve-rem-pipeline"
)
_DEFAULT_CHECKPOINT_DB = Path(
    os.environ.get("CVE_REM_CHECKPOINT_DB", "/tmp/score-checkpoints.sqlite")
)
_DEFAULT_RUN_TIMEOUT_S = int(os.environ.get("CVE_REM_RUN_TIMEOUT_S", "900"))
_POLL_INTERVAL_S = 3.0

_ARTIFACTS_ROOT = Path(
    os.environ.get("HARBOR_ARTIFACTS_ROOT", ".harbor/artifacts")
)
_DEFAULT_AUDIT_LOG = Path(
    os.environ.get("HARBOR_AUDIT_LOG", "/tmp/harbor-audit.jsonl")
)

# ── State keys to extract ──────────────────────────────────────────
_STATE_KEYS = [
    # identity
    "cve_id", "vuln_class", "cwe_class", "ssvc_tier", "disposition",
    "source_trust", "source_class", "injection_class",
    # CMDB
    "cmdb_software_name", "cmdb_software_sys_id", "cmdb_match_score",
    "cmdb_match_quality", "cmdb_query_count", "matched_candidate_product",
    "affected_host_names", "substrate_filter",
    # CargoNet
    "cargonet_node_count", "cargonet_lab_ref", "cargonet_correlation_map",
    # remediation discovery
    "fixed_version", "install_channel", "osv_package_name",
    "vulnerability_status", "recommendation_provenance",
    # planner
    "plan_hash", "plan_quality_score_bp", "planner_latency_ms",
    "planner_verifier_passed", "planner_schema_retries",
    "critic_verdict", "critic_attempt", "code_runtime",
    # sandbox
    "sandbox_runtime", "sandbox_status", "sandbox_quarantined",
    "sandbox_prod_divergence", "sandbox_probe_latency_ms",
    "sandbox_probe_steps", "skip_sandbox",
    # verification
    "verify_outcome", "verify_probe_method",
    "fleet_passed", "canary_passed", "stage_passed",
    "per_host_apply_results", "per_host_verify_results",
    "mitigation_only", "rollback_triggered",
    # CR
    "cr_correlation_id", "cr_status", "cr_lifecycle_states",
    "attachment_count", "cr_self_validation_passed",
    "cr_observed_journal_count", "cr_service_lookup_status",
    # retro
    "retro_id", "retro_outcome", "retro_pg_written", "retro_redis_written",
    "retro_pgvector_written", "retro_suggestion_count",
    "prior_retro_count", "prior_retro_outcomes",
    "prior_retro_retrieval_status", "prior_retro_retrieval_mode",
    "retro_failure_analysis",
    # KG
    "graph_prior_actions", "graph_prior_retrieval_status",
    "kg_run_written", "kg_run_nodes_written", "kg_run_edges_written",
    # doctrine/framework
    "framework_controls", "attack_patterns", "framework_mapping_status",
    # docplus
    "docplus_published", "docplus_staging_ref",
    # attestation
    "run_attestation_jws",
    # drift
    "drift_watch_window_hours", "drift_child_run_id",
    # halts/gates
    "halt_new_active", "halt_reason", "hitl_blocked_at",
    # errors (all last_*_error fields)
    "last_intake_error", "last_planner_error", "last_sandbox_error",
    "last_cmdb_error", "last_cargonet_error", "last_cr_link_error",
    "last_cr_lifecycle_error", "last_retro_error",
    "last_run_outcome_error", "last_capability_error",
    "last_docplus_table_error", "last_docx_emit_error",
    "last_attachment_error", "last_proof_report_error",
    "last_graph_prior_error", "last_framework_mapping_error",
    "last_attestation_error", "last_drift_spawn_error",
    "last_cr_self_validation_error", "last_kg_run_error",
    "last_broker_intent",
    # execution
    "execution_ledger",
]


# ── Run driver ─────────────────────────────────────────────────────
async def _start_run(client, graph_id, cve_id):
    body = {
        "graph_id": graph_id,
        "params": {"cve_id": cve_id, "site": "h11", "scoring_run": True},
        "idempotency_key": f"score100-{cve_id}-{int(time.time())}",
    }
    r = await client.post("/v1/runs", json=body, timeout=30)
    r.raise_for_status()
    return r.json()


_HITL_AUTO_APPROVE_DELAY_S = 20.0


async def _poll_until_terminal(client, run_id, timeout_s, *, max_approvals: int = 8):
    """Poll the run, auto-approving every HITL ``awaiting-input`` boundary
    after a 20-second hold so a human watcher can interject manually if
    they're tailing the sweep.

    Score sweeps are unattended; the cve-rem graph's hitl_plan_review
    interrupt rule (r-hitl-plan-gate) halts the run until a response is
    posted to ``/v1/runs/{id}/respond``. Mirrors the pattern in
    ``demos/cve_remediation/live_test.py:_approve_hitl_loop`` with an
    added pre-approval delay window.
    """
    from datetime import UTC, datetime

    deadline = time.monotonic() + timeout_s
    last = None
    approvals = 0
    awaiting_since: float | None = None
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
        # ``serve.lifecycle`` maps GraphRun.state="awaiting-input" to the
        # API status="paused" (Phase 2 widening pending; see
        # ``src/harbor/serve/lifecycle.py:332``). Score sweeps never call
        # /pause, so "paused" here always means HITL wait.
        if status in ("awaiting-input", "paused") and approvals < max_approvals:
            now = time.monotonic()
            if awaiting_since is None:
                awaiting_since = now
            if now - awaiting_since < _HITL_AUTO_APPROVE_DELAY_S:
                await asyncio.sleep(_POLL_INTERVAL_S)
                continue
            body_resp = {
                "response": {
                    "decision": "approve",
                    "actor": "score-100",
                    "note": f"auto-approve #{approvals + 1}",
                    "at": datetime.now(UTC).isoformat(),
                }
            }
            resp = await client.post(
                f"/v1/runs/{run_id}/respond", json=body_resp, timeout=30
            )
            if resp.status_code not in (200, 202):
                return {
                    "status": "respond_error",
                    "respond_code": resp.status_code,
                    "respond_body": resp.text[:500],
                    "last_observed": last,
                }
            approvals += 1
            awaiting_since = None
        else:
            # Reset the hold window if the run moved off awaiting-input.
            awaiting_since = None
        await asyncio.sleep(_POLL_INTERVAL_S)
    return {"status": "timeout", "last_observed": last}


def _fetch_checkpoints(db_path: Path, run_id: str):
    if not db_path.exists():
        return []
    for attempt in range(5):
        try:
            uri = f"file:{db_path}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=10.0) as conn:
                conn.execute("PRAGMA busy_timeout=10000")
                conn.row_factory = sqlite3.Row
                cur = conn.execute(
                    "SELECT step_idx, branch_id, ts, last_node, "
                    "state_snapshot FROM checkpoints "
                    "WHERE run_id = ? ORDER BY step_idx, branch_id",
                    (run_id,),
                )
                return [dict(row) for row in cur.fetchall()]
        except sqlite3.OperationalError:
            time.sleep(0.5 * (attempt + 1))
    return []


def _parse_audit_events(audit_path: Path, run_id: str) -> dict:
    """Extract typed events from the JSONL audit log for a specific run.

    Harbor's audit sink (FR-22) writes one JSON record per EventBus
    event: tool_call, tool_result, transition, checkpoint, error,
    bosun_audit, artifact_written, etc. Each carries {type, run_id,
    step, ts, payload}.
    """
    if not audit_path.exists():
        return {"events": [], "summary": {}}
    events = []
    try:
        with open(audit_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Handle signed envelopes: {"event": ..., "sig": "..."}
                if "event" in record and "sig" in record:
                    record = record["event"]
                if record.get("run_id") != run_id:
                    continue
                events.append(record)
    except Exception:
        pass

    # Summarize by event type
    type_counts = Counter(ev.get("type", "unknown") for ev in events)

    # Extract tool call details
    tool_calls = []
    for ev in events:
        if ev.get("type") == "tool_call":
            p = ev.get("payload") or ev
            tool_calls.append({
                "step": ev.get("step"),
                "tool": p.get("tool_name") or p.get("name", "?"),
                "ts": ev.get("ts", ""),
            })

    # Extract tool results with token counts
    tool_results = []
    total_tokens = 0
    for ev in events:
        if ev.get("type") == "tool_result":
            p = ev.get("payload") or ev
            usage = p.get("usage") or p.get("result", {}).get("usage") or {}
            tokens = usage.get("total_tokens", 0)
            total_tokens += tokens
            tool_results.append({
                "step": ev.get("step"),
                "tool": p.get("tool_name") or p.get("name", "?"),
                "tokens": tokens,
            })

    # Extract transitions (rule firings)
    transitions = []
    for ev in events:
        if ev.get("type") == "transition":
            p = ev.get("payload") or ev
            transitions.append({
                "step": ev.get("step"),
                "from": p.get("from_node", "?"),
                "to": p.get("to_node", "?"),
                "rule": p.get("rule", ""),
            })

    # Extract errors
    errors = []
    for ev in events:
        if ev.get("type") == "error":
            p = ev.get("payload") or ev
            errors.append({
                "step": ev.get("step"),
                "node": p.get("node", "?"),
                "message": str(p.get("message", ""))[:200],
                "fatal": p.get("fatal", False),
            })

    # Extract bosun audit events (governance)
    bosun_events = []
    for ev in events:
        if ev.get("type") == "bosun_audit":
            p = ev.get("payload") or ev
            bosun_events.append({
                "step": ev.get("step"),
                "kind": p.get("kind", "?"),
                "pack": p.get("pack", "?"),
                "detail": str(p.get("detail", ""))[:150],
            })

    # Extract artifact writes
    artifacts = []
    for ev in events:
        if ev.get("type") == "artifact_written":
            p = ev.get("payload") or ev
            artifacts.append({
                "step": ev.get("step"),
                "ref": p.get("artifact_ref", "?"),
            })

    return {
        "event_count": len(events),
        "event_types": dict(type_counts),
        "tool_calls": tool_calls,
        "tool_call_count": len(tool_calls),
        "tool_results": tool_results,
        "total_tokens": total_tokens,
        "transitions": transitions,
        "transition_count": len(transitions),
        "errors": errors,
        "error_count": len(errors),
        "bosun_events": bosun_events,
        "bosun_event_count": len(bosun_events),
        "artifacts": artifacts,
        "artifact_count": len(artifacts),
    }


def _per_node_timing(checkpoints):
    by_node = defaultdict(
        lambda: {"steps": 0, "first_ts": None, "last_ts": None}
    )
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
        }
    return out


def _node_io_trace(checkpoints):
    """Build per-node input/output diffs from consecutive state snapshots.

    For each checkpoint, diffs the state against the previous one to
    determine what keys the node read (unchanged but relevant) vs what
    it wrote (new or changed values). This is the closest we get to
    node-level I/O audit without instrumenting every node internally.
    """
    trace = []
    prev_state: dict = {}
    for c in checkpoints:
        raw = c.get("state_snapshot") or "{}"
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        cur_state = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if not isinstance(cur_state, dict):
            cur_state = {}

        node = c.get("last_node", "?")
        step = c.get("step_idx", 0)
        ts = c.get("ts", "")

        # Compute what this node changed
        written = {}
        for k, v in cur_state.items():
            if k.startswith("__"):
                continue
            old = prev_state.get(k)
            if old != v:
                # Truncate large values for the trace
                sv = str(v)
                if len(sv) > 200:
                    sv = sv[:200] + "..."
                written[k] = sv

        if written:
            trace.append({
                "step": step,
                "node": node,
                "ts": ts,
                "keys_written": list(written.keys()),
                "writes": written,
            })

        prev_state = cur_state

    return trace


def _extract_state(checkpoints):
    if not checkpoints:
        return {}
    raw = checkpoints[-1].get("state_snapshot") or "{}"
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    state = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(state, dict):
        return {}
    return {k: state.get(k) for k in _STATE_KEYS if k in state}


async def _run_one(client, graph_id, cve_id, db_path, audit_path, timeout_s):
    t0 = time.monotonic()
    try:
        start = await _start_run(client, graph_id, cve_id)
    except Exception as exc:
        return {
            "cve_id": cve_id,
            "status": "submit_error",
            "error": str(exc),
            "wall_s": 0,
        }
    run_id = start.get("run_id", "")
    result = await _poll_until_terminal(client, run_id, timeout_s)
    wall_s = round(time.monotonic() - t0, 2)
    checkpoints = _fetch_checkpoints(db_path, run_id)
    state = _extract_state(checkpoints)
    node_timing = _per_node_timing(checkpoints)
    io_trace = _node_io_trace(checkpoints)
    audit = _parse_audit_events(audit_path, run_id)
    return {
        "cve_id": cve_id,
        "run_id": run_id,
        "status": result.get("status", "unknown"),
        "wall_s": wall_s,
        "checkpoint_count": len(checkpoints),
        "node_timing": node_timing,
        "node_io_trace": io_trace,
        "audit": audit,
        "state": state,
    }


# ── Report generation ──────────────────────────────────────────────
def _stat_line(values: list[float], label: str) -> str:
    if not values:
        return f"  {label}: no data"
    mn = min(values)
    mx = max(values)
    avg = statistics.mean(values)
    med = statistics.median(values)
    p95 = sorted(values)[int(len(values) * 0.95)] if len(values) > 1 else mx
    return (
        f"  {label}: min={mn:.1f}  mean={avg:.1f}  "
        f"median={med:.1f}  p95={p95:.1f}  max={mx:.1f}"
    )


def _generate_report(results: list[dict], out_dir: Path) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    n = len(results)
    lines: list[str] = []
    ap = lines.append

    ap(f"# CVE Remediation Pipeline — 100-CVE Scoring Report")
    ap(f"")
    ap(f"**Generated:** {ts}")
    ap(f"**CVEs scored:** {n}")
    ap("")

    # ── 1. Overall timing ──
    ap("## 1. Execution Timing")
    ap("")
    walls = [r["wall_s"] for r in results if r["wall_s"] > 0]
    ap(_stat_line(walls, "Wall-clock (s)"))
    total_wall = sum(walls)
    ap(f"  Total sweep time: {total_wall:.0f}s ({total_wall/60:.1f}min)")
    ap("")

    # Longest / shortest
    by_wall = sorted(results, key=lambda r: r["wall_s"], reverse=True)
    ap("**Longest 5 runs:**")
    ap("")
    ap("| CVE | Wall (s) | Outcome | CMDB quality |")
    ap("|-----|----------|---------|--------------|")
    for r in by_wall[:5]:
        s = r.get("state", {})
        ap(f"| {r['cve_id']} | {r['wall_s']:.1f} | "
           f"{s.get('verify_outcome', '?')} | "
           f"{s.get('cmdb_match_quality', '?')} |")
    ap("")

    ap("**Shortest 5 runs:**")
    ap("")
    ap("| CVE | Wall (s) | Outcome | CMDB quality |")
    ap("|-----|----------|---------|--------------|")
    for r in by_wall[-5:]:
        s = r.get("state", {})
        ap(f"| {r['cve_id']} | {r['wall_s']:.1f} | "
           f"{s.get('verify_outcome', '?')} | "
           f"{s.get('cmdb_match_quality', '?')} |")
    ap("")

    # ── 2. Outcome distribution ──
    ap("## 2. Verification Outcomes")
    ap("")
    outcomes = Counter(
        r.get("state", {}).get("verify_outcome", "unknown")
        for r in results
    )
    for outcome, count in outcomes.most_common():
        pct = 100 * count / n
        ap(f"  {outcome:30s} {count:3d} ({pct:.0f}%)")
    ap("")

    retro_outcomes = Counter(
        r.get("state", {}).get("retro_outcome", "unknown")
        for r in results
    )
    ap("**Retro outcomes:**")
    for outcome, count in retro_outcomes.most_common():
        ap(f"  {outcome:30s} {count:3d}")
    ap("")

    # ── 3. CMDB correlation ──
    ap("## 3. CMDB Correlation")
    ap("")
    cmdb_q = Counter(
        r.get("state", {}).get("cmdb_match_quality", "unknown")
        for r in results
    )
    for q, count in cmdb_q.most_common():
        pct = 100 * count / n
        ap(f"  {q:25s} {count:3d} ({pct:.0f}%)")
    ap("")

    scores = [
        r.get("state", {}).get("cmdb_match_score", 0)
        for r in results
        if r.get("state", {}).get("cmdb_match_score") is not None
    ]
    if scores:
        ap(_stat_line([float(s) for s in scores], "Match score"))
    ap("")

    # Host counts
    host_counts = [
        len(r.get("state", {}).get("affected_host_names") or [])
        for r in results
    ]
    ap(f"  Avg affected hosts per CVE: {statistics.mean(host_counts):.1f}")
    ap(f"  CVEs with 0 hosts: {sum(1 for h in host_counts if h == 0)}")
    ap("")

    # ── 4. CargoNet correlation ──
    ap("## 4. CargoNet Topology Correlation")
    ap("")
    cn_counts = [
        r.get("state", {}).get("cargonet_node_count", 0) or 0
        for r in results
    ]
    ap(f"  CVEs with CargoNet match: {sum(1 for c in cn_counts if c > 0)}/{n}")
    ap(f"  Avg matched nodes: {statistics.mean(cn_counts):.1f}")
    ap("")

    # ── 5. Sandbox ──
    ap("## 5. Sandbox Execution")
    ap("")
    sb_runtimes = Counter(
        r.get("state", {}).get("sandbox_runtime", "unknown")
        for r in results
    )
    for rt, count in sb_runtimes.most_common():
        ap(f"  {rt:25s} {count:3d}")
    ap("")
    sb_statuses = Counter(
        r.get("state", {}).get("sandbox_status", "unknown")
        for r in results
    )
    ap("**Sandbox status:**")
    for st, count in sb_statuses.most_common():
        ap(f"  {st:25s} {count:3d}")
    ap("")
    sb_latencies = [
        r.get("state", {}).get("sandbox_probe_latency_ms", 0) or 0
        for r in results
        if (r.get("state", {}).get("sandbox_status") or "") not in ("skipped", "")
    ]
    if sb_latencies:
        ap(_stat_line([float(l) for l in sb_latencies], "Probe latency (ms)"))
    quarantined = sum(
        1 for r in results
        if r.get("state", {}).get("sandbox_quarantined")
    )
    ap(f"  Quarantined: {quarantined}")
    ap("")

    # ── 6. Planner ──
    ap("## 6. Planner Quality")
    ap("")
    plan_scores = [
        r.get("state", {}).get("plan_quality_score_bp", 0) or 0
        for r in results
        if r.get("state", {}).get("plan_hash")
    ]
    if plan_scores:
        ap(_stat_line([float(s) for s in plan_scores], "Plan quality (bp)"))
    plan_lats = [
        r.get("state", {}).get("planner_latency_ms", 0) or 0
        for r in results
        if r.get("state", {}).get("planner_latency_ms")
    ]
    if plan_lats:
        ap(_stat_line([float(l) for l in plan_lats], "Planner latency (ms)"))
    verifier_pass = sum(
        1 for r in results
        if r.get("state", {}).get("planner_verifier_passed")
    )
    ap(f"  Verifier passed: {verifier_pass}/{n}")
    critic_verdicts = Counter(
        r.get("state", {}).get("critic_verdict", "")
        for r in results
    )
    if critic_verdicts:
        ap(f"  Critic verdicts: {dict(critic_verdicts)}")
    ap("")

    # ── 7. CR lifecycle ──
    ap("## 7. Change Request Lifecycle")
    ap("")
    cr_statuses = Counter(
        r.get("state", {}).get("cr_status", "unknown")
        for r in results
    )
    for st, count in cr_statuses.most_common():
        ap(f"  {st:20s} {count:3d}")
    ap("")
    attach_counts = [
        r.get("state", {}).get("attachment_count", 0) or 0
        for r in results
    ]
    if attach_counts:
        ap(_stat_line([float(a) for a in attach_counts], "Attachments"))
    self_val = sum(
        1 for r in results
        if r.get("state", {}).get("cr_self_validation_passed")
    )
    ap(f"  CR self-validation passed: {self_val}/{n}")
    svc_lookup = Counter(
        r.get("state", {}).get("cr_service_lookup_status", "unknown")
        for r in results
    )
    ap(f"  Service lookup: {dict(svc_lookup)}")
    ap("")

    # ── 8. Retro + KG + Doc+ ──
    ap("## 8. Retrospective & Knowledge")
    ap("")
    retro_pg = sum(1 for r in results if r.get("state", {}).get("retro_pg_written"))
    retro_redis = sum(1 for r in results if r.get("state", {}).get("retro_redis_written"))
    retro_pgvec = sum(1 for r in results if r.get("state", {}).get("retro_pgvector_written"))
    docplus = sum(1 for r in results if r.get("state", {}).get("docplus_published"))
    kg_written = sum(1 for r in results if r.get("state", {}).get("kg_run_written"))
    ap(f"  Retro PG written:       {retro_pg}/{n}")
    ap(f"  Retro Redis written:    {retro_redis}/{n}")
    ap(f"  Retro PGVector written: {retro_pgvec}/{n}")
    ap(f"  Doc+ published:         {docplus}/{n}")
    ap(f"  KG run written:         {kg_written}/{n}")
    ap("")

    prior_counts = [
        r.get("state", {}).get("prior_retro_count", 0) or 0
        for r in results
    ]
    if prior_counts:
        ap(_stat_line([float(p) for p in prior_counts], "Prior retros consulted"))
    retrieval_modes = Counter(
        r.get("state", {}).get("prior_retro_retrieval_mode", "")
        for r in results
    )
    ap(f"  Retrieval modes: {dict(retrieval_modes)}")
    ap("")

    # Graph prior
    gp_counts = [
        len(r.get("state", {}).get("graph_prior_actions") or [])
        for r in results
    ]
    ap(f"  CVEs with graph prior actions: "
       f"{sum(1 for g in gp_counts if g > 0)}/{n}")
    if gp_counts:
        ap(_stat_line([float(g) for g in gp_counts], "Graph prior actions"))

    # KG nodes/edges
    kg_nodes = [
        r.get("state", {}).get("kg_run_nodes_written", 0) or 0
        for r in results
    ]
    kg_edges = [
        r.get("state", {}).get("kg_run_edges_written", 0) or 0
        for r in results
    ]
    if any(kg_nodes):
        ap(_stat_line([float(k) for k in kg_nodes], "KG nodes/run"))
        ap(_stat_line([float(k) for k in kg_edges], "KG edges/run"))
    ap("")

    # ── 9. Framework/Doctrine ──
    ap("## 9. Doctrine & Framework Mapping")
    ap("")
    fw_statuses = Counter(
        r.get("state", {}).get("framework_mapping_status", "")
        for r in results
    )
    ap(f"  Mapping status: {dict(fw_statuses)}")
    ctrl_counts = [
        len(r.get("state", {}).get("framework_controls") or [])
        for r in results
    ]
    ap(f"  CVEs with NIST controls: {sum(1 for c in ctrl_counts if c > 0)}/{n}")
    ap("")

    # ── 10. Per-host verification detail ──
    ap("## 10. Per-Host Verification")
    ap("")
    total_hosts = 0
    verified_ok = 0
    verified_fail = 0
    probe_methods = Counter()
    for r in results:
        pvr = r.get("state", {}).get("per_host_verify_results") or []
        total_hosts += len(pvr)
        for h in pvr:
            if h.get("ok"):
                verified_ok += 1
            else:
                verified_fail += 1
            probe_methods[h.get("probe_method", "unknown")] += 1
    ap(f"  Total host probes: {total_hosts}")
    ap(f"  Passed: {verified_ok}  Failed: {verified_fail}")
    if total_hosts:
        ap(f"  Pass rate: {100*verified_ok/total_hosts:.1f}%")
    ap(f"  Probe methods: {dict(probe_methods)}")
    ap("")

    # ── 11. Per-node execution profile ──
    ap("## 11. Node Execution Profile")
    ap("")
    all_node_timings: dict[str, list[float]] = defaultdict(list)
    for r in results:
        for node, timing in (r.get("node_timing") or {}).items():
            all_node_timings[node].append(timing["duration_s"])
    ap("| Node | Runs | Mean (s) | Median (s) | Max (s) |")
    ap("|------|------|----------|------------|---------|")
    for node in sorted(all_node_timings, key=lambda n: -statistics.mean(all_node_timings[n])):
        vals = all_node_timings[node]
        avg = statistics.mean(vals)
        med = statistics.median(vals)
        mx = max(vals)
        ap(f"| {node} | {len(vals)} | {avg:.2f} | {med:.2f} | {mx:.2f} |")
    ap("")

    # ── 12. Error catalog ──
    ap("## 12. Error Catalog")
    ap("")
    error_keys = [k for k in _STATE_KEYS if k.startswith("last_") and k.endswith("_error")]
    error_counts: dict[str, list[str]] = defaultdict(list)
    for r in results:
        for ek in error_keys:
            val = r.get("state", {}).get(ek, "")
            if val:
                error_counts[ek].append(f"{r['cve_id']}: {str(val)[:120]}")
    if error_counts:
        for ek in sorted(error_counts):
            count = len(error_counts[ek])
            ap(f"### {ek} ({count} occurrence(s))")
            ap("")
            for detail in error_counts[ek][:5]:
                ap(f"- {detail}")
            if count > 5:
                ap(f"- ... and {count - 5} more")
            ap("")
    else:
        ap("No errors recorded.")
        ap("")

    # ── 13. Install type breakdown ──
    ap("## 13. Install Type vs Outcome")
    ap("")
    v2 = json.load(open(_CVES_PATH))
    cve_install = {c["cve_id"]: c.get("install_type", "?") for c in v2["cves"]}
    type_outcomes: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        cid = r.get("cve_id", "")
        itype = cve_install.get(cid, "?")
        outcome = r.get("state", {}).get("verify_outcome", "unknown")
        type_outcomes[itype][outcome] += 1
    ap("| Install Type | Count | Patched | Vulnerable | Mitigation | N/A | Other |")
    ap("|-------------|-------|---------|------------|------------|-----|-------|")
    for itype in sorted(type_outcomes):
        oc = type_outcomes[itype]
        total = sum(oc.values())
        ap(f"| {itype} | {total} | "
           f"{oc.get('patched', 0)} | "
           f"{oc.get('vulnerable', 0)} | "
           f"{oc.get('mitigation_applied', 0)} | "
           f"{oc.get('not_applicable', 0) + oc.get('substrate_not_applicable', 0)} | "
           f"{total - oc.get('patched', 0) - oc.get('vulnerable', 0) - oc.get('mitigation_applied', 0) - oc.get('not_applicable', 0) - oc.get('substrate_not_applicable', 0)} |")
    ap("")

    # ── 14. SSVC tier distribution ──
    ap("## 14. SSVC Tier Distribution")
    ap("")
    tiers = Counter(
        r.get("state", {}).get("ssvc_tier", "unknown")
        for r in results
    )
    for tier, count in tiers.most_common():
        ap(f"  {tier:25s} {count:3d}")
    ap("")

    # ── 15. EventBus audit summary ──
    ap("## 15. EventBus Audit Trail (from JSONL audit log)")
    ap("")
    total_events = sum(
        (r.get("audit") or {}).get("event_count", 0) for r in results
    )
    if total_events == 0:
        ap("_No audit events captured. Ensure harbor serve runs with "
           "`--audit-log /tmp/harbor-audit.jsonl`._")
        ap("")
    else:
        ap(f"**Total events across all runs: {total_events}**")
        ap("")

        # Aggregate event types
        all_event_types: Counter = Counter()
        for r in results:
            for etype, count in (
                (r.get("audit") or {}).get("event_types") or {}
            ).items():
                all_event_types[etype] += count
        ap("**Event type distribution:**")
        ap("")
        ap("| Event Type | Count | Per-Run Avg |")
        ap("|------------|-------|-------------|")
        for etype, count in all_event_types.most_common():
            ap(f"| {etype} | {count} | {count/n:.1f} |")
        ap("")

        # Tool call analysis
        all_tool_names: Counter = Counter()
        for r in results:
            for tc in (r.get("audit") or {}).get("tool_calls") or []:
                all_tool_names[tc.get("tool", "?")] += 1
        if all_tool_names:
            ap("**Tool call frequency:**")
            ap("")
            ap("| Tool | Calls | Per-Run Avg |")
            ap("|------|-------|-------------|")
            for tool, count in all_tool_names.most_common(20):
                ap(f"| {tool} | {count} | {count/n:.1f} |")
            ap("")

        # Token usage
        token_totals = [
            (r.get("audit") or {}).get("total_tokens", 0)
            for r in results
        ]
        if any(token_totals):
            ap(_stat_line(
                [float(t) for t in token_totals], "Tokens per run"
            ))
            ap(f"  Total tokens: {sum(token_totals):,}")
            ap("")

        # Governance (Bosun) events
        bosun_total = sum(
            (r.get("audit") or {}).get("bosun_event_count", 0)
            for r in results
        )
        if bosun_total:
            ap(f"**Bosun governance events: {bosun_total}**")
            ap("")
            bosun_kinds: Counter = Counter()
            for r in results:
                for be in (r.get("audit") or {}).get("bosun_events") or []:
                    bosun_kinds[be.get("kind", "?")] += 1
            for kind, count in bosun_kinds.most_common():
                ap(f"  {kind}: {count}")
            ap("")

        # Artifact writes
        artifact_total = sum(
            (r.get("audit") or {}).get("artifact_count", 0)
            for r in results
        )
        ap(f"**Artifacts written: {artifact_total}** "
           f"(avg {artifact_total/n:.1f}/run)")
        ap("")

        # Transition count (rule firings)
        trans_counts = [
            (r.get("audit") or {}).get("transition_count", 0)
            for r in results
        ]
        if any(trans_counts):
            ap(_stat_line(
                [float(t) for t in trans_counts], "Transitions/run"
            ))
        ap("")

        # Errors from audit
        audit_errors = sum(
            (r.get("audit") or {}).get("error_count", 0)
            for r in results
        )
        if audit_errors:
            ap(f"**Audit-captured errors: {audit_errors}**")
            ap("")
            for r in results:
                for err in (r.get("audit") or {}).get("errors") or []:
                    if err.get("fatal"):
                        ap(f"- FATAL [{r['cve_id']}] step {err['step']}: "
                           f"{err['message']}")
            ap("")

    # ── 16. Node I/O audit ──
    ap("## 16. Node I/O Audit (state keys written per node)")
    ap("")
    ap("Shows which state keys each node typically writes — derived from "
       "state diffs between consecutive checkpoints.")
    ap("")
    node_writes: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        for entry in r.get("node_io_trace") or []:
            node = entry.get("node", "?")
            for k in entry.get("keys_written", []):
                node_writes[node][k] += 1
    for node in sorted(node_writes, key=lambda n: -sum(node_writes[n].values())):
        top_keys = node_writes[node].most_common(10)
        keys_str = ", ".join(f"{k}({c})" for k, c in top_keys)
        ap(f"- **{node}**: {keys_str}")
    ap("")

    # ── 17. Full CVE scorecard ──
    ap("## 17. Full CVE Scorecard")
    ap("")
    ap("| CVE | Wall(s) | Outcome | CMDB Q | CN Nodes | Sandbox | Retro | CR | Errors |")
    ap("|-----|---------|---------|--------|----------|---------|-------|----|--------|")
    for r in sorted(results, key=lambda r: r["cve_id"]):
        s = r.get("state", {})
        errs = sum(
            1 for ek in error_keys
            if s.get(ek, "")
        )
        ap(f"| {r['cve_id']} | {r['wall_s']:.0f} | "
           f"{s.get('verify_outcome', '?')[:12]} | "
           f"{s.get('cmdb_match_quality', '?')[:8]} | "
           f"{s.get('cargonet_node_count', 0) or 0} | "
           f"{s.get('sandbox_status', '?')[:8]} | "
           f"{'Y' if s.get('retro_pg_written') else 'N'} | "
           f"{s.get('cr_status', '?')[:8]} | "
           f"{errs} |")
    ap("")

    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────
async def _amain(args: argparse.Namespace) -> int:
    cves = json.loads(_CVES_PATH.read_text()).get("cves", [])
    if args.limit:
        cves = cves[: args.limit]
    n = len(cves)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    out_dir = _ARTIFACTS_ROOT / "scorecard"
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / f"score100_{ts}.jsonl"
    report_path = out_dir / f"score100_{ts}.md"

    print(f"=== score_100 ===")
    print(f"  CVEs          : {n}")
    print(f"  serve         : {args.serve_base}")
    print(f"  checkpoint DB : {args.checkpoint_db}")
    print(f"  JSONL output  : {jsonl_path}")
    print(f"  Report output : {report_path}")
    print(f"  Run timeout   : {args.run_timeout}s")
    print()

    results: list[dict] = []
    async with httpx.AsyncClient(
        base_url=args.serve_base, timeout=30
    ) as client:
        for i, cve in enumerate(cves, 1):
            cve_id = cve["cve_id"]
            print(f"  [{i:3d}/{n}] {cve_id} ...", end="", flush=True)
            result = await _run_one(
                client,
                args.graph_id,
                cve_id,
                Path(args.checkpoint_db),
                Path(args.audit_log),
                args.run_timeout,
            )
            results.append(result)
            outcome = result.get("state", {}).get("verify_outcome", "?")
            print(
                f" {result['status']} | {outcome} | "
                f"{result['wall_s']:.0f}s | "
                f"ckpts={result.get('checkpoint_count', 0)}"
            )
            # Write JSONL incrementally
            with open(jsonl_path, "a") as f:
                f.write(json.dumps(result, default=str) + "\n")

    # Generate report
    report = _generate_report(results, out_dir)
    report_path.write_text(report)
    print(f"\n=== Report written to {report_path} ===")

    # Print summary
    outcomes = Counter(
        r.get("state", {}).get("verify_outcome", "unknown")
        for r in results
    )
    walls = [r["wall_s"] for r in results if r["wall_s"] > 0]
    print(f"\n=== Summary ===")
    print(f"  Total CVEs:  {n}")
    print(f"  Outcomes:    {dict(outcomes)}")
    if walls:
        print(f"  Wall time:   mean={statistics.mean(walls):.1f}s  "
              f"median={statistics.median(walls):.1f}s  "
              f"total={sum(walls):.0f}s")
    errors = sum(
        1 for r in results
        if r.get("status") not in ("done", "completed")
    )
    print(f"  Run errors:  {errors}")

    return 0 if errors == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="100-CVE scoring sweep with analytics report"
    )
    ap.add_argument(
        "--serve-base", default=_DEFAULT_SERVE_BASE,
        help="harbor serve base URL",
    )
    ap.add_argument(
        "--graph-id", default=_DEFAULT_GRAPH_ID,
        help="graph ID to run",
    )
    ap.add_argument(
        "--checkpoint-db", default=str(_DEFAULT_CHECKPOINT_DB),
        help="SQLite checkpoint DB path",
    )
    ap.add_argument(
        "--audit-log", default=str(_DEFAULT_AUDIT_LOG),
        help="JSONL audit log path (harbor serve --audit-log)",
    )
    ap.add_argument(
        "--run-timeout", type=int, default=_DEFAULT_RUN_TIMEOUT_S,
        help="per-run timeout in seconds",
    )
    ap.add_argument(
        "--limit", type=int, default=0,
        help="limit to first N CVEs (0 = all)",
    )
    args = ap.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
