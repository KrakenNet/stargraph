# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :func:`stargraph.tools.nautilus.broker_request` (FR-45, AC-6.2, AC-6.5).

The :func:`broker_request` `@tool`-decorated coroutine is the
sub-graph / ReAct-skill consumption form of the Nautilus broker
integration. It mirrors :class:`BrokerNode`'s contract but ships as a
registry-discoverable tool callable rather than a graph node:

* Signature: ``async def broker_request(*, agent_id: str, intent: str)
  -> dict[str, Any]`` (returns the broker response in dumped JSON form
  with the stargraph provenance envelope; the wrapper exposes
  :attr:`broker_request.spec` so the registry can introspect it).
* Reads :func:`current_broker` to pull the lifespan singleton.
* JSON Schema for the response shape is re-exported from
  :func:`stargraph.nodes.nautilus.schemas.broker_response_schema`.

The RED test invariants from the task spec:

1. happy path -- the tool returns a dict carrying the broker response
   shape (JSON-dumped) with the provenance envelope.
2. registry shape -- the wrapped callable carries a ``spec`` attribute
   produced by :func:`stargraph.tools.tool` (FR-26 contract).
3. JSON Schema -- the surfaced response schema matches
   :func:`BrokerResponse.model_json_schema(mode="serialization")`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from nautilus import BrokerResponse  # pyright: ignore[reportMissingTypeStubs]

from stargraph.errors import StargraphRuntimeError
from stargraph.ir._models import ToolSpec
from stargraph.nodes.nautilus.schemas import broker_response_schema
from stargraph.serve.contextvars import _broker_var
from stargraph.tools.nautilus.broker_request import broker_request
from stargraph.tools.spec import ReplayPolicy, SideEffects


def _stub_response(request_id: str = "req-broker-tool-1") -> BrokerResponse:
    """Construct a deterministic :class:`BrokerResponse` for assertions."""
    return BrokerResponse(
        request_id=request_id,
        data={"vuln_db": [{"cve": "cve-2025-1234"}]},
        sources_queried=["vuln_db"],
        sources_denied=[],
        sources_skipped=[],
        sources_errored=[],
        scope_restrictions={},
        attestation_token=None,
        duration_ms=11,
        cap_breached=None,
        fact_set_hash=None,
        source_session_signatures={},
    )


@pytest.mark.asyncio
async def test_broker_request_calls_broker_and_returns_envelope() -> None:
    """``await broker_request(...)`` must call Broker.arequest and return the envelope."""
    response = _stub_response()
    fake_broker = AsyncMock()
    fake_broker.arequest = AsyncMock(return_value=response)

    token = _broker_var.set(fake_broker)
    try:
        result = await broker_request(agent_id="analyst", intent="cve-triage")
    finally:
        _broker_var.reset(token)

    fake_broker.arequest.assert_awaited_once_with(agent_id="analyst", intent="cve-triage")
    # Result shape: model_dump(mode="json") of BrokerResponse + provenance envelope.
    assert result["request_id"] == "req-broker-tool-1"
    assert result["data"]["vuln_db"] == [{"cve": "cve-2025-1234"}]
    provenance = result["__stargraph_provenance__"]
    assert provenance["origin"] == "tool"
    assert provenance["source"] == "nautilus"
    assert provenance["external_id"] == "req-broker-tool-1"


@pytest.mark.asyncio
async def test_broker_request_without_broker_raises() -> None:
    """Calling broker_request without an active lifespan raises StargraphRuntimeError."""
    with pytest.raises(StargraphRuntimeError):
        await broker_request(agent_id="analyst", intent="cve-triage")


def test_broker_request_carries_tool_spec() -> None:
    """The decorated callable exposes a :class:`ToolSpec` under ``.spec`` (FR-26)."""
    spec = broker_request.spec  # pyright: ignore[reportFunctionMemberAccess]
    assert isinstance(spec, ToolSpec)
    assert spec.namespace == "nautilus"
    assert spec.name == "broker_request"
    assert spec.side_effects is SideEffects.read
    # ReplayPolicy default for read-class side effects is recorded_result.
    assert spec.replay_policy is ReplayPolicy.recorded_result
    assert "tools:broker_request" in spec.permissions


def test_broker_request_response_schema_matches_nautilus_pydantic() -> None:
    """The published response JSON Schema mirrors ``BrokerResponse.model_json_schema``."""
    expected = BrokerResponse.model_json_schema(mode="serialization")
    assert broker_response_schema() == expected
