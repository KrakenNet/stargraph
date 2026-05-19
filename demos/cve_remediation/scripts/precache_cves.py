# SPDX-License-Identifier: Apache-2.0
"""Pre-cache 100 NVD CVE JSONs to ``fixtures/nvd/<cve>.json``.

Reads ``fixtures/scoring_cves_v1.json`` (output of
``curate_scoring_cves``), fetches each CVE's full NVD JSON via direct
HTTP (matches what ``demos.cve_remediation.tools.fetch_advisory`` calls
under the hood), writes the upstream payload verbatim. The scoring run
loop reads the cache instead of re-hitting NVD 100 times.

Pacing: NVD allows 5 req / 30s w/o API key, 50 req / 30s with one.
We default to 1 req / 6s (5 / 30s) when no key is set, 1 req / 0.6s
otherwise. Override via ``CVE_REM_NVD_PACE_S``.

Idempotent: skips entries whose cache file already exists.

Run::

    set -a; source demos/cve_remediation/.env; set +a
    uv run --no-project python -m demos.cve_remediation.scripts.precache_cves
    # force re-fetch:
    uv run --no-project python -m demos.cve_remediation.scripts.precache_cves --force
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_DEMO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_FIXTURE = _DEMO_ROOT / "fixtures" / "scoring_cves_v1.json"
_DEFAULT_CACHE_DIR = _DEMO_ROOT / "fixtures" / "nvd"
_NVD_BASE = os.environ.get(
    "NVD_BASE_URL",
    "https://services.nvd.nist.gov/rest/json/cves/2.0",
)
_API_KEY = os.environ.get("NVD_API_KEY", "").strip()
_DEFAULT_PACE_S = float(
    os.environ.get(
        "CVE_REM_NVD_PACE_S",
        "0.6" if _API_KEY else "6.0",
    )
)


def _fetch(cve_id: str, *, timeout: float = 15.0) -> dict:
    url = f"{_NVD_BASE}?cveId={cve_id}"
    req = urllib.request.Request(url)
    if _API_KEY:
        req.add_header("apiKey", _API_KEY)
    req.add_header("User-Agent", "harbor-cve-remediation-precache/1.0")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 -- public NVD endpoint
        body = json.load(resp)
    items = body.get("vulnerabilities") or []
    if not items:
        raise RuntimeError(f"NVD returned 0 vulnerabilities for {cve_id}")
    cve = items[0].get("cve")
    if not cve:
        raise RuntimeError(f"NVD response missing 'cve' for {cve_id}")
    return cve


def main() -> int:
    ap = argparse.ArgumentParser(description="precache NVD CVE JSONs")
    ap.add_argument("--fixture", type=Path, default=_DEFAULT_FIXTURE)
    ap.add_argument("--cache-dir", type=Path, default=_DEFAULT_CACHE_DIR)
    ap.add_argument("--force", action="store_true",
                    help="re-fetch even if cache file exists")
    ap.add_argument("--pace", type=float, default=_DEFAULT_PACE_S,
                    help="seconds between requests "
                         f"(default {_DEFAULT_PACE_S}; "
                         f"NVD_API_KEY {'present' if _API_KEY else 'absent'})")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap total CVEs fetched (0=all)")
    args = ap.parse_args()

    payload = json.loads(args.fixture.read_text())
    cves = payload.get("cves") or []
    if args.limit:
        cves = cves[: args.limit]
    args.cache_dir.mkdir(parents=True, exist_ok=True)

    total = len(cves)
    fetched = 0
    skipped = 0
    failed: list[tuple[str, str]] = []
    print(f"=== precache: {total} CVEs, pace={args.pace}s, "
          f"api_key={'yes' if _API_KEY else 'no'} ===")
    start = time.monotonic()

    for i, row in enumerate(cves, 1):
        cve_id = row["cve_id"]
        out = args.cache_dir / f"{cve_id}.json"
        if out.exists() and not args.force:
            skipped += 1
            print(f"  [{i:3d}/{total}] SKIP  {cve_id} (cached)")
            continue
        try:
            cve_json = _fetch(cve_id)
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as exc:
            failed.append((cve_id, f"{type(exc).__name__}: {exc}"))
            print(f"  [{i:3d}/{total}] FAIL  {cve_id}: {exc}")
            time.sleep(args.pace)
            continue
        out.write_text(json.dumps(cve_json, indent=2, sort_keys=True))
        fetched += 1
        print(f"  [{i:3d}/{total}] OK    {cve_id} -> {out.name} "
              f"({out.stat().st_size} bytes)")
        # Pace AFTER successful request, except on last iteration.
        if i < total:
            time.sleep(args.pace)

    elapsed = time.monotonic() - start
    print(f"\n=== summary: fetched={fetched} skipped={skipped} "
          f"failed={len(failed)} elapsed={elapsed:.1f}s ===")
    if failed:
        print("\n! failures:")
        for cve_id, err in failed:
            print(f"    {cve_id}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
