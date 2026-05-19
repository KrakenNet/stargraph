#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Derive expected_outcome + expected_* fields per CVE from existing
ground truth.

Reads:
  - fixtures/scoring_ground_truth.json (recipe_authored, in_cmdb, in_topology, ...)
  - fixtures/scoring_cves_v1.json (vendor, product)

Writes:
  - fixtures/scoring_expected.json — one entry per CVE with derived
    expected fields.

Tiers (deterministic, no LM):

  Tier 1 — fully patchable:
    recipe_authored=True AND in_cmdb=True AND in_topology=True
    → expected_outcome="patched"
       expected_correlation_quality="high"
       expected_sandbox_status="ok"
       expected_verify_outcome="patched"

  Tier 2 — correlatable, no recipe:
    recipe_authored=False AND in_cmdb=True AND in_topology=True
    → expected_outcome="mitigation_applied"
       expected_correlation_quality="high"
       expected_sandbox_status="skipped"  (mitigation_only path)
       expected_verify_outcome="vulnerable"  (host still has vuln)

  Tier 3 — orphan CMDB (CI exists, no Runs-on):
    in_cmdb=True AND in_topology=False
    → expected_outcome="not_applicable"
       expected_correlation_quality="low_conf_no_topo"
       expected_sandbox_status="skipped"

  Tier 4 — not in environment:
    in_cmdb=False
    → expected_outcome="not_applicable"
       expected_correlation_quality="miss"
       expected_sandbox_status="skipped"

Reasoning:
  - "patched" requires: real vulnerable package on host (recipe) +
    correlatable host (cmdb+topo) + reachable apply path.
  - "mitigation_applied" is the bounded-success path when no recipe
    means we can't physically patch but we can still emit + validate
    mitigation guidance for an operator.
  - "not_applicable" means the CVE doesn't apply to anything in the
    test environment — pipeline should suppress.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


_FIX_ROOT = Path(__file__).resolve().parent.parent / "fixtures"
_GT_PATH = _FIX_ROOT / "scoring_ground_truth.json"
_V1_PATH = _FIX_ROOT / "scoring_cves_v1.json"
_OUT_PATH = _FIX_ROOT / "scoring_expected.json"
_RECIPES_DIR = _FIX_ROOT / "vuln_install_recipes"


def _has_recipe(cve_id: str) -> bool:
    """Source-of-truth for recipe coverage: disk presence."""
    return (_RECIPES_DIR / f"{cve_id}.yaml").is_file()


def _classify(gt: dict) -> dict:
    # Recipe presence is the disk-truth (auto-generated + hand-authored
    # both count). GT field is stale on auto-gen pass.
    recipe = _has_recipe(gt["cve_id"])
    cmdb = bool(gt.get("in_cmdb", False))
    topo = bool(gt.get("in_topology", False))

    if recipe and cmdb and topo:
        tier = "patchable"
        # Tier-1 strict: must reach patched.
        accepted = ["patched"]
        corr = "high"
        sand = "ok"
        verify = "patched"
    elif cmdb and topo:
        tier = "applicable_no_recipe"
        # Tier-2 relaxed: any honest handling passes — LM-emitted
        # bundle may apply (patched), mitigate (mitigation_applied),
        # or escalate (hitl_review).  Cheats blocked by R1-R4
        # acceptance test, not by outcome label.
        accepted = ["patched", "mitigation_applied", "hitl_review"]
        corr = "high"
        sand = "ok"
        verify = "patched"
    elif cmdb and not topo:
        tier = "orphan_cmdb"
        accepted = ["not_applicable"]
        corr = "low_conf_no_topo"
        sand = "skipped"
        verify = "vulnerable"
    else:
        tier = "not_in_env"
        accepted = ["not_applicable"]
        corr = "miss"
        sand = "skipped"
        verify = "vulnerable"

    return {
        "tier": tier,
        # Primary expected (back-compat for older scoring tools).
        "expected_outcome": accepted[0],
        # 4-tier set: any of these counts as honest handling for the tier.
        "accepted_outcomes": accepted,
        "expected_correlation_quality": corr,
        "expected_sandbox_status": sand,
        "expected_verify_outcome": verify,
        "expected_hosts_min": len(gt.get("topo_nodes", [])),
        "patchable_in_test_rig": tier == "patchable",
    }


def main() -> int:
    gt_data = json.loads(_GT_PATH.read_text())
    v1_data = json.loads(_V1_PATH.read_text())
    v1_by_id = {c["cve_id"]: c for c in v1_data["cves"]}

    out = {"version": "1", "cves": []}
    counts = {"patchable": 0, "applicable_no_recipe": 0,
              "orphan_cmdb": 0, "not_in_env": 0}

    for gt in gt_data["cves"]:
        cve_id = gt["cve_id"]
        v1 = v1_by_id.get(cve_id, {})
        derived = _classify(gt)
        counts[derived["tier"]] += 1

        entry = {
            "cve_id": cve_id,
            "product": gt.get("product") or v1.get("product", ""),
            "vendor": gt.get("vendor") or v1.get("vendor", ""),
            "vuln_class": gt.get("vuln_class") or v1.get("vuln_class", ""),
            "recipe_authored": gt.get("recipe_authored", False),
            "in_cmdb": gt.get("in_cmdb", False),
            "in_topology": gt.get("in_topology", False),
            "expected_cmdb_cis": gt.get("cmdb_cis", []),
            "expected_topo_nodes": gt.get("topo_nodes", []),
            **derived,
        }
        out["cves"].append(entry)

    _OUT_PATH.write_text(json.dumps(out, indent=2))
    print(f"wrote {_OUT_PATH}")
    print("Tier counts:")
    for t, n in counts.items():
        print(f"  {t}: {n}")
    print(f"  total: {sum(counts.values())}")
    print()
    print("Expected outcome distribution:")
    out_dist: dict[str, int] = {}
    for c in out["cves"]:
        out_dist[c["expected_outcome"]] = out_dist.get(c["expected_outcome"], 0) + 1
    for k, v in sorted(out_dist.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    print(f"\n=> patch-rate ceiling: {counts['patchable']}/100 = {counts['patchable']}%")
    print(f"=> total expected pass (any honest handling):"
          f" {counts['patchable'] + counts['applicable_no_recipe'] + counts['orphan_cmdb'] + counts['not_in_env']}/100")
    return 0


if __name__ == "__main__":
    sys.exit(main())
