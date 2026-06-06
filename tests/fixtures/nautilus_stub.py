# SPDX-License-Identifier: Apache-2.0
"""Stub :class:`BrokerNode` returning a canned :class:`nautilus.BrokerResponse`.

Used by Phase-3 task 3.26 composition tests to exercise the
"Nautilus-stubbed" form of the CVE-triage IR end-to-end without a live
:class:`nautilus.Broker` lifespan singleton wired up. Mirrors the
:class:`harbor.nodes.nautilus.broker_node.BrokerNode` contract verbatim:

* Same :class:`harbor.nodes.nautilus.broker_node.BrokerNodeConfig` Pydantic
  schema (``agent_id_field`` / ``intent_field`` / ``output_field``).
* Same :class:`~harbor.tools.spec.SideEffects.read` class-level attribute.
* Same ``__harbor_provenance__`` envelope (``origin="tool"``,
  ``source="nautilus"``, ``external_id=<request_id>``) wrapped around the
  :meth:`nautilus.BrokerResponse.model_dump` shape.

The only divergence: :meth:`StubBrokerNode.execute` skips the
:func:`harbor.serve.contextvars.current_broker` lookup entirely and
fabricates a deterministic :class:`BrokerResponse`. This lets the
composition test drive the full graph (including HITL + artifact write)
without booting the Nautilus lifespan.

Production wiring uses the real :class:`BrokerNode`; this stub is
test-only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nautilus import BrokerResponse  # pyright: ignore[reportMissingTypeStubs]

from harbor.nodes.base import ExecutionContext, NodeBase
from harbor.nodes.nautilus.broker_node import BrokerNodeConfig
from harbor.tools.spec import SideEffects

if TYPE_CHECKING:
    from pydantic import BaseModel

__all__ = ["StubBrokerNode", "make_stub_response"]


def make_stub_response(request_id: str = "req-stub-cve-001") -> BrokerResponse:
    """Build a canned :class:`BrokerResponse` for stub-broker fixtures.

    Returns the same shape a real broker would return for a CVE-triage
    intent: a non-empty ``data`` payload, two ``sources_queried``
    entries, and the standard zero-ish defaults for the remaining
    optional fields. The ``request_id`` is stable so provenance assertions
    pin to a fixed value across runs.
    """
    return BrokerResponse(
        request_id=request_id,
        data={
            "hits": [
                {
                    "cve_id": "CVE-2026-0001",
                    "score": 9.8,
                    "vendor": "acme-corp",
                }
            ]
        },
        sources_queried=["vuln_db", "exploit_db"],
        sources_denied=[],
        sources_skipped=[],
        sources_errored=[],
        scope_restrictions={},
        attestation_token=None,
        duration_ms=12,
        cap_breached=None,
        fact_set_hash=None,
        source_session_signatures={},
    )


class StubBrokerNode(NodeBase):
    """Test-only :class:`BrokerNode` substitute returning a canned response.

    Constructor mirrors :class:`harbor.nodes.nautilus.BrokerNode`: takes
    a :class:`BrokerNodeConfig` and exposes the same ``side_effects``
    + ``config_model`` class attributes. :meth:`execute` reads
    ``state[agent_id_field]`` + ``state[intent_field]`` (so a
    misconfigured fixture surfaces an :class:`AttributeError` loudly --
    same convention as the real node) but does NOT call
    :meth:`Broker.arequest`; instead it returns
    :func:`make_stub_response` wrapped in the harbor provenance
    envelope.

    No capability gate: the composition test drives the graph from the
    POC default (``ctx.capabilities=None``), so the real node would
    skip its in-node gate too. The IR-level loop gate is unchanged.
    """

    side_effects = SideEffects.read
    config_model = BrokerNodeConfig

    def __init__(self, *, config: BrokerNodeConfig) -> None:
        self._config = config

    @property
    def config(self) -> BrokerNodeConfig:
        return self._config

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del ctx  # unused: stub skips capability + lifespan-broker lookup
        # Force-loud read via getattr so misconfigured fixtures surface
        # AttributeError at the call site (matches BrokerNode convention).
        agent_id = getattr(state, self._config.agent_id_field)
        intent = getattr(state, self._config.intent_field)
        if not isinstance(agent_id, str):
            raise TypeError(
                f"StubBrokerNode agent_id_field must resolve to str; got {type(agent_id).__name__}"
            )
        if not isinstance(intent, str):
            raise TypeError(
                f"StubBrokerNode intent_field must resolve to str; got {type(intent).__name__}"
            )

        response = make_stub_response()
        dumped: dict[str, Any] = response.model_dump(mode="json")
        dumped["__harbor_provenance__"] = {
            "origin": "tool",
            "source": "nautilus",
            "external_id": response.request_id,
        }
        return {self._config.output_field: dumped}
