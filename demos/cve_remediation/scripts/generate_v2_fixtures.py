# SPDX-License-Identifier: Apache-2.0
"""Generate v2 corpus fixtures from scoring_cves_v2.json.

For each CVE in the spec, emits:
1. fixtures/nvd/<CVE>.json — minimal NVD-shape JSON (configurations, descriptions, metrics, weaknesses, references, cisa* fields)
2. CMDB binding entry into h11_cmdb_seed.json (additive — preserves existing entries)
3. fixtures/vuln_install_recipes/<CVE>.yaml

Recipe schema: vuln_class drives sandbox_runtime via the dispatcher's
_SANDBOX_BY_VULN_CLASS map (library/application/web-framework/host/container → docker_compose).

The recipe pattern plants a marker file that mirrors the audit_signal text,
the probe greps for the signature, and the fix removes the marker file.
Real CVE references go into the description block + fix.rationale so the
remediation citation chain stays honest even when the planted artifact is a
canonicalized marker rather than a vendor-specific binary.

Run::

    uv run --no-project python -m demos.cve_remediation.scripts.generate_v2_fixtures
    uv run --no-project python -m demos.cve_remediation.scripts.generate_v2_fixtures --dry-run

Idempotent: re-running overwrites the generated files.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

_DEMO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = _DEMO_ROOT / "fixtures"
_SPEC = _FIXTURES / "scoring_cves_v2.json"
_NVD_DIR = _FIXTURES / "nvd"
_RECIPES_DIR = _FIXTURES / "vuln_install_recipes"
_CMDB_SEED = _FIXTURES / "h11_cmdb_seed.json"
_TRUTH_PATH = _FIXTURES / "scoring_ground_truth.json"

_CWE_TO_CWE_NAME = {
    "CWE-20": "Improper Input Validation",
    "CWE-22": "Path Traversal",
    "CWE-59": "Improper Link Resolution",
    "CWE-74": "Improper Neutralization",
    "CWE-77": "Command Injection",
    "CWE-78": "OS Command Injection",
    "CWE-79": "Cross-Site Scripting",
    "CWE-89": "SQL Injection",
    "CWE-94": "Code Injection",
    "CWE-119": "Buffer Overflow",
    "CWE-125": "Out-of-bounds Read",
    "CWE-200": "Information Exposure",
    "CWE-269": "Improper Privilege Management",
    "CWE-281": "Improper Preservation of Permissions",
    "CWE-284": "Improper Access Control",
    "CWE-287": "Improper Authentication",
    "CWE-288": "Authentication Bypass",
    "CWE-303": "Incorrect Authentication Check",
    "CWE-306": "Missing Authentication",
    "CWE-362": "Race Condition",
    "CWE-364": "Signal Handler Race",
    "CWE-400": "Uncontrolled Resource Consumption",
    "CWE-403": "Exposure of File Descriptor",
    "CWE-416": "Use After Free",
    "CWE-426": "Untrusted Search Path",
    "CWE-434": "Unrestricted File Upload",
    "CWE-444": "HTTP Request Smuggling",
    "CWE-502": "Deserialization of Untrusted Data",
    "CWE-506": "Embedded Malicious Code",
    "CWE-611": "XML External Entity",
    "CWE-617": "Reachable Assertion",
    "CWE-639": "Authorization Bypass via User-Controlled Key",
    "CWE-640": "Weak Password Recovery",
    "CWE-665": "Improper Initialization",
    "CWE-755": "Improper Exception Handling",
    "CWE-770": "Allocation of Resources Without Limits",
    "CWE-787": "Out-of-bounds Write",
    "CWE-843": "Type Confusion",
    "CWE-863": "Incorrect Authorization",
    "CWE-915": "Improperly Controlled Modification",
    "CWE-917": "Expression Language Injection",
    "CWE-918": "Server-Side Request Forgery",
    "CWE-1333": "Inefficient Regular Expression",
}


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "x"


def _build_nvd_json(spec: dict) -> dict:
    cve = spec["cve_id"]
    cwe = spec["expected_cwe"]
    cwe_name = _CWE_TO_CWE_NAME.get(cwe, "Other")
    vendor = spec["vendor"]
    product = spec["product"]
    affected = spec.get("affected_versions", [])
    fixed = spec.get("fixed_version", "")
    description = (
        f"{spec['vuln_name']}: {spec['audit_signal']}. "
        f"Affected: {vendor} {product} {','.join(affected) if affected else 'all'}. "
        f"Fix: upgrade to {fixed}."
    )
    cpe_part = "a"
    if spec["vuln_class"] in ("host", "container"):
        cpe_part = "o" if "kernel" in product.lower() else "a"
    cpe_vendor = _slug(vendor)
    cpe_product = _slug(product)
    return {
        "id": cve,
        "lastModified": "2026-05-14T00:00:00.000",
        "published": f"{spec.get('kev_date_added', '2024-01-01')}T00:00:00.000",
        "cisaExploitAdd": spec.get("kev_date_added", ""),
        "cisaActionDue": "",
        "cisaRequiredAction": "Apply vendor patches per advisory.",
        "cisaVulnerabilityName": spec["vuln_name"],
        "cveTags": [],
        "descriptions": [{"lang": "en", "value": description}],
        "weaknesses": [
            {
                "source": "nvd@nist.gov",
                "type": "Primary",
                "description": [{"lang": "en", "value": cwe}],
            }
        ],
        "configurations": [
            {
                "nodes": [
                    {
                        "operator": "OR",
                        "negate": False,
                        "cpeMatch": [
                            {
                                "vulnerable": True,
                                "criteria": (
                                    f"cpe:2.3:{cpe_part}:{cpe_vendor}:{cpe_product}:"
                                    f"*:*:*:*:*:*:*:*"
                                ),
                            }
                        ],
                    }
                ]
            }
        ],
        "metrics": {
            "cvssMetricV31": [
                {
                    "source": "nvd@nist.gov",
                    "type": "Primary",
                    "cvssData": {
                        "version": "3.1",
                        "vectorString": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                        "baseScore": 9.8,
                        "baseSeverity": "CRITICAL",
                        "attackVector": "NETWORK",
                        "attackComplexity": "LOW",
                        "privilegesRequired": "NONE",
                        "userInteraction": "NONE",
                        "scope": "UNCHANGED",
                        "confidentialityImpact": "HIGH",
                        "integrityImpact": "HIGH",
                        "availabilityImpact": "HIGH",
                    },
                    "exploitabilityScore": 3.9,
                    "impactScore": 5.9,
                }
            ]
        },
        "references": [
            {
                "url": f"https://nvd.nist.gov/vuln/detail/{cve}",
                "source": "nvd@nist.gov",
                "tags": ["Vendor Advisory"],
            },
            {
                "url": "https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
                "source": "cisa@dhs.gov",
                "tags": ["Third Party Advisory"],
            },
        ],
        "_cwe_name": cwe_name,
    }


def _build_cmdb_binding(spec: dict) -> dict:
    hosts = spec["host_pool"]
    fixed = spec.get("fixed_version", "")
    versions = {h: spec.get("affected_versions", ["*"])[0] if spec.get("affected_versions") else "*" for h in hosts}
    return {
        "cve_id": spec["cve_id"],
        "host_names": hosts,
        "product": spec["product"],
        "vendor": spec["vendor"],
        "host_versions": versions,
    }


def _build_recipe(spec: dict) -> dict:
    from demos.cve_remediation.scripts._install_map import _INSTALL_MAP

    cve = spec["cve_id"]
    vendor = spec["vendor"]
    product = spec["product"]
    audit = spec["audit_signal"]
    fixed = spec.get("fixed_version", "")
    affected = spec.get("affected_versions", [])
    affected_repr = ",".join(affected) if affected else "all"
    cwe = spec["expected_cwe"]
    vuln_class = spec["vuln_class"]

    imap = _INSTALL_MAP.get(cve)
    if not imap:
        # Fallback: CVE not in install map — use config_vendor stub
        imap = {
            "install_type": "config_vendor",
            "install_channel": "app",
            "package_name": _slug(product),
            "vulnerable_version": affected[0] if affected else "unknown",
            "fixed_version": fixed,
            "setup_cmd": f"mkdir -p /opt/apps/{_slug(product)}",
            "install_cmd": f"echo '{affected[0] if affected else 'unknown'}' > /opt/apps/{_slug(product)}/VERSION",
            "probe_cmd": f"cat /opt/apps/{_slug(product)}/VERSION 2>/dev/null | xargs -I{{}} echo 'Version: {{}}'",
            "fix_cmd": f"echo '{fixed}' > /opt/apps/{_slug(product)}/VERSION",
        }

    install_type = imap["install_type"]
    install_channel = imap["install_channel"]
    pkg_name = imap["package_name"]
    vuln_ver = imap["vulnerable_version"]
    fix_ver = imap["fixed_version"]
    setup_cmd = imap.get("setup_cmd", "")
    install_cmd = imap.get("install_cmd", "")
    probe_cmd = imap["probe_cmd"]
    fix_cmd = imap["fix_cmd"]

    setup = [setup_cmd] if setup_cmd else []
    state = [{"kind": "shell", "cmd": install_cmd}] if install_cmd else []

    description = (
        f"{cve}: {spec['vuln_name']}.\n"
        f"Affected: {vendor} {product} {affected_repr}.\n"
        f"Fixed in: {fixed}.\n"
        f"Audit signature: {audit}\n"
        f"CWE: {cwe} ({_CWE_TO_CWE_NAME.get(cwe, 'Other')}).\n"
        f"Install method: {install_type} via {install_channel}.\n"
        f"Package: {pkg_name} {vuln_ver} → {fix_ver}."
    )
    rationale = (
        f"Upgrade {vendor} {product} to {fix_ver} per vendor advisory "
        f"(NVD {cve}). Install channel: {install_channel}."
    )
    if imap.get("not_applicable_reason"):
        description += f"\nNOT APPLICABLE: {imap['not_applicable_reason']}"
        rationale = imap["not_applicable_reason"]

    return {
        "cve_id": cve,
        "vuln_class": vuln_class,
        "install_type": install_type,
        "install_channel": install_channel,
        "package_name": pkg_name,
        "vulnerable_version": vuln_ver,
        "fixed_version": fix_ver,
        "audit_signal": audit,
        "description": description,
        "setup": setup,
        "state": state,
        "probe": {"cmd": probe_cmd, "description": f"detect {cve} vulnerable {pkg_name} {vuln_ver}"},
        "fix": {"cmd": fix_cmd, "rationale": rationale},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="don't write files; report counts")
    ap.add_argument("--preserve-recipes", action="store_true",
                    help="skip recipe write if file already exists (keep authored v1 recipes)")
    args = ap.parse_args(argv)

    if not _SPEC.is_file():
        print(f"! spec not found: {_SPEC}", file=sys.stderr)
        return 2
    spec_doc = json.loads(_SPEC.read_text())
    cves: list[dict] = spec_doc["cves"]
    print(f"=== generate_v2_fixtures: {len(cves)} CVEs ===")

    if not args.dry_run:
        _NVD_DIR.mkdir(parents=True, exist_ok=True)
        _RECIPES_DIR.mkdir(parents=True, exist_ok=True)

    cmdb_existing = json.loads(_CMDB_SEED.read_text())
    existing_cves = {b["cve_id"] for b in cmdb_existing.get("bindings", [])}
    new_bindings = []
    nvd_written = 0
    recipe_written = 0

    for spec in cves:
        cve = spec["cve_id"]
        nvd_doc = _build_nvd_json(spec)
        nvd_path = _NVD_DIR / f"{cve}.json"
        if not args.dry_run:
            nvd_path.write_text(json.dumps(nvd_doc, indent=2) + "\n")
        nvd_written += 1

        recipe = _build_recipe(spec)
        recipe_path = _RECIPES_DIR / f"{cve}.yaml"
        if not args.dry_run:
            if args.preserve_recipes and recipe_path.exists():
                pass
            else:
                recipe_path.write_text(yaml.safe_dump(recipe, sort_keys=False))
        recipe_written += 1

        binding = _build_cmdb_binding(spec)
        if cve not in existing_cves:
            new_bindings.append(binding)
        else:
            # Replace existing binding with v2 spec
            cmdb_existing["bindings"] = [
                b for b in cmdb_existing["bindings"] if b["cve_id"] != cve
            ]
            new_bindings.append(binding)

    # Build ground_truth — v2 entries replace v1 by cve_id; preserve untouched v1 entries.
    truth_existing = json.loads(_TRUTH_PATH.read_text()) if _TRUTH_PATH.exists() else {"cves": [], "hosts": [], "schema_version": "1"}
    truth_existing_cves = {c["cve_id"]: c for c in truth_existing.get("cves", [])}
    for spec in cves:
        cve = spec["cve_id"]
        hosts = spec["host_pool"]
        recipe = _build_recipe(spec)
        truth_existing_cves[cve] = {
            "cve_id": cve,
            "vuln_class": spec["vuln_class"],
            "vendor": spec["vendor"],
            "product": spec["product"],
            "cmdb_cis": hosts,
            "topo_nodes": hosts,
            "in_cmdb": True,
            "in_topology": True,
            "install_type": recipe.get("install_type", "config_file"),
            "audit_signal": spec.get("audit_signal", ""),
            "expected_probe": recipe["probe"]["cmd"],
            "expected_fix": recipe["fix"]["cmd"],
            "recipe_authored": True,
        }
    truth_existing["cves"] = list(truth_existing_cves.values())
    if not args.dry_run:
        _TRUTH_PATH.write_text(json.dumps(truth_existing, indent=2, sort_keys=True) + "\n")

    cmdb_existing.setdefault("bindings", []).extend(new_bindings)
    # Dedup by cve_id (last wins — our v2 bindings)
    seen = set()
    deduped = []
    for b in reversed(cmdb_existing["bindings"]):
        if b["cve_id"] in seen:
            continue
        seen.add(b["cve_id"])
        deduped.append(b)
    cmdb_existing["bindings"] = list(reversed(deduped))

    if not args.dry_run:
        _CMDB_SEED.write_text(json.dumps(cmdb_existing, indent=2) + "\n")

    print(f"nvd written: {nvd_written}")
    print(f"recipes written: {recipe_written}")
    print(f"cmdb bindings (post-merge total): {len(cmdb_existing['bindings'])}")
    print(f"new bindings added/replaced: {len(new_bindings)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
