# SPDX-License-Identifier: Apache-2.0
"""Regenerate h11_cmdb_seed.json from scoring_cves_v2.json + recipes.

Closes the gap where the legacy CMDB seed only had 27 CVE→CI bindings
out of the 100 v2 scoring corpus. After this:

* Every v2 CVE has a binding row (host_names, product, vendor,
  host_versions) so the planner / CR creation node can resolve CIs
  by binding lookup instead of falling back to host_pool.
* Every host that appears in any v2 CVE's host_pool exists as a CMDB
  CI row (decommissioned=False, in_topology=True).

Preserves legacy v1 bindings (CVEs not in v2) and existing CI rows
(role, decommissioned, etc) by merge-update rather than overwrite.

Usage:
    python -m demos.cve_remediation.scripts.regen_cmdb_seed [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import yaml

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
_SPEC = _FIXTURES / "scoring_cves_v2.json"
_CMDB_SEED = _FIXTURES / "h11_cmdb_seed.json"
_RECIPES_DIR = _FIXTURES / "vuln_install_recipes"

# install_channel → conventional vendor label. Real packages don't all
# come from these vendors (e.g. urllib3 → PSF, not "PyPI") but for CMDB
# binding purposes the channel-as-vendor convention is consistent and
# already used by the existing seed bindings for cargonet/docker rows.
_CHANNEL_VENDOR = {
    "pip": "PyPI",
    "npm": "npm",
    "gem": "RubyGems",
    "apt": "Debian/Ubuntu",
    "jar": "Maven Central",
    "binary": "upstream",
    "app": "vendor",
}


def _role_from_host(host: str) -> str:
    # h11-<role>-NN  →  role
    parts = host.split("-")
    if len(parts) >= 3:
        return parts[1]
    return ""


def _build_binding_from_recipe(cve_id: str, host_pool: list[str], channel: str) -> dict:
    recipe_path = _RECIPES_DIR / f"{cve_id}.yaml"
    if recipe_path.exists():
        recipe = yaml.safe_load(recipe_path.read_text())
    else:
        recipe = {}
    pkg = recipe.get("package_name") or cve_id
    vuln_ver = recipe.get("vulnerable_version") or "*"
    vendor = _CHANNEL_VENDOR.get(channel, channel or "unknown")
    versions = {h: vuln_ver for h in host_pool}
    return {
        "cve_id": cve_id,
        "host_names": list(host_pool),
        "product": pkg,
        "vendor": vendor,
        "host_versions": versions,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not _SPEC.is_file():
        print(f"! spec not found: {_SPEC}", file=sys.stderr)
        return 2
    if not _CMDB_SEED.is_file():
        print(f"! seed not found: {_CMDB_SEED}", file=sys.stderr)
        return 2

    cves = json.loads(_SPEC.read_text())["cves"]
    seed = json.loads(_CMDB_SEED.read_text())
    legacy_bindings = list(seed.get("bindings", []))
    existing_cis = {ci["name"]: ci for ci in seed.get("cis", [])}

    v2_cve_ids = {c["cve_id"] for c in cves}

    # Build new v2 bindings.
    new_bindings: list[dict] = []
    for c in cves:
        new_bindings.append(
            _build_binding_from_recipe(
                cve_id=c["cve_id"],
                host_pool=c.get("host_pool", []),
                channel=c.get("install_channel", ""),
            )
        )

    # Keep legacy bindings whose CVE isn't in v2.
    kept_legacy = [b for b in legacy_bindings if b["cve_id"] not in v2_cve_ids]
    merged_bindings = kept_legacy + new_bindings

    # Add CIs for any v2 pool host not already present.
    all_v2_hosts = sorted({h for c in cves for h in c.get("host_pool", [])})
    added_cis: list[str] = []
    for host in all_v2_hosts:
        if host in existing_cis:
            continue
        existing_cis[host] = {
            "name": host,
            "role": _role_from_host(host),
            "decommissioned": False,
            "in_topology": True,
        }
        added_cis.append(host)

    seed["bindings"] = merged_bindings
    seed["cis"] = sorted(existing_cis.values(), key=lambda c: c["name"])
    seed["ci_count"] = len(seed["cis"])

    counts_by_vendor = Counter(b["vendor"] for b in new_bindings)
    print(f"=== regen_cmdb_seed ===")
    print(f"  v2 CVE bindings written : {len(new_bindings)}")
    print(f"  legacy bindings kept    : {len(kept_legacy)}")
    print(f"  total bindings (merged) : {len(merged_bindings)}")
    print(f"  v2 hosts                : {len(all_v2_hosts)}")
    print(f"  CIs added (new hosts)   : {len(added_cis)}")
    if added_cis:
        print(f"    added: {added_cis}")
    print(f"  total CIs (post-merge)  : {seed['ci_count']}")
    print(f"  v2 bindings by vendor   : {dict(counts_by_vendor)}")

    if args.dry_run:
        print("  (dry-run: not written)")
        return 0

    _CMDB_SEED.write_text(json.dumps(seed, indent=2, sort_keys=True) + "\n")
    print(f"  wrote: {_CMDB_SEED}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
