# SPDX-License-Identifier: Apache-2.0
"""Real EPSS + KEV feed fetchers with on-disk replay cache.

Replaces the per-CVE fixture lookup that ``_EnrichBase`` previously used
to populate ``epss_score_bp`` / ``kev_listed`` on the extract.

Design contract (per CRITERIA.md step 1 + the "no cheats" rule):

* The data comes from real upstream feeds, not a hand-curated fixture.
  - EPSS  : ``https://epss.cyentia.com/epss_scores-current.csv.gz``
  - KEV   : ``https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json``
* Each feed is cached on disk (under ``$HARBOR_CACHE_ROOT`` -- defaults
  to ``.harbor/cache``) so demo replays / tests / offline runs reuse a
  previously-captured snapshot.
* TTL: a cache file younger than ``_TTL_SECONDS`` is used directly. On
  miss / stale we attempt a refresh; if the refresh fails we fall back
  to the stale cache (replay mode) so a flaky NVD-side outage doesn't
  break a previously-successful run.
* Fail-loud: if there is no cache at all AND the feed is unreachable we
  raise ``HarborRuntimeError`` instead of silently returning a default.
  The caller surfaces the error via ``state.last_intake_error`` and the
  pipeline routes to quarantine. This is the explicit user preference --
  honest failure beats fake-pass.
* CVE-not-in-feed is NOT an error. EPSS only scores CVEs FIRST has seen;
  KEV only lists CVEs CISA has marked actively exploited. Absence is a
  real "no data" signal and is returned as ``None`` (EPSS) or ``False``
  (KEV).

Both fetchers parse their respective feeds once per process and memoise
the parsed lookup table in module-level dicts; subsequent ``cve_id``
lookups are O(1) without re-reading the cache file.
"""

from __future__ import annotations

import asyncio
import csv
import gzip
import io
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from harbor.errors import HarborRuntimeError

__all__ = [
    "fetch_epss_score",
    "fetch_kev_listed",
    "reset_cache",
]


_EPSS_URL = os.environ.get(
    "EPSS_FEED_URL",
    "https://epss.cyentia.com/epss_scores-current.csv.gz",
)
_KEV_URL = os.environ.get(
    "KEV_FEED_URL",
    "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
)

_CACHE_ROOT = Path(os.environ.get("HARBOR_CACHE_ROOT", ".harbor/cache"))
_EPSS_CACHE = _CACHE_ROOT / "epss_current.csv.gz"
_KEV_CACHE = _CACHE_ROOT / "kev_current.json"

_TTL_SECONDS = int(os.environ.get("HARBOR_FEED_TTL_S", str(24 * 3600)))
_HTTP_TIMEOUT_S = float(os.environ.get("HARBOR_FEED_TIMEOUT_S", "30.0"))

_FETCH_LOCK = asyncio.Lock()
_EPSS_TABLE: dict[str, int] | None = None
_KEV_TABLE: dict[str, bool] | None = None


def reset_cache() -> None:
    """Drop in-process parsed tables. Tests use this between runs."""
    global _EPSS_TABLE, _KEV_TABLE
    _EPSS_TABLE = None
    _KEV_TABLE = None


def _is_fresh(p: Path) -> bool:
    if not p.is_file():
        return False
    age = time.time() - p.stat().st_mtime
    return age < _TTL_SECONDS


async def _http_get_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "harbor-cve-rem/1.0"})
        resp.raise_for_status()
        return resp.content


async def _refresh_cache(url: str, target: Path) -> bytes:
    """Fetch ``url``, write atomically to ``target``, return bytes."""
    body = await _http_get_bytes(url)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(body)
    tmp.replace(target)
    return body


async def _load_feed_bytes(url: str, cache_path: Path) -> bytes:
    """Fresh-cache → use it. Stale/missing → refresh; fall back to stale; else raise."""
    if _is_fresh(cache_path):
        return cache_path.read_bytes()
    try:
        return await _refresh_cache(url, cache_path)
    except (httpx.HTTPError, OSError) as exc:
        if cache_path.is_file():
            return cache_path.read_bytes()
        raise HarborRuntimeError(
            f"feed unreachable and no cache present at {cache_path}: "
            f"{type(exc).__name__}: {exc}",
            feed_url=url,
            cache_path=str(cache_path),
        ) from exc


def _parse_epss_csv(blob: bytes) -> dict[str, int]:
    """Parse FIRST EPSS CSV → ``{cve_id_upper: epss_basis_points}``.

    Format::

        #model_version:v202x.x,score_date:YYYY-MM-DDTHH:mm:ss+0000
        cve,epss,percentile
        CVE-2021-44228,0.97559,0.99986
        ...

    EPSS score is a probability in [0, 1]; we store basis-points
    (``round(score * 10000)``) to keep the rest of the pipeline FR-4
    safe (no floats in the hashed payload).
    """
    text = gzip.decompress(blob).decode("utf-8")
    out: dict[str, int] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if line.startswith("cve,"):
            continue  # header
        parts = line.split(",")
        if len(parts) < 2:
            continue
        cve_id = parts[0].strip().upper()
        try:
            epss = float(parts[1].strip())
        except ValueError:
            continue
        out[cve_id] = int(round(epss * 10000))
    return out


def _parse_kev_json(blob: bytes) -> dict[str, bool]:
    """Parse CISA KEV catalog → ``{cve_id_upper: True}`` for every listed CVE."""
    envelope = json.loads(blob.decode("utf-8"))
    out: dict[str, bool] = {}
    for v in envelope.get("vulnerabilities", []) or []:
        cid = str(v.get("cveID") or "").strip().upper()
        if cid:
            out[cid] = True
    return out


async def _ensure_epss_table() -> dict[str, int]:
    global _EPSS_TABLE
    if _EPSS_TABLE is not None:
        return _EPSS_TABLE
    async with _FETCH_LOCK:
        if _EPSS_TABLE is not None:
            return _EPSS_TABLE
        blob = await _load_feed_bytes(_EPSS_URL, _EPSS_CACHE)
        _EPSS_TABLE = _parse_epss_csv(blob)
    return _EPSS_TABLE


async def _ensure_kev_table() -> dict[str, bool]:
    global _KEV_TABLE
    if _KEV_TABLE is not None:
        return _KEV_TABLE
    async with _FETCH_LOCK:
        if _KEV_TABLE is not None:
            return _KEV_TABLE
        blob = await _load_feed_bytes(_KEV_URL, _KEV_CACHE)
        _KEV_TABLE = _parse_kev_json(blob)
    return _KEV_TABLE


async def fetch_epss_score(cve_id: str) -> int | None:
    """Return EPSS basis-points for ``cve_id`` or ``None`` if not scored.

    Raises ``HarborRuntimeError`` only when the feed is unreachable AND
    no cache exists. CVE-not-in-feed → ``None`` (legitimate "no score
    yet" -- EPSS only scores CVEs FIRST has data on).
    """
    if not cve_id or not cve_id.strip():
        return None
    table = await _ensure_epss_table()
    return table.get(cve_id.strip().upper())


async def fetch_kev_listed(cve_id: str) -> bool:
    """Return ``True`` iff ``cve_id`` is in the live CISA KEV catalog.

    Raises ``HarborRuntimeError`` only when the feed is unreachable AND
    no cache exists. CVE-not-in-catalog → ``False`` (KEV is exhaustive
    for "actively exploited" by definition; absence == not exploited).
    """
    if not cve_id or not cve_id.strip():
        return False
    table = await _ensure_kev_table()
    return table.get(cve_id.strip().upper(), False)
