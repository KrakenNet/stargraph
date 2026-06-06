# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :class:`stargraph.nodes.nautilus.BrokerNode` (FR-44, FR-46, AC-6.1, AC-6.4).

The :class:`BrokerNode` is the graph-node form of the Nautilus broker
integration. It reads three state-keys (configurable: ``agent_id_field``,
``intent_field``, ``output_field``), calls
:meth:`nautilus.Broker.arequest` via the lifespan-singleton accessor
(:func:`stargraph.serve.contextvars.current_broker`), and writes the
:class:`nautilus.BrokerResponse` back to ``state[output_field]`` with a
``stargraph.evidence``-shaped provenance record (``origin=tool``,
``source=nautilus``, ``external_id=<broker request_id>``).

Tests cover the three RED-test invariants from the task spec:

1. happy path -- arequest is called with the right args, response lands
   on the configured output field, provenance carries the right shape.
2. missing-broker fail-loud -- :func:`current_broker` raises
   :class:`StargraphRuntimeError` outside an active lifespan, and
   :class:`BrokerNode` propagates that.
3. capability gate -- when ``ctx.capabilities`` is supplied without the
   ``tools:broker_request`` permission, the node raises
   :class:`CapabilityError` before reaching the broker call.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from nautilus import BrokerResponse  # pyright: ignore[reportMissingTypeStubs]
from pydantic import BaseModel

from stargraph.errors import CapabilityError, StargraphRuntimeError
from stargraph.nodes.nautilus.broker_node import BrokerNode, BrokerNodeConfig
from stargraph.security.capabilities import Capabilities, CapabilityClaim
from stargraph.serve.contextvars import _broker_var, current_broker
from stargraph.tools.spec import SideEffects


class _State(BaseModel):
    """Minimal Pydantic state holder used by the BrokerNode tests."""

    agent_id: str
    intent: str
    response: dict[str, Any] | None = None


class _Ctx:
    """Duck-typed execution context covering the Phase-1 surface BrokerNode reads."""

    def __init__(self, *, capabilities: Capabilities | None = None) -> None:
        self.run_id = "run-test"
        self.step = 1
        self.capabilities = capabilities


def _stub_response(request_id: str = "req-abc-123") -> BrokerResponse:
    """Construct a deterministic :class:`BrokerResponse` for assertions."""
    return BrokerResponse(
        request_id=request_id,
        data={"hits": []},
        sources_queried=["vuln_db"],
        sources_denied=[],
        sources_skipped=[],
        sources_errored=[],
        scope_restrictions={},
        attestation_token=None,
        duration_ms=42,
        cap_breached=None,
        fact_set_hash=None,
        source_session_signatures={},
    )


@pytest.mark.asyncio
async def test_broker_node_happy_path_calls_arequest_and_writes_response() -> None:
    """Node must call Broker.arequest with the right args and write the response."""
    response = _stub_response()
    fake_broker = AsyncMock()
    fake_broker.arequest = AsyncMock(return_value=response)

    node = BrokerNode(
        config=BrokerNodeConfig(
            agent_id_field="agent_id",
            intent_field="intent",
            output_field="response",
        )
    )
    state = _State(agent_id="analyst", intent="cve-triage")

    token = _broker_var.set(fake_broker)
    try:
        patch = await node.execute(state, _Ctx())
    finally:
        _broker_var.reset(token)

    # (a) arequest called with the right args
    fake_broker.arequest.assert_awaited_once_with(agent_id="analyst", intent="cve-triage")
    # (b) response written to state[output_field] with provenance envelope
    assert "response" in patch
    written = patch["response"]
    assert written["data"]["hits"] == []
    # (c) provenance carries origin=tool, source=nautilus, external_id=<request_id>
    provenance = written["__stargraph_provenance__"]
    assert provenance["origin"] == "tool"
    assert provenance["source"] == "nautilus"
    assert provenance["external_id"] == "req-abc-123"
    # (d) class-level side_effects=read declared
    assert BrokerNode.side_effects is SideEffects.read


@pytest.mark.asyncio
async def test_broker_node_without_broker_contextvar_raises() -> None:
    """Running BrokerNode outside an active lifespan raises StargraphRuntimeError."""
    node = BrokerNode(
        config=BrokerNodeConfig(
            agent_id_field="agent_id",
            intent_field="intent",
            output_field="response",
        )
    )
    state = _State(agent_id="analyst", intent="cve-triage")

    # Sanity: contextvar reads None outside an active lifespan; the
    # accessor lifts that into StargraphRuntimeError.
    with pytest.raises(StargraphRuntimeError):
        current_broker()
    with pytest.raises(StargraphRuntimeError):
        await node.execute(state, _Ctx())


@pytest.mark.asyncio
async def test_broker_node_capability_gate_denies_without_permission() -> None:
    """Without ``tools:broker_request`` capability, execute() raises CapabilityError."""
    fake_broker = AsyncMock()
    fake_broker.arequest = AsyncMock(return_value=_stub_response())

    node = BrokerNode(
        config=BrokerNodeConfig(
            agent_id_field="agent_id",
            intent_field="intent",
            output_field="response",
        )
    )
    state = _State(agent_id="analyst", intent="cve-triage")
    capabilities = Capabilities(
        default_deny=True,
        granted={CapabilityClaim(name="other:thing")},
    )

    token = _broker_var.set(fake_broker)
    try:
        with pytest.raises(CapabilityError):
            await node.execute(state, _Ctx(capabilities=capabilities))
    finally:
        _broker_var.reset(token)

    # Capability check fires BEFORE the broker call -- arequest must not run.
    fake_broker.arequest.assert_not_awaited()


def test_broker_node_declares_required_capability() -> None:
    """The class exposes ``requires_capabilities`` for the loop-side gate."""
    assert BrokerNode.requires_capabilities == ("tools:broker_request",)
