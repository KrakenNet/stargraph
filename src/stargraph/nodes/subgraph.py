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

Two execution modes (P3b):

* **Sequential** (the original FR-7 minimum): no live-routable child
  rules -- every child runs once in declaration order.
* **Rule-routed**: the child IR's rules compile via
  :func:`stargraph.fathom.build_ir_routing` into a *child-owned* Fathom
  engine (isolated working memory -- parent facts are never touched).
  Each child tick mirrors the child state, evaluates, and routes
  goto/halt/continue exactly like the parent loop, so subgraphs can
  loop internally (fix-loops, judge-refine cycles). State crosses the
  boundary through explicit input/output projection
  (:class:`SubGraphNodeConfig`): parent -> child on entry, child ->
  parent on halt. ``interrupt``/``parallel`` decisions inside a
  subgraph are unsupported v1 and fail loudly.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel as _PydanticBaseModel
from pydantic import ConfigDict, Field
from pydantic import ValidationError as _PydanticValidationError

from stargraph.errors import StargraphRuntimeError
from stargraph.ir._models import GotoAction, HaltAction
from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.runtime.action import ContinueAction, translate_actions
from stargraph.runtime.dispatch import (
    _assert_specs,  # pyright: ignore[reportPrivateUsage]
    _retract_stargraph_actions,  # pyright: ignore[reportPrivateUsage]
)
from stargraph.runtime.events import TransitionEvent

if TYPE_CHECKING:
    from pydantic import BaseModel

    from stargraph.ir._models import IRDocument

__all__ = ["SubGraphContext", "SubGraphNode", "SubGraphNodeConfig"]


