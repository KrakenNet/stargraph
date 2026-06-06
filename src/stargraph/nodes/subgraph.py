# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes.subgraph -- :class:`SubGraphNode` (FR-7, design §3.9.4, §5).

Per FR-7, a sub-graph is **not** a new IR construct: it is a node whose
body executes a child sequence of :class:`~stargraph.nodes.base.NodeBase`
instances inside the parent run's execution context. The same event bus,
the same ``run_id``, and the same checkpointer are reused -- the only
distinguishing surface is ``branch_id``, which tags every event the sub
emits so downstream consumers (audit log, replay, CLI ``inspect``) can
reconstruct the parent/child lineage.

Provenance lineage (Done-when):
* The child events carry ``run_id == parent.run_id`` (the parent's
  identity propagates verbatim -- no new ``run_id`` is minted; FR-7
  treats the sub-graph as a logical fragment of the parent run).
* The child events carry ``branch_id == subgraph_id``; the parent's own
  events carry ``branch_id is None``. The two are interleaved on the
  same bus.

This Phase-3 module deliberately ships the minimum surface FR-7 needs:
sequential child execution + per-child :class:`TransitionEvent` emission
on the parent's bus. The reference recipe in design §3.9.4
(``training-subgraph.yaml``) only relies on this contract; richer
features (parallel-inside-sub, child checkpoints, child Fathom mirror)
land in later tasks once their parent paths exist.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.runtime.events import TransitionEvent

if TYPE_CHECKING:
    from pydantic import BaseModel

__all__ = ["SubGraphContext", "SubGraphNode"]


@runtime_checkable
class SubGraphContext(Protocol):
    """Structural surface SubGraphNode needs from the run context.

    The Phase-1 :class:`~stargraph.nodes.base.ExecutionContext` Protocol only
    pins ``run_id``; SubGraphNode additionally requires the parent run's
    event bus so the child events stream alongside the parent's. The
    real :class:`~stargraph.graph.run.GraphRun` satisfies this surface (it
    exposes ``run_id`` and ``bus``); tests pass duck-typed contexts.

    ``bus`` and ``fathom`` are :data:`Any`-typed because the runtime bus
    (``EventBus``) is a concrete class, not a Protocol -- structural
    typing here keeps tests free to substitute lightweight recorders.
    """

    run_id: str
    bus: Any
    fathom: Any


class SubGraphNode(NodeBase):
    """Execute a child sequence of nodes inside the parent run (FR-7).

    Each child :class:`NodeBase` runs in declaration order; its outputs
    are merged left-to-right into the in-flight state via ``model_copy``
    (the same last-write-wins convention the parent loop uses pre-FR-11
    typed merge). Per child, a :class:`TransitionEvent` is published on
    the parent's bus with:

    * ``run_id`` = parent ``ctx.run_id`` (provenance lineage),
    * ``branch_id`` = ``self.subgraph_id`` (lineage discriminator),
    * ``from_node`` = child id, ``to_node`` = next child id (or ``""``
      on the terminal child to mirror the parent loop's convention).

    ``ctx`` must satisfy the :class:`SubGraphContext` Protocol (the
    real :class:`GraphRun` does); when a child needs the same context
    surface (e.g. another :class:`SubGraphNode` nested inside), it is
    threaded through verbatim so nested sub-graphs preserve lineage.

    Args:
        subgraph_id: Stable identifier stamped onto every child event's
            ``branch_id`` field. Conventionally matches the parent
            ``NodeSpec.id`` so the lineage line is searchable.
        children: Ordered list of :class:`NodeBase` to dispatch. Empty
            list is legal (degenerate sub-graph: no events, no merges).
    """

    def __init__(self, *, subgraph_id: str, children: list[NodeBase]) -> None:
        self._subgraph_id = subgraph_id
        self._children: list[NodeBase] = list(children)

    @property
    def subgraph_id(self) -> str:
        """Public read-only handle on the lineage discriminator (used by tests)."""
        return self._subgraph_id

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        """Run every child against the in-flight state on the parent bus.

        Returns the cumulative dict of outputs (last-write-wins on key
        collisions, mirroring the parent loop's merge convention so
        :func:`stargraph.runtime.dispatch.dispatch_node` can apply the
        result with a single ``state.model_copy(update=outputs)``).
        """
        sub_ctx: SubGraphContext = self._require_subgraph_context(ctx)

        accumulated: dict[str, Any] = {}
        cursor: BaseModel = state
        n_children = len(self._children)
        for idx, child in enumerate(self._children):
            child_id = self._child_id(child, idx)
            outputs = await child.execute(cursor, ctx)
            accumulated.update(outputs)
            cursor = cursor.model_copy(update=outputs)

            next_id = (
                self._child_id(self._children[idx + 1], idx + 1) if idx + 1 < n_children else ""
            )
            await self._emit_child_transition(
                sub_ctx,
                step=idx,
                from_node=child_id,
                to_node=next_id,
            )
        return accumulated

    # ------------------------------------------------------------------ #
    # internals                                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _require_subgraph_context(ctx: ExecutionContext) -> SubGraphContext:
        """Narrow ``ctx`` to the :class:`SubGraphContext` surface or raise loudly.

        The Phase-1 :class:`ExecutionContext` Protocol only declares
        ``run_id``; SubGraphNode additionally requires ``bus`` (and
        opportunistically ``fathom``). A missing ``bus`` is a wiring
        bug, not a recoverable runtime condition, so we raise rather
        than silently dropping events (FR-6 force-loud).
        """
        if not isinstance(ctx, SubGraphContext):
            raise AttributeError(
                "SubGraphNode requires an execution context with `run_id`, "
                "`bus`, and `fathom`; got " + type(ctx).__name__
            )
        return ctx

    @staticmethod
    def _child_id(child: NodeBase, idx: int) -> str:
        """Best-effort stable id for a child node (falls back to positional)."""
        nid = getattr(child, "id", None)
        if isinstance(nid, str) and nid:
            return nid
        return f"child-{idx}"

    async def _emit_child_transition(
        self,
        ctx: SubGraphContext,
        *,
        step: int,
        from_node: str,
        to_node: str,
    ) -> None:
        """Publish one child :class:`TransitionEvent` onto the parent bus."""
        event = TransitionEvent(
            run_id=ctx.run_id,
            step=step,
            branch_id=self._subgraph_id,
            ts=datetime.now(UTC),
            from_node=from_node,
            to_node=to_node,
            rule_id="",
            reason="subgraph",
        )
        await ctx.bus.send(event, fathom=ctx.fathom)
