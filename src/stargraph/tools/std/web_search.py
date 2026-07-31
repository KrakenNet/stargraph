# SPDX-License-Identifier: Apache-2.0
"""``std.web_search`` -- DuckDuckGo web search via the ``ddgs`` package.

``ddgs`` (the maintained successor of ``duckduckgo_search``; the
``stargraph[tools]`` extra) is imported lazily inside the tool body, so
the tool always registers and a missing dependency fails loudly with a
pip hint at call time. The blocking client runs in a worker thread.
"""

from __future__ import annotations

import asyncio
from typing import Any

from stargraph.errors import StargraphRuntimeError
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects

__all__ = ["web_search"]

_MAX_RESULTS = 25


def _load_ddgs() -> Any:
    """Import the DDGS client class; both package names are accepted."""
    import importlib

    for module_name in ("ddgs", "duckduckgo_search"):  # current name, legacy name
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        return getattr(module, "DDGS")  # noqa: B009 -- untyped module attribute
    raise StargraphRuntimeError(
        "std.web_search requires the ddgs package; install it with: pip install stargraph[tools]",
    )


@tool(
    name="web_search",
    namespace="std",
    version="1",
    side_effects=SideEffects.read,
    description=(
        "Search the web (DuckDuckGo) and return result titles, URLs, and "
        "snippets. No API key required."
    ),
)
async def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search for ``query``; return ``{query, results: [{title, url, snippet}]}``."""
    limit = max(1, min(max_results, _MAX_RESULTS))
    ddgs_cls = _load_ddgs()

    def _run() -> list[dict[str, Any]]:
        with ddgs_cls() as client:
            return [dict(r) for r in client.text(query, max_results=limit)]

    try:
        raw = await asyncio.to_thread(_run)
    except StargraphRuntimeError:
        raise
    except Exception as exc:  # ddgs raises library-specific errors (rate limits etc.)
        raise StargraphRuntimeError(f"web search failed: {exc}", query=query) from exc
    results = [
        {
            "title": str(r.get("title", "")),
            "url": str(r.get("href") or r.get("url") or ""),
            "snippet": str(r.get("body", "")),
        }
        for r in raw
    ]
    return {"query": query, "results": results}
