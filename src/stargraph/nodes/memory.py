# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes.memory -- :class:`MemoryWriteNode` (FR-27, design §3.4).

POC node that persists a single :class:`~stargraph.stores.memory.Episode`
into an injected memory-store provider (structurally a
:class:`~stargraph.stores.memory.MemoryStore`). The
``(user, session, agent)`` triple is read from the per-run
:class:`~stargraph.nodes.base.ExecutionContext`; Phase 1's
:class:`ExecutionContext` Protocol is intentionally minimal
(``run_id`` only), so the node falls back to ``"anon"`` /
``"default"`` / ``"default"`` when the concrete context object lacks
the optional fields. Phase 2 tightens the Protocol (design §3.4).

Side-effect / replay annotations follow the engine ToolSpec extension
(FR-33, design 3.4.2): :class:`SideEffects.write` plus
:class:`ReplayPolicy.must_stub` advertise that this node mutates
external state and must be stubbed under replay.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.tools.spec import ReplayPolicy, SideEffects

if TYPE_CHECKING:
    from pydantic import BaseModel

    from stargraph.stores.memory import Episode, MemoryStore


__all__ = ["MemoryWriteNode"]


class MemoryWriteNode(NodeBase):
    """Persist ``state.<episode_field>`` into a ``MemoryStore`` (FR-27).

    POC scope: reads the :class:`Episode` off the named state field,
    extracts the ``(user, session, agent)`` scope from ``ctx`` (with
    safe fallbacks while the :class:`ExecutionContext` Protocol is
    still Phase-1 minimal), and calls
    :meth:`stargraph.stores.memory.MemoryStore.put`.

    :param memory_store: The injected :class:`MemoryStore` provider
        (e.g. :class:`stargraph.stores.sqlite_memory.SQLiteMemoryStore`).
    :param episode_field: Name of the state field carrying the
        :class:`Episode` payload; defaults to ``"episode"``.
    """

    SIDE_EFFECTS = SideEffects.write
    REPLAY_POLICY = ReplayPolicy.must_stub

    def __init__(
        self,
        memory_store: MemoryStore,
        *,
        episode_field: str = "episode",
    ) -> None:
        self._memory_store: MemoryStore = memory_store
        self._episode_field: str = episode_field

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        """Write ``state.<episode_field>`` into the store (NodeBase, FR-1).

        :returns: ``{"memory_written": True, "episode_id": episode.id}``
            for the field-merge registry to fold into the next state.
        """
        episode: Episode = getattr(state, self._episode_field)
        user = getattr(ctx, "user", "anon")
        session = getattr(ctx, "session", "default")
        agent = getattr(ctx, "agent", "default")
        await self._memory_store.put(
            episode,
            user=user,
            session=session,
            agent=agent,
        )
        return {"memory_written": True, "episode_id": episode.id}
