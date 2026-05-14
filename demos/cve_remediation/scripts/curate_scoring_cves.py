# SPDX-License-Identifier: Apache-2.0
"""Curate 100 CVEs across vuln_class buckets for the scoring run.

Pulls the live CISA Known-Exploited-Vulnerabilities (KEV) catalog and
buckets entries by ``vendorProject`` + ``vulnerabilityName`` keywords
into the four sandbox-runtime buckets that drive
``demos/cve_remediation/graph/real_nodes.py:SandboxDispatchNode``:

* **cargonet** -- network gear (Cisco/Juniper/F5/Palo Alto/Fortinet/etc).
  Maps to ``cargonet_lab`` runtime.
* **docker** -- application/library/web-framework on commodity OS.
  Maps to ``docker_compose`` runtime.
* **static** -- TLS/cipher/ACL/config-only CVEs (no probe needed).
  Maps to ``static_detection`` runtime.
* **hitl** -- logic-flaw / business-rule / authn-bypass CVEs that the
  sandbox cannot meaningfully probe -- forces HITL gate.

Output (deterministic per seed): ``fixtures/scoring_cves_v1.json`` with
one row per CVE: ``{cve_id, vuln_class, vendor, product, vuln_name,
known_exploited: true, kev_date_added}``. Pre-cache step downloads
NVD JSON per cve_id; the curator does NOT hit NVD.

Run::

    uv run --no-project python -m demos.cve_remediation.scripts.curate_scoring_cves
    uv run --no-project python -m demos.cve_remediation.scripts.curate_scoring_cves \\
        --seed 42 --out fixtures/scoring_cves_v1.json
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import urllib.request
from pathlib import Path

_DEMO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_OUT = _DEMO_ROOT / "fixtures" / "scoring_cves_v1.json"
_KEV_URL = (
    "https://www.cisa.gov/sites/default/files/feeds/"
    "known_exploited_vulnerabilities.json"
)

# Vendor-string substring -> bucket. First match wins; ordered by
# specificity. Lower-cased substring match against
# ``vendorProject + " " + product``.
_BUCKETS_BY_VENDOR: list[tuple[str, str]] = [
    # cargonet — network gear
    ("cisco", "cargonet"),
    ("juniper", "cargonet"),
    ("palo alto", "cargonet"),
    ("fortinet", "cargonet"),
    ("f5", "cargonet"),
    ("citrix", "cargonet"),
    ("netscaler", "cargonet"),
    ("sonicwall", "cargonet"),
    ("ivanti", "cargonet"),
    ("pulse secure", "cargonet"),
    ("zyxel", "cargonet"),
    ("d-link", "cargonet"),
    ("netgear", "cargonet"),
    ("mikrotik", "cargonet"),
    ("aruba", "cargonet"),
    ("ruckus", "cargonet"),
    ("arcadyan", "cargonet"),
    ("draytek", "cargonet"),
    ("qnap", "cargonet"),
    ("synology", "cargonet"),
    # docker — app / library / web-framework on commodity OS
    ("microsoft", "docker"),
    ("apache", "docker"),
    ("adobe", "docker"),
    ("oracle", "docker"),
    ("atlassian", "docker"),
    ("gitlab", "docker"),
    ("vmware", "docker"),
    ("mongodb", "docker"),
    ("linux", "docker"),
    ("apple", "docker"),
    ("google", "docker"),
    ("samba", "docker"),
    ("php", "docker"),
    ("wordpress", "docker"),
    ("drupal", "docker"),
    ("jenkins", "docker"),
    ("jboss", "docker"),
    ("kibana", "docker"),
    ("elasticsearch", "docker"),
    ("openssl", "docker"),
]

# vulnerabilityName keyword -> bucket (override). Authn/authz logic
# flaws go to HITL; cipher/TLS/ACL goes to static.
_BUCKETS_BY_KEYWORD: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bauthent(?:ication)?\s+bypass\b", re.I), "hitl"),
    (re.compile(r"\bauthor(?:ization|ised)?\s+(?:bypass|flaw)\b", re.I), "hitl"),
    (re.compile(r"\bprivilege\s+escalation\b", re.I), "hitl"),
    (re.compile(r"\binsecure\s+default\b", re.I), "hitl"),
    (re.compile(r"\bcipher\b", re.I), "static"),
    (re.compile(r"\btls\b", re.I), "static"),
    (re.compile(r"\bssl\b", re.I), "static"),
    (re.compile(r"\bcertif", re.I), "static"),
    (re.compile(r"\bweak\s+(?:hash|crypto|random)", re.I), "static"),
    (re.compile(r"\bmisconfig", re.I), "static"),
    (re.compile(r"\bdefault\s+credential", re.I), "hitl"),
    (re.compile(r"\binformation\s+disclosure\b", re.I), "static"),
]

_BUCKET_QUOTAS = {"cargonet": 30, "docker": 35, "static": 15, "hitl": 20}


def _classify(entry: dict) -> str | None:
    name = (entry.get("vulnerabilityName") or "")
    for pat, bucket in _BUCKETS_BY_KEYWORD:
        if pat.search(name):
            return bucket
    haystack = (
        f"{entry.get('vendorProject', '')} {entry.get('product', '')}"
    ).lower()
    for needle, bucket in _BUCKETS_BY_VENDOR:
        if needle in haystack:
            return bucket
    return None  # unclassifiable


def _fetch_kev(url: str) -> dict:
    print(f"fetching KEV catalog: {url}")
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 -- public CISA endpoint
        return json.load(resp)


def _sample(
    pool: list[dict], quota: int, rng: random.Random,
) -> list[dict]:
    if len(pool) <= quota:
        print(f"  ! pool size {len(pool)} <= quota {quota}; taking all")
        return pool
    return rng.sample(pool, quota)


def _normalize(entry: dict, vuln_class: str) -> dict:
    return {
        "cve_id": entry["cveID"],
        "vuln_class": vuln_class,
        "vendor": entry.get("vendorProject", ""),
        "product": entry.get("product", ""),
        "vuln_name": entry.get("vulnerabilityName", ""),
        "known_exploited": True,
        "kev_date_added": entry.get("dateAdded", ""),
        "ransomware_use": entry.get("knownRansomwareCampaignUse", "Unknown"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="curate 100 CVEs for scoring")
    ap.add_argument("--seed", type=int, default=20260507)
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    ap.add_argument("--kev-url", default=_KEV_URL)
    ap.add_argument(
        "--kev-cache", type=Path,
        help="optional local KEV JSON path (skip fetch if present)",
    )
    args = ap.parse_args()

    if args.kev_cache and args.kev_cache.exists():
        kev = json.loads(args.kev_cache.read_text())
        print(f"loaded KEV from cache: {args.kev_cache} "
              f"({kev.get('count', '?')} entries)")
    else:
        kev = _fetch_kev(args.kev_url)
        if args.kev_cache:
            args.kev_cache.write_text(json.dumps(kev))

    entries = kev.get("vulnerabilities") or []
    buckets: dict[str, list[dict]] = {k: [] for k in _BUCKET_QUOTAS}
    unclassified = 0
    for e in entries:
        b = _classify(e)
        if b is None:
            unclassified += 1
            continue
        buckets[b].append(e)

    print("\n=== bucket sizes (pre-sample) ===")
    for k in _BUCKET_QUOTAS:
        print(f"  {k:8} : pool={len(buckets[k]):4d}  quota={_BUCKET_QUOTAS[k]}")
    print(f"  unclassified: {unclassified}")

    rng = random.Random(args.seed)
    selected: list[dict] = []
    for bucket, quota in _BUCKET_QUOTAS.items():
        sample = _sample(buckets[bucket], quota, rng)
        for e in sample:
            selected.append(_normalize(e, bucket))

    selected.sort(key=lambda r: (r["vuln_class"], r["cve_id"]))
    if len(selected) != sum(_BUCKET_QUOTAS.values()):
        print(f"\n! got {len(selected)}, expected "
              f"{sum(_BUCKET_QUOTAS.values())}")

    payload = {
        "schema_version": "1.0",
        "kev_catalog_version": kev.get("catalogVersion"),
        "kev_date_released": kev.get("dateReleased"),
        "seed": args.seed,
        "bucket_quotas": _BUCKET_QUOTAS,
        "count": len(selected),
        "cves": selected,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True))
    print(f"\nwrote {len(selected)} CVEs -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
