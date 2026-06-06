# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes.nautilus.broker_node -- :class:`BrokerNode` built-in (FR-44, FR-46, design §8.1).

The node is the graph-node form of the Nautilus broker integration.
A graph author drops a :class:`BrokerNode` configured with three
state-key names (``agent_id_field``, ``intent_field``, ``output_field``)
into a graph; on dispatch the node:

1. Pulls ``agent_id`` and ``intent`` from the run state at the
   configured keys.
2. Resolves the lifespan-singleton :class:`nautilus.Broker` via
   :func:`stargraph.serve.contextvars.current_broker` (raises
   :class:`StargraphRuntimeError` if no lifespan is active).
3. Optionally enforces the ``tools:broker_request`` capability gate
   when ``ctx.capabilities`` is supplied (the loop's IR-level gate at
   :func:`stargraph.graph.loop._check_node_capability` is the canonical
   line of defense; this in-node check is a defense-in-depth backstop
   for unit-tested call sites and for graphs that bypass the IR-level
   gate via direct :meth:`NodeBase.execute` invocation).
4. Calls :meth:`Broker.arequest` and patches the response onto state at
   ``output_field`` -- the patch dict carries a
   ``__stargraph_provenance__`` envelope (``origin=tool``,
   ``source=nautilus``, ``external_id=<broker request_id>``) per design
   §8.1 so downstream :mod:`stargraph.fathom` mirroring can pick it up.

