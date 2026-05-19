# SPDX-License-Identifier: Apache-2.0
"""Tier-2 retrieval-grounded planning support.

For each CVE the planner is reasoning about, we pull a small number of
authoritative reference URLs (NVD ``references`` -- vendor advisories,
GitHub security issues, etc.), strip HTML to readable text, and inject
the extracted bodies as numbered sources in the LM prompt. The planner
is then required to cite from this source set; the citation verifier
catches claims that don't appear in any cited source -- a signal that
the LM is drawing on training data rather than the live advisory.

Cache layout: ``$HARBOR_CACHE_ROOT/rag/<sha256(url)[:16]>.txt``. TTL is
24h by default. Fail-loud: if zero sources land for a CVE, the caller
treats this as a verifier finding and escalates -- but a single failed
URL doesn't kill the run (we still try the rest).
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx

__all__ = ["fetch_rag_sources", "RagSource"]


_CACHE_ROOT = Path(
    os.environ.get("HARBOR_CACHE_ROOT", ".harbor/cache")
) / "rag"
_TTL_S = int(os.environ.get("HARBOR_RAG_TTL_S", str(24 * 3600)))
_TIMEOUT_S = float(os.environ.get("HARBOR_RAG_TIMEOUT_S", "10.0"))
_MAX_BODY_CHARS = int(os.environ.get("HARBOR_RAG_BODY_CHARS", "4000"))
_DEFAULT_MAX_N = int(os.environ.get("HARBOR_RAG_MAX_SOURCES", "3"))


class RagSource(dict[str, str]):
    """Typed alias -- a source dict with ``index``, ``url``, ``body``."""


class _TextExtractor(HTMLParser):
    """Minimal HTML-to-text extractor.

    Skips ``<script>``/``<style>`` content; collapses whitespace at
    serialization. Avoids pulling a heavyweight dep (BeautifulSoup) for
    the demo.
    """

    _SKIP_TAGS = {"script", "style", "noscript", "head", "meta", "link"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._chunks.append(data)

    def text(self) -> str:
        joined = " ".join(self._chunks)
        # Collapse whitespace runs, keep single spaces.
        return re.sub(r"\s+", " ", html.unescape(joined)).strip()


def _is_fresh(p: Path) -> bool:
    if not p.is_file():
        return False
    return (time.time() - p.stat().st_mtime) < _TTL_S


def _cache_path(url: str) -> Path:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return _CACHE_ROOT / f"{h}.txt"


async def _fetch_one(client: httpx.AsyncClient, url: str) -> str:
    """Fetch ``url``, return extracted plain-text body. Empty string on failure.

    Caches the extracted text (not the raw HTML) so re-runs skip the
    parse step. We ignore non-2xx responses, redirect bodies, and
    unsupported content-types -- bad sources contribute nothing rather
    than blowing up the run.
    """
    target = _cache_path(url)
    if _is_fresh(target):
        return target.read_text(encoding="utf-8", errors="replace")
    try:
        resp = await client.get(
            url,
            timeout=_TIMEOUT_S,
            follow_redirects=True,
            headers={"User-Agent": "harbor-cve-rem-rag/1.0"},
        )
        if resp.status_code >= 400:
            return ""
        ct = resp.headers.get("content-type", "")
        body = resp.text or ""
        if "html" in ct.lower():
            ex = _TextExtractor()
            ex.feed(body)
            extracted = ex.text()
        elif "json" in ct.lower() or "text" in ct.lower():
            extracted = body
        else:
            extracted = ""
        extracted = extracted[:_MAX_BODY_CHARS]
        if extracted:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(extracted, encoding="utf-8")
        return extracted
    except (httpx.HTTPError, OSError):
        return ""


async def fetch_rag_sources(
    *,
    cve_id: str,
    references: list[Any],
    max_n: int = _DEFAULT_MAX_N,
) -> list[RagSource]:
    """Fetch + extract up to ``max_n`` authoritative sources for ``cve_id``.

    Inputs:
        ``references`` is the NVD ``cve.references`` list (already
        normalized by ``fetch_advisory`` to ``[{"url": str, "tags":
        list[str]}, ...]``). We prefer entries tagged ``Vendor
        Advisory``/``Patch``/``Mitigation`` but fall back to the head
        of the list when the tag mix is sparse.

    Returns: list of ``{"index": str(1..N), "url": str, "body": str}``
    -- only sources that actually returned non-empty text. A successful
    return with zero sources is itself a meaningful signal (the caller
    can record a finding and escalate to multi-turn).
    """
    if not cve_id or not references:
        return []
    # Priority order for picking which references to fetch.
    PREFERRED_TAGS = ("Vendor Advisory", "Patch", "Mitigation", "Third Party Advisory")
    scored: list[tuple[int, str]] = []
    for r in references:
        if not isinstance(r, dict):
            continue
        url = str(r.get("url") or "").strip()
        tags = [str(t) for t in (r.get("tags") or [])]
        if not url or not url.startswith(("http://", "https://")):
            continue
        score = 0
        for i, pref in enumerate(PREFERRED_TAGS):
            if pref in tags:
                score = max(score, len(PREFERRED_TAGS) - i)
        scored.append((score, url))
    scored.sort(key=lambda x: x[0], reverse=True)
    candidates = [u for _, u in scored][: max_n * 2]  # over-fetch; some fail
    if not candidates:
        return []

    sources: list[RagSource] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT_S, follow_redirects=True) as client:
        # Fan-out fetches concurrently; cap at max_n successful.
        coros = [_fetch_one(client, url) for url in candidates]
        bodies = await asyncio.gather(*coros, return_exceptions=False)
    for url, body in zip(candidates, bodies):
        if not body:
            continue
        sources.append(
            RagSource(
                index=str(len(sources) + 1),
                url=url,
                body=body,
            )
        )
        if len(sources) >= max_n:
            break
    return sources
