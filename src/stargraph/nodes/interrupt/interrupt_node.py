# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes.interrupt.interrupt_node -- :class:`InterruptNode` (FR-82, AC-14.2, design §9.2).

The node is the bypass-Fathom HITL primitive: a graph author drops it
into a graph wherever an analyst gate is required, and on dispatch it
raises the loop's typed control-flow signal (``_HitInterrupt`` carrying
an :class:`~stargraph.ir._models.InterruptAction` payload). The
:func:`~stargraph.graph.loop.execute` arm at task 1.11 catches the signal,
transitions ``state="awaiting-input"``, emits a
:class:`~stargraph.runtime.events.WaitingForInputEvent`, persists a
checkpoint, and exits cleanly. Resume happens via cold-restart through
:meth:`~stargraph.graph.run.GraphRun.resume` after
:meth:`~stargraph.graph.run.GraphRun.respond` flips state back to
``"running"`` (design §9.4).

Per design §17 Decision #1 (locked), interrupt is a control-flow
primitive, not a routing decision: the node deliberately does NOT return
a :class:`~stargraph.ir._models.InterruptAction`-shaped patch via the
field-merge surface. The dispatch path is the typed signal raise --
:data:`~stargraph.runtime.action.RoutingDecision` never carries an
interrupt variant.

Design-vs-reality: design §9.2 references an aspirational ``Node`` /
``NodeResult.interrupt(...)`` / ``SideEffectClass.read`` API that does
not exist in the codebase. The implementation matches the real
:class:`~stargraph.nodes.base.NodeBase` ABC (``async execute(state, ctx)``)
and the real :class:`stargraph.tools.spec.SideEffects` enum -- same
convention task 1.14 (:class:`~stargraph.nodes.artifacts.WriteArtifactNode`)
established. Phase 2 either promotes the design's API into the
codebase or amends the design to match.
"""

from __future__ import annotations

from datetime import timedelta  # noqa: TC003 -- pydantic resolves at runtime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from stargraph.graph.loop import _HitInterrupt  # pyright: ignore[reportPrivateUsage]
from stargraph.ir import IRBase
from stargraph.ir._models import InterruptAction
from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.tools.spec import SideEffects

if TYPE_CHECKING:
    from pydantic import BaseModel

__all__ = [
    "InterruptNode",
    "InterruptNodeConfig",
]


class InterruptNodeConfig(IRBase):
    """Pydantic config for :class:`InterruptNode` (design §9.2).

    Fields mirror :class:`stargraph.ir._models.InterruptAction` verbatim so
    a :class:`InterruptNode` can construct an
    :class:`InterruptAction` payload without lossy coercion. Inherits
    ``extra="forbid"`` from :class:`IRBase` -- unknown YAML/JSON keys
    fail loudly at validation time (FR-6, AC-9.1).
    """

    prompt: str
    """Analyst-facing prompt surfaced via WS / ``GET /runs/{id}``."""
    interrupt_payload: dict[str, Any] = Field(default_factory=dict[str, Any])
    """Opaque blob exposed via WS / respond endpoint (design §9.2, §9.4)."""
    requested_capability: str | None = None
    """Capability the human responder must hold; gate enforced at respond-time, not here."""
    timeout: timedelta | None = None
    """Wait bound; ``None`` = wait indefinitely (FR-87, NFR-22)."""
    on_timeout: Literal["halt"] | str = "halt"
    """Loop policy on timeout: ``"halt"`` (terminal) or ``"goto:<node_id>"`` (resume target)."""


class InterruptNode(NodeBase):
    """Bypass-Fathom HITL pause node (design §9.2, FR-82, AC-14.2).

    Raises :class:`stargraph.graph.loop._HitInterrupt` on dispatch carrying
    an :class:`stargraph.ir._models.InterruptAction` payload built from
    :class:`InterruptNodeConfig`. The loop's
    ``except _HitInterrupt`` arm (task 1.11) handles the state
    transition, event emission, and clean exit -- the node body itself
    does no I/O.

    ``side_effects = SideEffects.read`` because the node performs no
    Stargraph-side mutation: it only requests human input. The actual
    response is asserted as a ``stargraph.evidence`` Fathom fact by
    :meth:`~stargraph.graph.run.GraphRun.respond` post-resume -- not by
    this node.

    Configured via :class:`InterruptNodeConfig`; the config attaches at
    construction time (``InterruptNode(config=cfg)``) -- same convention
    :class:`~stargraph.nodes.artifacts.WriteArtifactNode` established
    (task 1.14).
    """

    side_effects = SideEffects.read
    """No Stargraph-side mutation -- only requests human input (design §9.2)."""
    config_model = InterruptNodeConfig
    """Pydantic config schema, surfaced for IR validators / registry tooling."""

    def __init__(self, *, config: InterruptNodeConfig) -> None:
        self._config = config

    @property
    def config(self) -> InterruptNodeConfig:
        """Public read-only handle on the validated config (used by tests)."""
        return self._config

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        """Raise :class:`_HitInterrupt` carrying the configured :class:`InterruptAction`.

        Per design §17 Decision #1, this is the dispatch surface for
        HITL pause -- the loop arm at :func:`stargraph.graph.loop.execute`
        catches the signal, transitions ``state="awaiting-input"``,
        emits :class:`~stargraph.runtime.events.WaitingForInputEvent`, and
        exits cleanly. The node never returns a patch dict; its return
        type is annotated for ABC-shape parity only.
        """
        del state, ctx  # unused: signal is raise-only; loop owns state
        action = InterruptAction(
            prompt=self._config.prompt,
            interrupt_payload=self._config.interrupt_payload,
            requested_capability=self._config.requested_capability,
            timeout=self._config.timeout,
            on_timeout=self._config.on_timeout,
        )
        raise _HitInterrupt(action)
