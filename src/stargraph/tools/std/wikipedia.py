# SPDX-License-Identifier: Apache-2.0
"""``std.wikipedia`` -- Wikipedia article search via the public REST API.

Hits ``https://<lang>.wikipedia.org/w/rest.php/v1/search/page`` (no API
key) and returns title / description / plain-text excerpt / canonical URL
per hit. The ``lang`` subdomain is validated against a strict pattern so
a crafted value cannot redirect the request off-wiki.
"""

from __future__ import annotations

import re
from typing import Any, cast
from urllib.parse import quote

import httpx

from stargraph.errors import StargraphRuntimeError
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects
from stargraph.tools.std import _http
from stargraph.tools.std.fetch_page import strip_tags

__all__ = ["wikipedia"]

_MAX_LIMIT = 20
_LANG_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$")


@tool(
    name="wikipedia",
    namespace="std",
    version="1",
    side_effects=SideEffects.read,
    description=(
        "Search Wikipedia and return matching articles (title, description, "
        "excerpt, URL). No API key required."
    ),
)
async def wikipedia(
    query: str,
    lang: str = "en",
    limit: int = 3,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Search ``query``; return ``{query, results: [{title, description, excerpt, url}]}``."""
    if not _LANG_RE.match(lang):
        raise StargraphRuntimeError(
            f"invalid Wikipedia language code {lang!r}",
            lang=lang,
        )
    base = f"https://{lang}.wikipedia.org"
    try:
        async with _http.build_client(timeout_s) as client:
            response = await client.get(
                f"{base}/w/rest.php/v1/search/page",
                params={"q": query, "limit": max(1, min(limit, _MAX_LIMIT))},
            )
            response.raise_for_status()
            payload: Any = response.json()
    except httpx.HTTPError as exc:
        raise StargraphRuntimeError(f"wikipedia search failed: {exc}", query=query) from exc
    # isinstance narrows Any -> dict[Unknown, Unknown]; re-widen via cast.
    payload_map = cast("dict[str, Any]", payload) if isinstance(payload, dict) else {}
    pages = cast("list[Any]", payload_map.get("pages", []))
    results: list[dict[str, Any]] = []
    for page_any in pages:
        if not isinstance(page_any, dict):
            continue
        page = cast("dict[str, Any]", page_any)
        key = str(page.get("key", ""))
        results.append(
            {
                "title": str(page.get("title", "")),
                "description": str(page.get("description") or ""),
                "excerpt": strip_tags(str(page.get("excerpt") or "")),
                "url": f"{base}/wiki/{quote(key)}" if key else "",
            }
        )
    return {"query": query, "results": results}
