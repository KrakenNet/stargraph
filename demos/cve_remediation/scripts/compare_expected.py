#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare an actual scoring run against expected outcomes.

Reads:
  - actual JSONL from scripts/score_run.py (one record per CVE)
  - fixtures/scoring_expected.json (per-CVE expected_outcome + ...)

Scoring rules (per CVE):

  outcome_match = (actual.retro_outcome == expected.expected_outcome)
                  OR (expected="not_applicable" AND actual in
                      {vulnerable, mitigation_applied, rollback}
                      AND no hosts AND no CR-affecting actions)

  correlation_match = (actual.cmdb_match_quality == expected.expected_correlation_quality)
                      OR (both effectively "no match")

  sandbox_match = expected.expected_sandbox_status in
                  {actual.sandbox_status, "skipped"} (lenient)

A CVE PASSES when outcome_match AND correlation_match.

Reports:
  - overall pass rate
  - per-tier breakdown (patchable / correlatable_no_recipe / orphan_cmdb / not_in_env)
  - per-mismatch breakdown (where the pipeline disagrees)
  - top mismatches with details (CVE, tier, expected, actual)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


_FIX_ROOT = Path(__file__).resolve().parent.parent / "fixtures"
_EXPECTED_PATH = _FIX_ROOT / "scoring_expected.json"


def _load_expected(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text())
    return {c["cve_id"]: c for c in data["cves"]}


def _load_actual(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _normalize_outcome_for_compare(actual_outcome: str, hosts_n: int) -> str:
    # Pipeline emits "vulnerable" when correlation misses entirely;
    # for the not_applicable tier we treat that as a successful suppress.
    if hosts_n == 0 and actual_outcome in ("vulnerable", "rollback", "incomplete"):
        return "not_applicable"
    return actual_outcome or "missing"


def _correlation_match(actual_q: str, expected_q: str) -> bool:
    if actual_q == expected_q:
        return True
    # high and medium are both "found correctly"; treat as adjacent.
    if expected_q == "high" and actual_q == "medium":
        return True
    if expected_q == "miss" and actual_q in ("miss", "reject"):
        return True
    if expected_q == "low_conf_no_topo" and actual_q in ("low_conf_no_topo", "miss"):
        return True
    return False


def _compare(actual: list[dict[str, Any]], expected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    per_cve = []
    by_tier: dict[str, list] = defaultdict(list)
    mismatch_reasons: dict[str, int] = defaultdict(int)

    for row in actual:
        cve_id = row.get("cve_id", "")
        outcome = row.get("outcome", {}) or {}
        exp = expected.get(cve_id)
        if not exp:
            continue

        tier = exp["tier"]
        actual_outcome = outcome.get("retro_outcome") or "missing"
        actual_q = outcome.get("cmdb_match_quality") or "miss"
        hosts_n = len(outcome.get("affected_host_names") or [])
        normalized = _normalize_outcome_for_compare(actual_outcome, hosts_n)
        # 4-tier matrix: accepted_outcomes is the set of honest handling
        # labels for the tier.  Pre-2026-05-08 tools wrote a single
        # expected_outcome; honor both shapes for back-compat.
        accepted = exp.get("accepted_outcomes") or [exp["expected_outcome"]]
        outcome_ok = (normalized in accepted)
        corr_ok = _correlation_match(actual_q, exp["expected_correlation_quality"])
        passed = outcome_ok and corr_ok

        rec = {
            "cve_id": cve_id,
            "tier": tier,
            "expected_outcome": exp["expected_outcome"],
            "actual_outcome": actual_outcome,
            "actual_normalized": normalized,
            "outcome_ok": outcome_ok,
            "expected_correlation": exp["expected_correlation_quality"],
            "actual_correlation": actual_q,
            "correlation_ok": corr_ok,
            "hosts_n": hosts_n,
            "passed": passed,
        }
        per_cve.append(rec)
        by_tier[tier].append(rec)
        if not passed:
            if not outcome_ok and not corr_ok:
                mismatch_reasons["both_outcome_and_correlation"] += 1
            elif not outcome_ok:
                mismatch_reasons[f"outcome:{normalized}!={exp['expected_outcome']}"] += 1
            elif not corr_ok:
                mismatch_reasons[f"correlation:{actual_q}!={exp['expected_correlation_quality']}"] += 1

    pass_count = sum(1 for r in per_cve if r["passed"])
    return {
        "per_cve": per_cve,
        "by_tier": dict(by_tier),
        "mismatch_reasons": dict(mismatch_reasons),
        "pass_count": pass_count,
        "total": len(per_cve),
    }


def _report(result: dict[str, Any]) -> None:
    pass_count = result["pass_count"]
    total = result["total"]
    pct = (100.0 * pass_count / total) if total else 0.0
    print(f"=== Overall: {pass_count}/{total} = {pct:.1f}% ===\n")

    print("=== Per-tier ===")
    for tier, rows in sorted(result["by_tier"].items()):
        ok = sum(1 for r in rows if r["passed"])
        n = len(rows)
        print(f"  {tier}: {ok}/{n} ({100.0 * ok / n:.0f}%)")

    print("\n=== Top mismatch reasons ===")
    for reason, count in sorted(
        result["mismatch_reasons"].items(), key=lambda kv: -kv[1]
    )[:10]:
        print(f"  {count:3d}× {reason}")

    print("\n=== Failures by tier (first 5 per tier) ===")
    for tier, rows in sorted(result["by_tier"].items()):
        fails = [r for r in rows if not r["passed"]]
        if not fails:
            continue
        print(f"\n  -- {tier} ({len(fails)} fails) --")
        for r in fails[:5]:
            o_marker = "✓" if r["outcome_ok"] else "✗"
            c_marker = "✓" if r["correlation_ok"] else "✗"
            print(f"     {r['cve_id']}: outcome[{o_marker}]"
                  f" expected={r['expected_outcome']} actual={r['actual_normalized']}"
                  f" | corr[{c_marker}] expected={r['expected_correlation']}"
                  f" actual={r['actual_correlation']} hosts={r['hosts_n']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Score a run vs expected outcomes")
    ap.add_argument("jsonl", help="actual run JSONL from score_run.py")
    ap.add_argument("--expected", default=str(_EXPECTED_PATH))
    ap.add_argument("--csv", action="store_true", help="emit per-CVE CSV")
    args = ap.parse_args()

    actual = _load_actual(Path(args.jsonl))
    expected = _load_expected(Path(args.expected))
    result = _compare(actual, expected)

    if args.csv:
        cols = ["cve_id", "tier", "passed", "outcome_ok", "correlation_ok",
                "expected_outcome", "actual_normalized",
                "expected_correlation", "actual_correlation", "hosts_n"]
        print(",".join(cols))
        for r in result["per_cve"]:
            print(",".join(str(r.get(c, "")) for c in cols))
    else:
        _report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
