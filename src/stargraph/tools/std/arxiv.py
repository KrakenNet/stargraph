# SPDX-License-Identifier: Apache-2.0
"""``std.arxiv`` -- arXiv paper search via the public Atom API.

Queries ``https://export.arxiv.org/api/query`` (no API key) and parses
the Atom feed with :mod:`xml.etree.ElementTree`. XXE / billion-laughs
hardening: any document carrying a DTD (``<!DOCTYPE``/``<!ENTITY``) is
rejected before parsing -- the same forbid-DTD rule defusedxml applies,
and a legitimate Atom feed never declares one.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import httpx

from stargraph.errors import StargraphRuntimeError
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects
from stargraph.tools.std import _http

__all__ = ["arxiv_search"]

_API_URL = "https://export.arxiv.org/api/query"
_MAX_RESULTS = 25
_ATOM = "{http://www.w3.org/2005/Atom}"


def _entry_to_result(entry: ET.Element) -> dict[str, Any]:
    def text_of(tag: str) -> str:
        node = entry.find(f"{_ATOM}{tag}")
        return " ".join((node.text or "").split()) if node is not None else ""

    authors = [
        " ".join((name.text or "").split())
        for author in entry.findall(f"{_ATOM}author")
        if (name := author.find(f"{_ATOM}name")) is not None
    ]
    pdf_url = ""
    for link in entry.findall(f"{_ATOM}link"):
        if link.get("title") == "pdf":
            pdf_url = link.get("href", "")
            break
    return {
        "title": text_of("title"),
        "summary": text_of("summary"),
        "authors": authors,
        "published": text_of("published"),
        "url": text_of("id"),
        "pdf_url": pdf_url,
    }


@tool(
    name="arxiv",
    namespace="std",
    version="1",
    side_effects=SideEffects.read,
    description=(
        "Search arXiv and return matching papers (title, authors, abstract, "
        "published date, abstract/PDF URLs). No API key required."
    ),
)
async def arxiv_search(query: str, max_results: int = 5, timeout_s: float = 30.0) -> dict[str, Any]:
    """Search ``query``; return ``{query, results: [{title, summary, authors, ...}]}``."""
    limit = max(1, min(max_results, _MAX_RESULTS))
    try:
        async with _http.build_client(timeout_s) as client:
            response = await client.get(
                _API_URL,
                params={"search_query": f"all:{query}", "start": 0, "max_results": limit},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise StargraphRuntimeError(f"arxiv search failed: {exc}", query=query) from exc
    body = response.text
    if "<!DOCTYPE" in body or "<!ENTITY" in body:
        raise StargraphRuntimeError(
            "arxiv response contains a DTD declaration; refusing to parse (XXE hardening)",
            query=query,
        )
    try:
        feed = ET.fromstring(body)
    except ET.ParseError as exc:
        raise StargraphRuntimeError(f"arxiv returned unparsable XML: {exc}", query=query) from exc
    results = [_entry_to_result(entry) for entry in feed.findall(f"{_ATOM}entry")]
    return {"query": query, "results": results}
