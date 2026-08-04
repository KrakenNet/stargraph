# SPDX-License-Identifier: Apache-2.0
"""Live-network smoke tests for the ``std`` tool pack.

Gated behind ``STARGRAPH_LIVE_NET=1`` -- they hit real public endpoints
(example.com, Wikipedia, arXiv, DuckDuckGo) and are inherently flaky
offline/CI. Offline behavior is covered in
``tests/unit/test_std_http_tools.py``.
"""

from __future__ import annotations

import os

import pytest

from stargraph.tools.std.arxiv import arxiv_search
from stargraph.tools.std.fetch_page import fetch_page
from stargraph.tools.std.http_request import http_request
from stargraph.tools.std.wikipedia import wikipedia

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("STARGRAPH_LIVE_NET"),
        reason="set STARGRAPH_LIVE_NET=1 to run live network tests",
    ),
]


async def test_http_request_live() -> None:
    out = await http_request(url="https://example.com/")
    assert out["status_code"] == 200
    assert "Example Domain" in out["text"]


async def test_fetch_page_live() -> None:
    pytest.importorskip("readability")
    out = await fetch_page(url="https://example.com/")
    assert out["status_code"] == 200
    assert "illustrative examples" in out["text"]


async def test_wikipedia_live() -> None:
    out = await wikipedia(query="Python programming language")
    assert out["results"]
    assert any("Python" in r["title"] for r in out["results"])


async def test_arxiv_live() -> None:
    out = await arxiv_search(query="reinforcement learning", max_results=3)
    assert out["results"]
    assert all(r["url"] for r in out["results"])


async def test_web_search_live() -> None:
    pytest.importorskip("ddgs")
    from stargraph.tools.std.web_search import web_search

    out = await web_search(query="stargraph agent framework", max_results=3)
    assert out["results"]
