# SPDX-License-Identifier: Apache-2.0
"""Score a scoring-run JSONL against the h11 ground truth manifest.

Reads:

* ``$HARBOR_ARTIFACTS_ROOT/scorecard/run_<ts>.jsonl`` -- one record per
  CVE produced by ``score_run.py``.
* ``demos/cve_remediation/fixtures/scoring_ground_truth.json`` -- per-CVE
  ``cmdb_cis`` + ``topo_nodes`` + ``recipe_authored`` (truth).

Computes:

1. **Per-node stats** -- across every CVE that exercised a node:
   invocation count, mean step count, error rate (presence of any
   ``last_<step>_error`` field).
2. **Correlation P/R/F1** -- workflow-predicted CIs/hosts vs ground
   truth, broken down by bucket (cargonet/docker/static/hitl) and by
   ``recipe_authored``.
3. **Plan quality** -- mean ``plan_quality_score_bp``, fraction with
   ``planner_verifier_passed``, mean citation count, mean
   ``planner_latency_ms``.
4. **Lifecycle progression** -- terminal status distribution; sandbox
   pass rate; CR closure rate.

Emits human table to stdout + JSON to
``$HARBOR_ARTIFACTS_ROOT/scorecard/score_<ts>.json``.

Run::

    uv run --no-project python -m demos.cve_remediation.scripts.score_report \\
        .harbor/artifacts/scorecard/run_<ts>.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

_DEMO_ROOT = Path(__file__).resolve().parent.parent
_TRUTH_PATH = _DEMO_ROOT / "fixtures" / "scoring_ground_truth.json"
_ARTIFACTS_ROOT = Path(
    os.environ.get("HARBOR_ARTIFACTS_ROOT", ".harbor/artifacts")
)
_OUT_DIR = _ARTIFACTS_ROOT / "scorecard"


def _load_jsonl(path: Path):
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _truth_lookup(truth):
    return {c["cve_id"]: c for c in truth["cves"]}


def _per_node_stats(records):
    node_to_runs = defaultdict(int)
    node_to_steps = defaultdict(list)
    error_keys = (
        "last_intake_error", "last_planner_error", "last_sandbox_error",
        "last_cmdb_error", "last_cargonet_error", "last_cr_link_error",
        "last_retro_error", "last_run_outcome_error",
    )
    error_counts = defaultdict(int)
    for r in records:
        per_node = r.get("per_node", {}) or {}
        for node, info in per_node.items():
            node_to_runs[node] += 1
            node_to_steps[node].append(info.get("steps", 0))
        outcome = r.get("outcome") or {}
        for k in error_keys:
            v = outcome.get(k)
            if v:
                error_counts[k] += 1
    out = {}
    total = len(records) or 1
    for node, runs in node_to_runs.items():
        steps = node_to_steps[node]
        out[node] = {
            "invoke_count": runs,
            "invoke_rate": round(runs / total, 3),
            "mean_steps": round(sum(steps) / len(steps), 2) if steps else 0.0,
            "max_steps": max(steps) if steps else 0,
        }
    return out, dict(error_counts)


def _prf1(predicted, expected):
    p_set, e_set = set(predicted or []), set(expected or [])
    tp = len(p_set & e_set)
    fp = len(p_set - e_set)
    fn = len(e_set - p_set)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
    }


def _correlation(records, truth_by_id):
    """For each record: compare predicted CIs/hosts to ground truth."""
    cmdb_per_record = []
    topo_per_record = []
    for r in records:
        cid = r["cve_id"]
        truth = truth_by_id.get(cid) or {}
        outcome = r.get("outcome") or {}
        # CMDB predicted: prefer affected_host_names; fallback to candidate_products
        predicted_cmdb = outcome.get("affected_host_names") or []
        predicted_topo = outcome.get("cargonet_correlation_map") or []
        # cargonet_correlation_map is dict[host -> probe_outcome]; pull keys
        if isinstance(predicted_topo, dict):
            predicted_topo = list(predicted_topo.keys())
        cmdb_truth = truth.get("cmdb_cis") or []
        topo_truth = truth.get("topo_nodes") or []
        cmdb = _prf1(predicted_cmdb, cmdb_truth)
        topo = _prf1(predicted_topo, topo_truth)
        cmdb["cve_id"] = cid
        cmdb["vuln_class"] = truth.get("vuln_class")
        cmdb["recipe_authored"] = bool(truth.get("recipe_authored"))
        topo["cve_id"] = cid
        topo["vuln_class"] = truth.get("vuln_class")
        topo["recipe_authored"] = bool(truth.get("recipe_authored"))
        cmdb_per_record.append(cmdb)
        topo_per_record.append(topo)
    return cmdb_per_record, topo_per_record


def _aggregate_prf1(per_record, *, group_key=None):
    """Sum tp/fp/fn across records (micro-avg) + simple mean (macro-avg)."""
    groups = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0,
                                   "p_list": [], "r_list": [], "f1_list": []})
    for rec in per_record:
        key = "ALL" if group_key is None else (rec.get(group_key) or "unknown")
        g = groups[key]
        g["tp"] += rec["tp"]; g["fp"] += rec["fp"]; g["fn"] += rec["fn"]
        g["p_list"].append(rec["precision"])
        g["r_list"].append(rec["recall"])
        g["f1_list"].append(rec["f1"])
    out = {}
    for k, g in groups.items():
        micro_p = g["tp"] / (g["tp"] + g["fp"]) if (g["tp"] + g["fp"]) else 0.0
        micro_r = g["tp"] / (g["tp"] + g["fn"]) if (g["tp"] + g["fn"]) else 0.0
        micro_f1 = 2 * micro_p * micro_r / (micro_p + micro_r) if (micro_p + micro_r) else 0.0
        out[k] = {
            "n": len(g["p_list"]),
            "tp": g["tp"], "fp": g["fp"], "fn": g["fn"],
            "micro_precision": round(micro_p, 3),
            "micro_recall": round(micro_r, 3),
            "micro_f1": round(micro_f1, 3),
            "macro_precision": round(sum(g["p_list"]) / len(g["p_list"]), 3) if g["p_list"] else 0.0,
            "macro_recall": round(sum(g["r_list"]) / len(g["r_list"]), 3) if g["r_list"] else 0.0,
            "macro_f1": round(sum(g["f1_list"]) / len(g["f1_list"]), 3) if g["f1_list"] else 0.0,
        }
    return out


def _plan_quality(records):
    bps = [r["outcome"].get("plan_quality_score_bp") for r in records
           if r.get("outcome") and isinstance(r["outcome"].get("plan_quality_score_bp"), (int, float))]
    verified = [bool(r["outcome"].get("planner_verifier_passed"))
                for r in records if r.get("outcome")]
    citations = []
    for r in records:
        cf = (r.get("outcome") or {}).get("planner_citation_findings") or []
        citations.append(len(cf) if isinstance(cf, list) else 0)
    latencies = [r["outcome"].get("planner_latency_ms")
                 for r in records if r.get("outcome")
                 and isinstance(r["outcome"].get("planner_latency_ms"), (int, float))]
    sandbox_passed = [
        (r.get("outcome") or {}).get("sandbox_status") == "verified_patched"
        for r in records
    ]
    return {
        "mean_plan_quality_bp": round(sum(bps) / len(bps), 1) if bps else None,
        "verifier_pass_rate": round(sum(verified) / len(verified), 3) if verified else None,
        "mean_citation_count": round(sum(citations) / len(citations), 2) if citations else None,
        "mean_planner_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "sandbox_verified_rate": round(sum(sandbox_passed) / len(sandbox_passed), 3) if sandbox_passed else None,
        "n_records": len(records),
    }


def _lifecycle(records):
    statuses = defaultdict(int)
    cr_closed = 0
    sandbox_runtimes = defaultdict(int)
    ssvc_tiers = defaultdict(int)
    for r in records:
        statuses[r.get("terminal_status", "unknown")] += 1
        outcome = r.get("outcome") or {}
        if outcome.get("cr_status") == "closed":
            cr_closed += 1
        sandbox_runtimes[outcome.get("sandbox_runtime") or "<none>"] += 1
        ssvc_tiers[outcome.get("ssvc_tier") or "<none>"] += 1
    n = len(records) or 1
    return {
        "terminal_status_dist": dict(statuses),
        "cr_closed_rate": round(cr_closed / n, 3),
        "sandbox_runtime_dist": dict(sandbox_runtimes),
        "ssvc_tier_dist": dict(ssvc_tiers),
    }


def _print_table(report):
    print(f"\n=== SUMMARY (n={report['n_records']}) ===")
    print(f"  terminal: {report['lifecycle']['terminal_status_dist']}")
    print(f"  ssvc tiers: {report['lifecycle']['ssvc_tier_dist']}")
    print(f"  sandbox runtimes: {report['lifecycle']['sandbox_runtime_dist']}")
    print(f"\n=== PLAN QUALITY ===")
    pq = report["plan_quality"]
    print(f"  mean plan_quality_bp : {pq['mean_plan_quality_bp']}")
    print(f"  verifier pass rate   : {pq['verifier_pass_rate']}")
    print(f"  mean citations       : {pq['mean_citation_count']}")
    print(f"  mean planner latency : {pq['mean_planner_latency_ms']} ms")
    print(f"  sandbox verified rate: {pq['sandbox_verified_rate']}")
    print(f"\n=== CMDB CORRELATION (CVE -> CIs) ===")
    print(f"  {'group':<14} {'n':>4}  {'mP':>6} {'mR':>6} {'mF1':>6}  {'MP':>6} {'MR':>6} {'MF1':>6}")
    for k, v in sorted(report["cmdb_correlation"].items()):
        print(f"  {k:<14} {v['n']:>4}  "
              f"{v['micro_precision']:>6} {v['micro_recall']:>6} {v['micro_f1']:>6}  "
              f"{v['macro_precision']:>6} {v['macro_recall']:>6} {v['macro_f1']:>6}")
    print(f"\n=== TOPOLOGY CORRELATION (CVE -> hosts) ===")
    print(f"  {'group':<14} {'n':>4}  {'mP':>6} {'mR':>6} {'mF1':>6}  {'MP':>6} {'MR':>6} {'MF1':>6}")
    for k, v in sorted(report["topo_correlation"].items()):
        print(f"  {k:<14} {v['n']:>4}  "
              f"{v['micro_precision']:>6} {v['micro_recall']:>6} {v['micro_f1']:>6}  "
              f"{v['macro_precision']:>6} {v['macro_recall']:>6} {v['macro_f1']:>6}")
    print(f"\n=== PER-NODE (top 12 by invoke_count) ===")
    nodes = sorted(report["per_node"].items(),
                   key=lambda kv: -kv[1]["invoke_count"])
    print(f"  {'node':<32} {'invokes':>7} {'rate':>5} {'mean_steps':>10}")
    for n, info in nodes[:12]:
        print(f"  {n[:32]:<32} {info['invoke_count']:>7} "
              f"{info['invoke_rate']:>5} {info['mean_steps']:>10}")
    print(f"\n=== ERRORS BY NODE-PHASE ===")
    for k, v in sorted(report["error_counts"].items(), key=lambda kv: -kv[1]):
        print(f"  {k:<28} {v}")


def main():
    ap = argparse.ArgumentParser(description="score a scoring-run JSONL")
    ap.add_argument("jsonl", type=Path, help="path to run_<ts>.jsonl")
    ap.add_argument("--truth", type=Path, default=_TRUTH_PATH)
    ap.add_argument("--json-only", action="store_true")
    args = ap.parse_args()

    records = _load_jsonl(args.jsonl)
    truth = json.loads(args.truth.read_text())
    truth_by_id = _truth_lookup(truth)

    per_node, error_counts = _per_node_stats(records)
    cmdb_per, topo_per = _correlation(records, truth_by_id)

    report = {
        "n_records": len(records),
        "source_jsonl": str(args.jsonl),
        "generated_at": datetime.now(UTC).isoformat(),
        "lifecycle": _lifecycle(records),
        "plan_quality": _plan_quality(records),
        "cmdb_correlation": {
            "ALL": _aggregate_prf1(cmdb_per)["ALL"],
            **{f"bucket:{k}": v
               for k, v in _aggregate_prf1(cmdb_per, group_key="vuln_class").items()},
            **{f"recipe:{'authored' if k else 'unauthored'}": v
               for k, v in _aggregate_prf1(cmdb_per, group_key="recipe_authored").items()},
        },
        "topo_correlation": {
            "ALL": _aggregate_prf1(topo_per)["ALL"],
            **{f"bucket:{k}": v
               for k, v in _aggregate_prf1(topo_per, group_key="vuln_class").items()},
            **{f"recipe:{'authored' if k else 'unauthored'}": v
               for k, v in _aggregate_prf1(topo_per, group_key="recipe_authored").items()},
        },
        "per_node": per_node,
        "error_counts": error_counts,
        "per_record_cmdb": cmdb_per,
        "per_record_topo": topo_per,
    }

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = _OUT_DIR / f"score_{ts}.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True))

    if not args.json_only:
        _print_table(report)
        print(f"\nartifact: {out}")
    else:
        print(str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
