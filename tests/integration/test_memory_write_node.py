# SPDX-License-Identifier: Apache-2.0
"""Integration test for :class:`stargraph.nodes.memory.MemoryWriteNode` (FR-27, AC-5.1).

Two checks:

* ``MemoryWriteNode`` records an :class:`Episode` into a
  :class:`SQLiteMemoryStore` under the full ``(user, session, agent)``
  3-tuple read off the :class:`ExecutionContext`.
* The class-level replay annotations advertise ``SideEffects.write`` and
  ``ReplayPolicy.must_stub`` so the engine's replay harness stubs the
  node rather than re-executing the side-effecting write (FR-33,
  design §3.4.2).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel

from stargraph.nodes.memory import MemoryWriteNode
from stargraph.stores.memory import Episode
from stargraph.stores.sqlite_memory import SQLiteMemoryStore
from stargraph.tools.spec import ReplayPolicy, SideEffects

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


class _State(BaseModel):
    """Run state carrying the episode the node will write."""

    episode: Episode


class _Ctx:
    """:class:`ExecutionContext` impl carrying the full 3-tuple scope."""

    run_id: str = "run-mw-1"
    user: str = "Alice"
    session: str = "S1"
    agent: str = "rag"


async def test_memory_write_node_records_full_3_tuple(tmp_path: Path) -> None:
    """Episode is written under ``(user, session, agent)`` from ``ctx``."""
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.bootstrap()

    episode = Episode(
        id="ep-mw-1",
        content="hello memory",
        timestamp=datetime.now(UTC),
        source_node="node-mw",
        agent="rag",
        user="Alice",
        session="S1",
    )
    node = MemoryWriteNode(store)
    out = await node.execute(_State(episode=episode), _Ctx())

    assert out == {"memory_written": True, "episode_id": "ep-mw-1"}

    # Round-trip through the exact 3-tuple confirms the scope-key encoding.
    rows = await store.recent("Alice", session="S1", agent="rag", limit=10)
    assert len(rows) == 1
    assert rows[0].id == "ep-mw-1"
    assert rows[0].content == "hello memory"


def test_memory_write_node_replay_policy_must_stub() -> None:
    """Replay annotations advertise ``write`` + ``must_stub`` (FR-33)."""
    assert MemoryWriteNode.SIDE_EFFECTS is SideEffects.write
    assert MemoryWriteNode.REPLAY_POLICY is ReplayPolicy.must_stub
