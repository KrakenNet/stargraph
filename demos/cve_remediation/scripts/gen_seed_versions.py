# SPDX-License-Identifier: Apache-2.0
"""Augment ``h11_cmdb_seed.json`` with per-binding ``host_versions``.

For every binding (``cve_id``, ``product``, ``vendor``, ``host_names``),
read the matching ``fixtures/nvd/{cve_id}.json``, extract a representative
vulnerable version from the first ``vulnerable`` ``cpeMatch`` row whose
CPE vendor matches the binding's vendor, and assign it to every host in
``host_names`` under a new ``host_versions`` map::

    {"h11-rtr-01": "15.6.0", "h11-sw-03": "15.6.0"}

The chosen version satisfies the NVD range for THAT CVE. When the same
Software CI is shared across multiple CVEs (e.g. ``Cisco IOS and IOS XE``
appears in CVE-2017-3881 and CVE-2018-0171 with disjoint host bindings),
each binding's hosts get the version that matches *its* CVE's range, so
the downstream version filter selects only the correct hosts per CVE.

Rules
-----
* ``exact_affected_versions`` (literal CPE versions) → first literal.
* ``versionStartIncluding`` + ``versionEndExcluding`` → ``versionStartIncluding``.
* Only ``versionEndExcluding`` → that string minus a trailing ``.0`` bump.
* Only ``versionEndIncluding`` → ``versionEndIncluding``.
* All four fields empty (wildcard) → ``"*"`` (downstream filter treats
  ``"*"`` as "unconstrained — keep the host").

Idempotent: replaces ``host_versions`` only when missing or empty.

Run::

    uv run --no-project python -m demos.cve_remediation.scripts.gen_seed_versions
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_DEMO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = _DEMO_ROOT / "fixtures"
_SEED_PATH = _FIXTURES / "h11_cmdb_seed.json"
_NVD_DIR = _FIXTURES / "nvd"

_CPE_RE = re.compile(r"^cpe:2\.3:[aoh]:(?P<vendor>[^:]+):(?P<product>[^:]+):(?P<version>[^:]+):")


def _pick_vuln_row(nvd: dict, vendor_hint: str) -> dict | None:
    """Return the first ``cpeMatch`` row whose CPE vendor token matches
    ``vendor_hint`` (case-folded, underscore-spaced). If no vendor match,
    fall back to the first vulnerable row regardless of vendor.
    """
    vh = (vendor_hint or "").strip().lower().replace(" ", "_").replace("-", "_")
    fallback: dict | None = None
    for cfg in nvd.get("configurations") or []:
        for node in cfg.get("nodes") or []:
            for m in node.get("cpeMatch") or []:
                if not m.get("vulnerable"):
                    continue
                if fallback is None:
                    fallback = m
                criteria = str(m.get("criteria") or "")
                mt = _CPE_RE.match(criteria)
                if not mt:
                    continue
                cpe_vendor = mt.group("vendor").lower()
                if vh and (cpe_vendor == vh or cpe_vendor in vh or vh in cpe_vendor):
                    return m
    return fallback


def _exact_version(m: dict) -> str:
    """Extract a literal version from the CPE 2.3 URI when not ``-`` / ``*``."""
    mt = _CPE_RE.match(str(m.get("criteria") or ""))
    if not mt:
        return ""
    v = mt.group("version").strip()
    if v in ("", "-", "*"):
        return ""
    return v


def _bump_down(v: str) -> str:
    """Return a version slightly below ``v`` (for ``versionEndExcluding``).

    Decrements the trailing numeric segment by 1 when possible
    (``2.4.60`` → ``2.4.59``); otherwise falls back to ``v`` unchanged.
    NVD's ``versionEndExcluding`` is exclusive; choosing one tick below
    keeps the install version inside the affected range.
    """
    parts = re.split(r"([._\-])", v)
    for i in range(len(parts) - 1, -1, -1):
        seg = parts[i]
        if seg.isdigit():
            n = int(seg)
            if n > 0:
                parts[i] = str(n - 1)
                return "".join(parts)
            break
    return v


def _derive_install_version(nvd: dict, vendor_hint: str) -> str:
    m = _pick_vuln_row(nvd, vendor_hint)
    if not m:
        return "*"
    exact = _exact_version(m)
    if exact:
        return exact
    start_inc = m.get("versionStartIncluding") or ""
    end_exc = m.get("versionEndExcluding") or ""
    end_inc = m.get("versionEndIncluding") or ""
    if start_inc:
        return start_inc
    if end_exc:
        return _bump_down(end_exc)
    if end_inc:
        return end_inc
    return "*"


def main() -> int:
    if not _SEED_PATH.exists():
        print(f"! seed missing: {_SEED_PATH}", file=sys.stderr)
        return 2
    seed = json.loads(_SEED_PATH.read_text())
    bindings = seed.get("bindings") or []
    augmented = unchanged = 0
    for b in bindings:
        if b.get("host_versions"):
            unchanged += 1
            continue
        cve_id = b.get("cve_id") or ""
        vendor = b.get("vendor") or ""
        nvd_path = _NVD_DIR / f"{cve_id}.json"
        if not nvd_path.exists():
            print(f"  [skip ] {cve_id}: no NVD fixture")
            b["host_versions"] = {h: "*" for h in (b.get("host_names") or [])}
            continue
        try:
            nvd = json.loads(nvd_path.read_text())
        except Exception as exc:  # noqa: BLE001
            print(f"  [err  ] {cve_id}: {exc}")
            b["host_versions"] = {h: "*" for h in (b.get("host_names") or [])}
            continue
        version = _derive_install_version(nvd, vendor)
        b["host_versions"] = {h: version for h in (b.get("host_names") or [])}
        augmented += 1
        print(f"  [bind ] {cve_id:18} vendor={vendor:14} hosts={len(b['host_names'])} v={version}")
    _SEED_PATH.write_text(json.dumps(seed, indent=2) + "\n")
    print(f"\nwrote {_SEED_PATH}  augmented={augmented}  unchanged={unchanged}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
