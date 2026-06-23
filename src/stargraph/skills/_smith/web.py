# SPDX-License-Identifier: Apache-2.0
"""Web tool-use — optional, model-decided external research for generation.

The generator decides *whether* it needs outside information (``_decide``); only
then do we search and fetch. Everything here is **best-effort**: no API key, and
any failure (no network, parse miss, model error, ``needs=False``) yields ``[]``
so generation proceeds on the local RAG context alone.

The HTTP boundary is a single seam (``_http_get``) so tests stub the network
without monkeypatching httpx internals. Search uses DuckDuckGo's keyless HTML
endpoint; fetch strips tags and clips, since the result only seeds a prompt.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from stargraph.skills._smith.retrieval import Snippet

_DDG_HTML = "https://html.duckduckgo.com/html/"
_UA = "Mozilla/5.0 (compatible; stargraph-smith/1.0)"
_FETCH_MAX_CHARS = 2000

_RESULT_A = re.compile(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_SNIPPET = re.compile(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', re.S)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _http_get(url: str, *, params: dict[str, str] | None = None, timeout_s: float = 6.0) -> str:
    """The one network call — GET ``url`` and return the body text. Stub seam."""
    import httpx

    resp = httpx.get(
        url, params=params, timeout=timeout_s, headers={"User-Agent": _UA}, follow_redirects=True
    )
    resp.raise_for_status()
    return resp.text


def _strip_html(html: str) -> str:
    return _WS.sub(" ", _TAG.sub(" ", html)).strip()


def _unwrap(href: str) -> str:
    """DuckDuckGo wraps result links as ``/l/?uddg=<encoded>`` — unwrap to the real URL."""
    if "uddg=" in href:
        target = parse_qs(urlparse(href).query).get("uddg")
        if target:
            return unquote(target[0])
    return href


def web_search(query: str, *, k: int = 3, timeout_s: float = 6.0) -> list[dict[str, str]]:
    """Best-effort keyless web search. Returns up to ``k`` ``{title,url,snippet}``;
    ``[]`` on any error (no network, blocked, parse miss)."""
    try:
        html = _http_get(_DDG_HTML, params={"q": query}, timeout_s=timeout_s)
    except Exception:
        return []
    snippets = [_strip_html(s) for s in _SNIPPET.findall(html)]
    hits: list[dict[str, str]] = []
    for i, (href, title) in enumerate(_RESULT_A.findall(html)[:k]):
        hits.append(
            {
                "title": _strip_html(title),
                "url": _unwrap(href),
                "snippet": snippets[i] if i < len(snippets) else "",
            }
        )
    return hits


def web_fetch(url: str, *, timeout_s: float = 6.0, max_chars: int = _FETCH_MAX_CHARS) -> str:
    """Best-effort GET → tag-stripped, clipped text; ``""`` on any error."""
    try:
        body = _strip_html(_http_get(url, timeout_s=timeout_s))
    except Exception:
        return ""
    return body[:max_chars]


def _decide(brief: str) -> tuple[bool, list[str]]:
    """Model-decided: does this brief need outside info, and what to search for?
    Best-effort — any error → ``(False, [])`` (skip research)."""
    try:
        import dspy  # pyright: ignore[reportMissingTypeStubs]

        predictor = dspy.Predict("brief -> needs_research: bool, queries: list[str]")  # pyright: ignore[reportUnknownMemberType]
        result = predictor(brief=brief)  # pyright: ignore[reportUnknownVariableType]
        queries: list[str] = []
        raw: Any = getattr(result, "queries", None)
        try:
            for q in raw:
                text = str(q).strip()
                if text:
                    queries.append(text)
        except TypeError:
            queries = []
        needs = bool(getattr(result, "needs_research", False)) and bool(queries)
        return needs, queries
    except Exception:
        return False, []


def research(brief: str, *, max_queries: int = 2, per_query: int = 2) -> list[Snippet]:
    """Model-decided web research → grounding snippets (same type as RAG).

    Asks the model whether external info is needed; if so, searches each query
    and fetches the top hit for extra depth. Best-effort: returns ``[]`` when the
    model declines or anything fails. Caller scopes the LM with ``dspy.context``.
    """
    try:
        needs, queries = _decide(brief)
        if not needs:
            return []
        out: list[Snippet] = []
        for qi, query in enumerate(queries[:max_queries]):
            hits = web_search(query, k=per_query)
            if not hits:
                continue
            lines = [f"- {h['title']} ({h['url']}): {h['snippet']}" for h in hits]
            out.append(Snippet(source=f"web:search {query!r}", text="\n".join(lines)))
            if qi == 0:  # one deep fetch, on the top hit of the first query
                body = web_fetch(hits[0]["url"])
                if body:
                    out.append(Snippet(source=f"web:fetch {hits[0]['url']}", text=body))
        return out
    except Exception:
        return []
