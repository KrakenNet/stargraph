# SPDX-License-Identifier: Apache-2.0
"""Offline tests for the ``std`` network tools.

Every tool builds its client through ``_http.build_client`` -- the tests
monkeypatch that single seam with an ``httpx.MockTransport`` so no test
touches the network. Live coverage is env-gated in
``tests/integration/test_std_tools_live.py``.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

import httpx
import pytest

from stargraph.errors import StargraphRuntimeError
from stargraph.tools.std import _http
from stargraph.tools.std.arxiv import arxiv_search
from stargraph.tools.std.fetch_page import fetch_page
from stargraph.tools.std.http_request import http_request
from stargraph.tools.std.web_search import web_search
from stargraph.tools.std.wikipedia import wikipedia

pytestmark = pytest.mark.unit

_Handler = Any  # Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def mock_http(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install a request handler behind ``_http.build_client``."""

    def install(handler: _Handler) -> None:
        def _build(timeout: float = 30.0) -> httpx.AsyncClient:
            del timeout
            return httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                follow_redirects=True,
            )

        monkeypatch.setattr(_http, "build_client", _build)

    return install


# ---------------------------------------------------------------------------
# std.http_request
# ---------------------------------------------------------------------------


async def test_http_request_get(mock_http: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.params["q"] == "x"
        return httpx.Response(200, text="pong")

    mock_http(handler)
    out = await http_request(url="https://api.example.com/ping", params={"q": "x"})
    assert out["status_code"] == 200
    assert out["text"] == "pong"
    assert out["truncated"] is False


async def test_http_request_post_json(mock_http: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers["content-type"] == "application/json"
        return httpx.Response(201, json={"ok": True})

    mock_http(handler)
    out = await http_request(
        url="https://api.example.com/things", method="post", json_body={"a": 1}
    )
    assert out["status_code"] == 201


async def test_http_request_rejects_bad_scheme() -> None:
    with pytest.raises(StargraphRuntimeError, match="http:// or https://"):
        await http_request(url="ftp://example.com/file")


async def test_http_request_rejects_unknown_method() -> None:
    with pytest.raises(StargraphRuntimeError, match="unsupported HTTP method"):
        await http_request(url="https://example.com", method="TRACE")


async def test_http_request_body_and_json_exclusive() -> None:
    with pytest.raises(StargraphRuntimeError, match="mutually exclusive"):
        await http_request(url="https://example.com", body="raw", json_body={"a": 1})


async def test_http_request_wraps_transport_errors(mock_http: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    mock_http(handler)
    with pytest.raises(StargraphRuntimeError, match="http request failed"):
        await http_request(url="https://down.example.com")


# ---------------------------------------------------------------------------
# std.fetch_page
# ---------------------------------------------------------------------------

_HTML_PAGE = """
<html><head><title>Test Article</title></head><body>
<nav>menu menu menu</nav>
<article><h1>Test Article</h1>
<p>{}</p><p>Second paragraph with the payload.</p></article>
</body></html>
""".format("Readable body text. " * 30)


async def test_fetch_page_extracts_main_content(mock_http: Any) -> None:
    pytest.importorskip("readability")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML_PAGE, headers={"content-type": "text/html"})

    mock_http(handler)
    out = await fetch_page(url="https://example.com/article")
    assert out["title"] == "Test Article"
    assert "Readable body text." in out["text"]
    assert "<p>" not in out["text"]


async def test_fetch_page_non_html_passthrough(mock_http: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text='{"k": 1}', headers={"content-type": "application/json"})

    mock_http(handler)
    out = await fetch_page(url="https://example.com/data.json")
    assert out["text"] == '{"k": 1}'
    assert out["title"] == ""


async def test_fetch_page_truncates(mock_http: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="y" * 500, headers={"content-type": "text/plain"})

    mock_http(handler)
    out = await fetch_page(url="https://example.com/big.txt", max_chars=100)
    assert len(out["text"]) == 100
    assert out["truncated"] is True


# ---------------------------------------------------------------------------
# std.wikipedia
# ---------------------------------------------------------------------------


async def test_wikipedia_parses_search_results(mock_http: Any) -> None:
    payload = {
        "pages": [
            {
                "id": 1,
                "key": "Python_(programming_language)",
                "title": "Python (programming language)",
                "excerpt": '<span class="searchmatch">Python</span> is a language',
                "description": "General-purpose programming language",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "en.wikipedia.org"
        assert request.url.params["q"] == "python"
        return httpx.Response(200, json=payload)

    mock_http(handler)
    out = await wikipedia(query="python")
    (result,) = out["results"]
    assert result["title"] == "Python (programming language)"
    assert result["excerpt"] == "Python is a language"  # tags stripped
    assert result["url"] == "https://en.wikipedia.org/wiki/Python_%28programming_language%29"


async def test_wikipedia_rejects_bad_lang() -> None:
    with pytest.raises(StargraphRuntimeError, match="language code"):
        await wikipedia(query="x", lang="evil.example.com/")


# ---------------------------------------------------------------------------
# std.arxiv
# ---------------------------------------------------------------------------

_ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>Deterministic  Agent
      Routing</title>
    <summary>Rules, not vibes.</summary>
    <published>2024-01-01T00:00:00Z</published>
    <author><name>Ada Lovelace</name></author>
    <author><name>Alan Turing</name></author>
    <link href="http://arxiv.org/abs/2401.00001v1" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2401.00001v1" rel="related"/>
  </entry>
</feed>
"""


async def test_arxiv_parses_atom_entries(mock_http: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "export.arxiv.org"
        assert request.url.params["search_query"] == "all:agents"
        return httpx.Response(200, text=_ATOM_FEED)

    mock_http(handler)
    out = await arxiv_search(query="agents")
    (result,) = out["results"]
    assert result["title"] == "Deterministic Agent Routing"  # whitespace collapsed
    assert result["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert result["url"] == "http://arxiv.org/abs/2401.00001v1"
    assert result["pdf_url"] == "http://arxiv.org/pdf/2401.00001v1"


async def test_arxiv_rejects_dtd_payload(mock_http: Any) -> None:
    evil = '<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY x "y">]><feed/>'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=evil)

    mock_http(handler)
    with pytest.raises(StargraphRuntimeError, match="DTD"):
        await arxiv_search(query="agents")


# ---------------------------------------------------------------------------
# std.web_search
# ---------------------------------------------------------------------------


class _FakeDDGS:
    def __enter__(self) -> _FakeDDGS:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def text(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        del max_results
        return [{"title": f"About {query}", "href": "https://r.example.com", "body": "snippet"}]


async def test_web_search_maps_results(monkeypatch: pytest.MonkeyPatch) -> None:
    # ``stargraph.tools.std.web_search`` the *attribute* is the re-exported
    # function (shadowing the submodule); patch the module object itself.
    monkeypatch.setattr(
        import_module("stargraph.tools.std.web_search"), "_load_ddgs", lambda: _FakeDDGS
    )
    out = await web_search(query="stargraph")
    (result,) = out["results"]
    assert result == {
        "title": "About stargraph",
        "url": "https://r.example.com",
        "snippet": "snippet",
    }


async def test_web_search_wraps_backend_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boom(_FakeDDGS):
        def text(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
            raise RuntimeError("rate limited")

    monkeypatch.setattr(
        import_module("stargraph.tools.std.web_search"), "_load_ddgs", lambda: _Boom
    )
    with pytest.raises(StargraphRuntimeError, match="web search failed"):
        await web_search(query="stargraph")
