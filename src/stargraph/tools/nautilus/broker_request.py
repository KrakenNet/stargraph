# SPDX-License-Identifier: Apache-2.0
"""stargraph.tools.nautilus.broker_request -- the @tool form of the broker call (FR-45, §8.2).

This is the sub-graph / ReAct-skill consumption form of the Nautilus
broker integration. Where :class:`stargraph.nodes.nautilus.BrokerNode`
ships as a graph node (one fixed slot in a graph spec), this ships as
a registry-discoverable async function that any tool-aware caller (a
ReAct skill, a sub-graph dispatcher, an explicit `stargraph.tools` lookup)
can invoke at any step.

The ``@tool`` decorator binds the callable to a :class:`ToolSpec`
(``namespace="nautilus"``, ``name="broker_request"``, ``version="1"``)
so the registry surfaces it through the standard discovery path.
``side_effects = SideEffects.read`` gives a default
``replay_policy = recorded_result`` -- the broker is read-only from
Stargraph's POV and cassette-replay is the right default behaviour.

The function body resolves the lifespan-singleton :class:`Broker` via
:func:`current_broker` (raises :class:`StargraphRuntimeError` if unset)
and returns the response dump with the same stargraph-provenance envelope
:class:`BrokerNode` writes -- so any consumer that pattern-matches on
the envelope works the same against either form.

Distribution entry-point: declared in ``pyproject.toml`` under
``[project.entry-points."stargraph.tools"]`` so the Phase 1 plugin loader
auto-discovers the tool when a downstream distribution registers a
:class:`PluginManifest`. Stargraph's own pyproject already lists the
manifest entry-point factory; this file just exposes the callable for
the loader to pick up.
"""

from __future__ import annotations

from typing import Any

from stargraph.nodes.nautilus.schemas import broker_response_schema
from stargraph.serve.contextvars import current_broker
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects

__all__ = ["broker_request"]


_NAMESPACE = "nautilus"
_NAME = "broker_request"
_VERSION = "1"
_REQUIRED_CAPABILITY = "tools:broker_request"


@tool(
    name=_NAME,
    namespace=_NAMESPACE,
    version=_VERSION,
    side_effects=SideEffects.read,
    requires_capability=_REQUIRED_CAPABILITY,
    description=(
        "Call the lifespan-singleton Nautilus Broker to resolve an intent "
        "for an agent. Returns the BrokerResponse JSON dump plus a stargraph "
        "provenance envelope (origin=tool, source=nautilus, external_id=<request_id>)."
    ),
    output_schema=broker_response_schema(),
)
async def broker_request(*, agent_id: str, intent: str) -> dict[str, Any]:
    """Call :meth:`nautilus.Broker.arequest` and return the dumped response (design §8.2).

    Parameters
    ----------
    agent_id
        Identifier of the requesting agent (Nautilus consults its
        :class:`AgentRegistry` to resolve the agent's clearance,
        compartments, and default purpose).
    intent
        Free-text intent string the broker's intent-analyser pipeline
        consumes; resolved against configured sources via the policy
        engine.

    Returns
    -------
    dict[str, Any]
        ``BrokerResponse.model_dump(mode="json")`` plus a
        ``__stargraph_provenance__`` envelope with
        ``origin="tool"``, ``source="nautilus"`` and
        ``external_id=<broker request_id>`` for cross-system correlation.

    Raises
    ------
    StargraphRuntimeError
        If no lifespan-singleton :class:`Broker` is registered (i.e.
        the FastAPI lifespan factory has not run, or
        ``<stargraph-config>/nautilus.yaml`` was missing at startup).
    """
    broker = current_broker()
    response = await broker.arequest(agent_id=agent_id, intent=intent)
    dumped: dict[str, Any] = response.model_dump(mode="json")
    dumped["__stargraph_provenance__"] = {
        "origin": "tool",
        "source": _NAMESPACE,
        "external_id": response.request_id,
    }
    return dumped