class SubGraphNodeConfig(_PydanticBaseModel):
    """``NodeSpec.config`` schema for rule-routed ``kind: subgraph``.

    ``inputs`` maps child state fields to parent state fields (entry
    projection); ``outputs`` maps parent state fields to child state
    fields (exit projection). Unmapped fields project by shared name.
    ``max_steps`` bounds the internal routing loop -- a child rule set
    that never halts fails loudly instead of spinning forever.
    """

    model_config = ConfigDict(extra="forbid")

    inputs: dict[str, str] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    max_steps: int = Field(default=50, ge=1, le=10_000)


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

    def __init__(
        self,
        *,
        subgraph_id: str,
        children: list[NodeBase],
        child_ir: IRDocument | None = None,
        child_nodes: dict[str, NodeBase] | None = None,
        child_state_cls: type[BaseModel] | None = None,
        child_fathom: Any = None,
        config: SubGraphNodeConfig | None = None,
    ) -> None:
        self._subgraph_id = subgraph_id
        self._children: list[NodeBase] = list(children)
        # Rule-routed mode wiring (all-or-nothing; registry builder supplies
        # the full set when the child IR has live-routable rules).
        self._child_ir = child_ir
        self._child_nodes: dict[str, NodeBase] = dict(child_nodes or {})
        self._child_state_cls = child_state_cls
        self._child_fathom = child_fathom
        self._config = config or SubGraphNodeConfig()

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
        if self._child_fathom is not None:
            return await self._execute_routed(state, ctx, sub_ctx)

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
    # rule-routed mode                                                   #
    # ------------------------------------------------------------------ #

    async def _execute_routed(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
        sub_ctx: SubGraphContext,
    ) -> dict[str, Any]:
        """Drive the child IR's own Fathom-routed loop to a halt.

        Mirrors the parent loop's per-tick shape (execute -> merge ->
        mirror -> evaluate -> route) against the child-owned engine;
        emits one :class:`TransitionEvent` per tick with
        ``branch_id == subgraph_id`` and ``reason == decision.kind``.
        """
        assert self._child_ir is not None and self._child_state_cls is not None
        child_state = self._project_in(state)
        node_ids = [n.id for n in self._child_ir.nodes]
        current = node_ids[0]

        for step in range(self._config.max_steps):
            node = self._child_nodes.get(current)
            if node is None:
                raise StargraphRuntimeError(
                    f"subgraph {self._subgraph_id!r}: child rule routed to "
                    f"unknown node {current!r}",
                    subgraph_id=self._subgraph_id,
                )
            outputs = await node.execute(child_state, ctx)
            child_state = child_state.model_copy(update=outputs)

            specs = self._child_fathom.mirror_state(child_state, annotations={"node_id": current})
            await asyncio.to_thread(_assert_specs, self._child_fathom, specs, sub_ctx.run_id, step)
            actions: list[Any] = await asyncio.to_thread(self._child_fathom.evaluate)
            if actions:
                await asyncio.to_thread(_retract_stargraph_actions, self._child_fathom)
            decision = translate_actions(actions)

            if isinstance(decision, HaltAction):
                await self._emit_child_transition(
                    sub_ctx, step=step, from_node=current, to_node="", reason="halt"
                )
                return self._project_out(child_state, state)
            if isinstance(decision, GotoAction):
                await self._emit_child_transition(
                    sub_ctx,
                    step=step,
                    from_node=current,
                    to_node=decision.target,
                    reason="goto",
                )
                current = decision.target
                continue
            if isinstance(decision, ContinueAction):
                idx = node_ids.index(current)
                next_id = node_ids[idx + 1] if idx + 1 < len(node_ids) else ""
                await self._emit_child_transition(
                    sub_ctx,
                    step=step,
                    from_node=current,
                    to_node=next_id,
                    reason="continue",
                )
                if not next_id:
                    return self._project_out(child_state, state)
                current = next_id
                continue
            raise StargraphRuntimeError(
                f"subgraph {self._subgraph_id!r}: unsupported routing decision "
                f"{decision.kind!r} inside a subgraph (v1 supports goto/halt/continue)",
                subgraph_id=self._subgraph_id,
                decision=decision.kind,
            )

        raise StargraphRuntimeError(
            f"subgraph {self._subgraph_id!r} exceeded max_steps="
            f"{self._config.max_steps} without halting",
            subgraph_id=self._subgraph_id,
            max_steps=self._config.max_steps,
        )

    def _project_in(self, parent_state: BaseModel) -> BaseModel:
        """Parent state -> child state (explicit ``inputs`` map, then shared names)."""
        assert self._child_state_cls is not None
        kwargs: dict[str, Any] = {}
        for field in self._child_state_cls.model_fields:
            parent_field = self._config.inputs.get(field)
            if parent_field is not None:
                if not hasattr(parent_state, parent_field):
                    raise StargraphRuntimeError(
                        f"subgraph {self._subgraph_id!r}: inputs maps child field "
                        f"{field!r} to parent field {parent_field!r} which does "
                        "not exist on the run state",
                        subgraph_id=self._subgraph_id,
                    )
                kwargs[field] = getattr(parent_state, parent_field)
            elif hasattr(parent_state, field):
                kwargs[field] = getattr(parent_state, field)
        try:
            return self._child_state_cls(**kwargs)
        except _PydanticValidationError as e:
            raise StargraphRuntimeError(
                f"subgraph {self._subgraph_id!r}: cannot build child state from parent state: {e}",
                subgraph_id=self._subgraph_id,
            ) from e

    def _project_out(self, child_state: BaseModel, parent_state: BaseModel) -> dict[str, Any]:
        """Child state -> parent output dict (explicit ``outputs`` map, then shared names)."""
        if self._config.outputs:
            out: dict[str, Any] = {}
            for parent_field, child_field in self._config.outputs.items():
                if not hasattr(child_state, child_field):
                    raise StargraphRuntimeError(
                        f"subgraph {self._subgraph_id!r}: outputs maps parent field "
                        f"{parent_field!r} to child field {child_field!r} which does "
                        "not exist on the child state",
                        subgraph_id=self._subgraph_id,
                    )
                out[parent_field] = getattr(child_state, child_field)
            return out
        return {
            field: getattr(child_state, field)
            for field in type(child_state).model_fields
            if hasattr(parent_state, field)
        }

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
        reason: str = "subgraph",
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
            reason=reason,
        )
        await ctx.bus.send(event, fathom=ctx.fathom)
