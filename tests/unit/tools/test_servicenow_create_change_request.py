# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :func:`harbor.tools.servicenow.create_change_request`.

The tool is the documented write surface for ServiceNow CR creation in
Harbor v1 (Nautilus's adapter is read-only; see module docstring).
Coverage:

1. **Dry-run by default** -- without ``HARBOR_SERVICENOW_LIVE``, the
   tool short-circuits before any network call and returns a synthetic
   envelope with ``status="dry-run"``.
2. **Idempotency required** -- empty / whitespace ``correlation_id``
   raises :class:`HarborRuntimeError` so a caller can never accidentally
   POST without a dedupe key.
3. **Live POST shape** -- with ``HARBOR_SERVICENOW_LIVE=1`` and a
   patched httpx transport, the tool issues a single POST to
   ``/api/now/table/change_request`` with the resolved body + auth and
   surfaces the parsed ``result`` plus the provenance envelope.
4. **Auth resolution** -- bearer / basic both work; missing creds raise.
5. **Registry shape** -- decorator stamps :class:`ToolSpec` with the
   right namespace / name / version / capability / side-effect.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from harbor.errors import HarborRuntimeError
from harbor.ir._models import ToolSpec
from harbor.tools.servicenow.create_change_request import create_change_request
from harbor.tools.spec import ReplayPolicy, SideEffects

# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------


def test_tool_spec_shape() -> None:
    spec = create_change_request.spec  # pyright: ignore[reportFunctionMemberAccess]
    assert isinstance(spec, ToolSpec)
    assert spec.namespace == "servicenow"
    assert spec.name == "create_change_request"
    assert spec.version == "1"
    assert spec.side_effects == SideEffects.write
    assert spec.replay_policy == ReplayPolicy.must_stub
    assert "tools:servicenow:write" in spec.permissions


# ---------------------------------------------------------------------------
# Dry-run path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HARBOR_SERVICENOW_LIVE", raising=False)
    out = await create_change_request(
        short_description="CVE-2021-44228 remediation",
        description="Apply log4j 2.17 patch to affected hosts.",
        correlation_id="remediation-2021-44228-batch-1",
        priority=2,
    )
    assert out["status"] == "dry-run"
    body = out["request_body"]
    assert body["short_description"] == "CVE-2021-44228 remediation"
    assert body["correlation_id"] == "remediation-2021-44228-batch-1"
    assert body["priority"] == "2"
    prov = out["__harbor_provenance__"]
    assert prov["source"] == "servicenow"
    assert prov["external_id"].startswith("dry-run:")


# ---------------------------------------------------------------------------
# Idempotency requirement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "   ", "\t\n"])
async def test_empty_correlation_id_rejected(bad: str) -> None:
    with pytest.raises(HarborRuntimeError, match="correlation_id"):
        await create_change_request(
            short_description="x",
            description="y",
            correlation_id=bad,
        )


# ---------------------------------------------------------------------------
# Live POST path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_post_basic_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARBOR_SERVICENOW_LIVE", "1")
    monkeypatch.setenv("SERVICENOW_BASE_URL", "https://ven00000.service-now.com/")
    monkeypatch.setenv("SERVICENOW_AUTH_KIND", "basic")
    monkeypatch.setenv("SERVICENOW_USERNAME", "robot")
    monkeypatch.setenv("SERVICENOW_PASSWORD", "secret")

    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["body"] = json.loads(request.content)
        captured["auth_header"] = request.headers.get("Authorization", "")
        return httpx.Response(
            201,
            json={
                "result": {
                    "sys_id": "abc123",
                    "number": "CHG0010001",
                }
            },
        )

    transport = httpx.MockTransport(_handler)

    # Patch httpx.AsyncClient to use the mock transport.
    real_client = httpx.AsyncClient

    class _PatchedClient(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _PatchedClient)

    out = await create_change_request(
        short_description="live test",
        description="live mode CR",
        correlation_id="live-test-1",
        priority=4,
    )
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/now/table/change_request")
    assert captured["body"]["correlation_id"] == "live-test-1"
    # httpx basic-auth header is base64-encoded but starts with "Basic "
    assert captured["auth_header"].startswith("Basic ")
    assert out["status"] == "ok"
    assert out["result"]["number"] == "CHG0010001"
    assert out["__harbor_provenance__"]["external_id"] == "abc123"


@pytest.mark.asyncio
async def test_live_post_bearer_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARBOR_SERVICENOW_LIVE", "1")
    monkeypatch.setenv("SERVICENOW_BASE_URL", "https://ven00000.service-now.com")
    monkeypatch.setenv("SERVICENOW_AUTH_KIND", "bearer")
    monkeypatch.setenv("SERVICENOW_BEARER_TOKEN", "tok-123")

    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["auth_header"] = request.headers.get("Authorization", "")
        return httpx.Response(201, json={"result": {"sys_id": "x"}})

    transport = httpx.MockTransport(_handler)
    real_client = httpx.AsyncClient

    class _PatchedClient(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _PatchedClient)

    await create_change_request(
        short_description="bearer test",
        description="x",
        correlation_id="bearer-1",
    )
    assert captured["auth_header"] == "Bearer tok-123"


# ---------------------------------------------------------------------------
# Missing-env failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_missing_base_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARBOR_SERVICENOW_LIVE", "1")
    monkeypatch.setenv("SERVICENOW_AUTH_KIND", "basic")
    monkeypatch.setenv("SERVICENOW_USERNAME", "u")
    monkeypatch.setenv("SERVICENOW_PASSWORD", "p")
    monkeypatch.delenv("SERVICENOW_BASE_URL", raising=False)
    with pytest.raises(HarborRuntimeError, match="SERVICENOW_BASE_URL"):
        await create_change_request(short_description="x", description="y", correlation_id="z")


@pytest.mark.asyncio
async def test_live_missing_basic_creds_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARBOR_SERVICENOW_LIVE", "1")
    monkeypatch.setenv("SERVICENOW_BASE_URL", "https://ven.service-now.com")
    monkeypatch.setenv("SERVICENOW_AUTH_KIND", "basic")
    monkeypatch.delenv("SERVICENOW_USERNAME", raising=False)
    monkeypatch.delenv("SERVICENOW_PASSWORD", raising=False)
    with pytest.raises(HarborRuntimeError, match="USERNAME and SERVICENOW_PASSWORD"):
        await create_change_request(short_description="x", description="y", correlation_id="z")


@pytest.mark.asyncio
async def test_live_mtls_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARBOR_SERVICENOW_LIVE", "1")
    monkeypatch.setenv("SERVICENOW_BASE_URL", "https://ven.service-now.com")
    monkeypatch.setenv("SERVICENOW_AUTH_KIND", "mtls")
    with pytest.raises(HarborRuntimeError, match="not supported"):
        await create_change_request(short_description="x", description="y", correlation_id="z")


@pytest.mark.asyncio
async def test_live_unknown_auth_kind_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARBOR_SERVICENOW_LIVE", "1")
    monkeypatch.setenv("SERVICENOW_BASE_URL", "https://ven.service-now.com")
    monkeypatch.setenv("SERVICENOW_AUTH_KIND", "wat")
    with pytest.raises(HarborRuntimeError, match="unknown SERVICENOW_AUTH_KIND"):
        await create_change_request(short_description="x", description="y", correlation_id="z")
