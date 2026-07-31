# SPDX-License-Identifier: Apache-2.0
"""``std.http_request`` -- general HTTP client (any method, any endpoint).

``side_effects=external`` because the method is caller-chosen: a POST can
mutate the world, so replay defaults to ``must-stub`` (FR-21). For pure
page reads prefer ``std.fetch_page`` (side-effect ``read``, replayable
from recorded results).
"""

from __future__ import annotations

from typing import Any

import httpx

from stargraph.errors import StargraphRuntimeError
from stargraph.tools import _http
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects

__all__ = ["http_request"]

_ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
_MAX_TEXT_CHARS = 262_144


@tool(
    name="http_request",
    namespace="std",
    version="1",
    side_effects=SideEffects.external,
    description=(
        "Make an HTTP request (GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS) and "
        "return status, headers, and body text. body (raw string) and "
        "json_body are mutually exclusive."
    ),
)
async def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    body: str | None = None,
    json_body: Any | None = None,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Send the request; return ``{status_code, headers, text, url, truncated}``."""
    _http.check_scheme(url)
    verb = method.upper()
    if verb not in _ALLOWED_METHODS:
        raise StargraphRuntimeError(
            f"unsupported HTTP method {method!r}; expected one of {', '.join(_ALLOWED_METHODS)}",
            method=method,
        )
    if body is not None and json_body is not None:
        raise StargraphRuntimeError(
            "body and json_body are mutually exclusive",
            url=url,
        )
    try:
        async with _http.build_client(timeout_s) as client:
            response = await client.request(
                verb,
                url,
                headers=headers,
                params=params,
                content=body,
                json=json_body,
            )
    except httpx.HTTPError as exc:
        raise StargraphRuntimeError(f"http request failed: {exc}", url=url) from exc
    text = response.text
    truncated = len(text) > _MAX_TEXT_CHARS
    return {
        "status_code": response.status_code,
        "headers": dict(response.headers),
        "text": text[:_MAX_TEXT_CHARS],
        "url": str(response.url),
        "truncated": truncated,
    }