``side_effects = SideEffects.read`` because the broker is read-only
from Stargraph's POV (Nautilus owns its own write side-effects internally;
Stargraph consumes the read surface only). Phase 1 keeps this default;
Phase 2 may surface a per-instance override if downstream consumers
wire Nautilus to side-effecting tools (the design notes this carve-out
in §8.1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.errors import CapabilityError, StargraphRuntimeError
from stargraph.ir import IRBase
from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.serve.contextvars import current_broker
from stargraph.tools.spec import SideEffects

if TYPE_CHECKING:
    from pydantic import BaseModel

__all__ = ["BrokerNode", "BrokerNodeConfig"]


_REQUIRED_CAPABILITY = "tools:broker_request"
"""Capability required to call the broker (design §8.1)."""


class BrokerNodeConfig(IRBase):
    """Pydantic config for :class:`BrokerNode` (design §8.1).

    Three string fields name the state-keys the node reads/writes:

    * ``agent_id_field`` -- read at dispatch; passed as
      :meth:`Broker.arequest`'s ``agent_id`` kwarg.
    * ``intent_field`` -- read at dispatch; passed as
      :meth:`Broker.arequest`'s ``intent`` kwarg.
    * ``output_field`` -- the resolved
      :class:`nautilus.BrokerResponse` (``model_dump`` form, plus a
      ``__stargraph_provenance__`` envelope) is patched into state at this
      key.

    Inherits ``extra="forbid"`` from :class:`IRBase` -- unknown YAML/
    JSON keys fail loudly at validation time (FR-6, AC-9.1).
    """

    agent_id_field: str
    """State attribute holding the requesting agent's id (string)."""
    intent_field: str
    """State attribute holding the broker intent string."""
    output_field: str
    """State key receiving the :class:`BrokerResponse` dump."""


class BrokerNode(NodeBase):
    """Built-in node that calls :meth:`nautilus.Broker.arequest` (FR-44, AC-6.1).

    ``side_effects = SideEffects.read`` -- Nautilus is read-only from
    Stargraph's POV (design §8.1). Replay-safe by default.

    ``requires_capabilities = ("tools:broker_request",)`` exposes the
    capability namespace so callers / tests can introspect the gate;
    the loop's :func:`stargraph.graph.loop._check_node_capability` enforces
    it from the IR-level :class:`NodeSpec.required_capability`. The
    in-:meth:`execute` check below is a defense-in-depth backstop for
    direct dispatch (unit tests, ad-hoc :class:`stargraph.Graph` builders
    that don't thread an IR through).

    Configured via :class:`BrokerNodeConfig`; the config is attached at
    construction time (``BrokerNode(config=cfg)``) -- same convention
    :class:`~stargraph.nodes.artifacts.WriteArtifactNode` and
    :class:`~stargraph.nodes.interrupt.InterruptNode` established.
    """

    side_effects = SideEffects.read
    """Read-only side effect class -- Nautilus is replay-safe (design §8.1)."""
    config_model = BrokerNodeConfig
    """Pydantic config schema, surfaced for IR validators / registry tooling."""
    requires_capabilities: tuple[str, ...] = (_REQUIRED_CAPABILITY,)
    """Capability namespace consumed by the loop's IR-level gate (design §8.1)."""

    def __init__(self, *, config: BrokerNodeConfig) -> None:
        self._config = config

    @property
    def config(self) -> BrokerNodeConfig:
        """Public read-only handle on the validated config (used by tests)."""
        return self._config

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        """Call :meth:`Broker.arequest` and patch the response onto state.

        Raises
        ------
        StargraphRuntimeError
            If no lifespan-singleton :class:`Broker` is registered (i.e.
            :func:`current_broker` returns ``None`` because the FastAPI
            lifespan factory has not run yet, or the node is being
            dispatched outside an active lifespan).
        CapabilityError
            If ``ctx.capabilities`` is supplied (non-``None``) but does
            not grant ``tools:broker_request``. ``ctx.capabilities=None``
            (the POC default) skips the in-node gate; the IR-level gate
            in :func:`stargraph.graph.loop._check_node_capability` is the
            canonical enforcement point.
        """
        self._enforce_capability_gate(ctx)
        broker = current_broker()

        agent_id = self._read_state_field(state, self._config.agent_id_field)
        intent = self._read_state_field(state, self._config.intent_field)
        if not isinstance(agent_id, str):
            raise StargraphRuntimeError(
                f"BrokerNode agent_id_field must resolve to a str; got {type(agent_id).__name__}",
                field=self._config.agent_id_field,
            )
        if not isinstance(intent, str):
            raise StargraphRuntimeError(
                f"BrokerNode intent_field must resolve to a str; got {type(intent).__name__}",
                field=self._config.intent_field,
            )

        response = await broker.arequest(agent_id=agent_id, intent=intent)

        return {self._config.output_field: self._envelope(response)}

    # ------------------------------------------------------------------ #
    # internals                                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _read_state_field(state: BaseModel, field: str) -> Any:
        """Read ``field`` off the run state via :func:`getattr`.

        Missing field surfaces as :class:`AttributeError` loudly so the
        wiring bug is visible at the call site (matches the
        :class:`~stargraph.nodes.base.EchoNode` convention from task 1.1).
        """
        return getattr(state, field)

    def _enforce_capability_gate(self, ctx: ExecutionContext) -> None:
        """Defense-in-depth capability check (design §8.1).

        ``ctx.capabilities=None`` (POC default) skips the gate -- the
        loop's IR-level enforcement is the canonical line of defense.
        When supplied, the call must grant
        ``tools:broker_request`` or this raises
        :class:`CapabilityError`.
        """
        capabilities: Any = getattr(ctx, "capabilities", None)
        if capabilities is None:
            return
        if capabilities.has_permission(_REQUIRED_CAPABILITY):
            return
        raise CapabilityError(
            f"BrokerNode requires capability {_REQUIRED_CAPABILITY!r}; "
            "denied by run-side capability gate",
            capability=_REQUIRED_CAPABILITY,
            tool_id=None,
            deployment="node:broker",
        )

    @staticmethod
    def _envelope(response: Any) -> dict[str, Any]:
        """Wrap a :class:`BrokerResponse` in the stargraph provenance envelope.

        Returns a JSON-serialisable dict matching the broker response
        ``model_dump(mode="json")`` shape with an additional
        ``__stargraph_provenance__`` key carrying the design §8.1 fields:
        ``origin=tool``, ``source=nautilus``, ``external_id`` set to
        the broker's :attr:`BrokerResponse.request_id` for cross-system
        correlation.
        """
        dumped: dict[str, Any] = response.model_dump(mode="json")
        dumped["__stargraph_provenance__"] = {
            "origin": "tool",
            "source": "nautilus",
            "external_id": response.request_id,
        }
        return dumped
