# SPDX-License-Identifier: Apache-2.0
"""``std.fetch_page`` -- fetch a URL and extract its readable main content.

HTML responses go through ``readability-lxml`` (the ``stargraph[tools]``
extra) to isolate the article body, then tags are stripped to plain text.
Non-HTML responses (JSON, plain text) return their body verbatim, no
readability needed. Network read -> ``side_effects=read`` (replayable
from recorded results, per the house convention).
"""

from __future__ import annotations

import html as html_mod
import re
from typing import Any

import httpx

from stargraph.errors import StargraphRuntimeError
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects
from stargraph.tools.std import _http

__all__ = ["fetch_page", "strip_tags"]

_MAX_CHARS = 100_000

_BLOCK_TAG_RE = re.compile(r"</?(?:p|br|div|li|tr|h[1-6])[^>]*>", re.IGNORECASE)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def strip_tags(fragment: str) -> str:
    """HTML fragment -> plain text (block tags become newlines).

    Shared with :mod:`stargraph.tools.std.wikipedia` (search excerpts carry
    highlight markup).
    """
    text = _SCRIPT_STYLE_RE.sub(" ", fragment)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html_mod.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


def _extract_readable(html: str, url: str) -> tuple[str, str]:
    """Return ``(title, text)`` of the main content via readability-lxml."""
    try:
        from readability import Document  # pyright: ignore[reportMissingTypeStubs]
    except ImportError as exc:
        raise StargraphRuntimeError(
            "std.fetch_page requires readability-lxml for HTML extraction; "
            "install it with: pip install stargraph[tools]",
            url=url,
        ) from exc
    doc: Any = Document(html)
    title = str(doc.title() or "").strip()
    text = strip_tags(str(doc.summary()))
    return title, text


@tool(
    name="fetch_page",
    namespace="std",
    version="1",
    side_effects=SideEffects.read,
    description=(
        "Fetch a URL and return its readable main content as plain text "
        "(readability extraction for HTML; verbatim body for JSON/plain "
        "responses)."
    ),
)
async def fetch_page(url: str, max_chars: int = 20_000, timeout_s: float = 30.0) -> dict[str, Any]:
    """Fetch ``url``; return ``{url, status_code, title, text, truncated}``."""
    _http.check_scheme(url)
    max_chars = max(1, min(max_chars, _MAX_CHARS))
    try:
        async with _http.build_client(timeout_s) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise StargraphRuntimeError(f"fetch failed: {exc}", url=url) from exc
    content_type = response.headers.get("content-type", "")
    if "html" in content_type:
        title, text = _extract_readable(response.text, url)
    else:
        title, text = "", response.text
    truncated = len(text) > max_chars
    return {
        "url": str(response.url),
        "status_code": response.status_code,
        "title": title,
        "text": text[:max_chars],
        "truncated": truncated,
    }
