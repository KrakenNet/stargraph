# SPDX-License-Identifier: Apache-2.0
"""Shared httpx client factory for the built-in network tool packs.

Every network tool (std, slack, github, ...) builds its client through :func:`build_client` (called
via the module, ``_http.build_client(...)``) so tests can monkeypatch a
single seam with an ``httpx.MockTransport``-backed client.
"""

from __future__ import annotations

import httpx

from stargraph.errors import StargraphRuntimeError

__all__ = ["build_client", "check_scheme"]

_USER_AGENT = "stargraph-std-tools/1 (+https://github.com/KrakenNet/stargraph)"


def build_client(timeout: float = 30.0) -> httpx.AsyncClient:
    """A redirect-following AsyncClient with the pack's User-Agent."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT},
    )


def check_scheme(url: str) -> None:
    """Reject non-http(s) URLs before any request is attempted."""
    if not url.startswith(("http://", "https://")):
        raise StargraphRuntimeError(
            f"URL {url!r} must use http:// or https://",
            url=url,
        )
