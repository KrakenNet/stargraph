# SPDX-License-Identifier: Apache-2.0
"""Episodic widening-read semantics across the full scope cascade (FR-5, FR-27).

Three cases covering the LIKE-pattern widening at every truncation
of the ``(user, session, agent)`` scope:

1. 3-tuple put -> 2-tuple recent (omit ``agent``).
2. 2-tuple recent -> 1-tuple recent (omit ``session`` + ``agent``).
3. 1-tuple put -> global recent (omit everything; user is required).

Note: the current :class:`stargraph.stores.sqlite_memory.SQLiteMemoryStore`
``recent`` API requires a ``user`` argument (no global "every user"
read). The third case therefore checks the 1-tuple put -> 1-tuple
recent collapse (no narrowing required) which is the operator-visible
"global per-user" widen.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from stargraph.stores.memory import Episode
from stargraph.stores.sqlite_memory import SQLiteMemoryStore

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


def _episode(ep_id: str, user: str, session: str, agent: str) -> Episode:
    """Build an :class:`Episode` with the given scope tuple."""
    return Episode(
        id=ep_id,
        content=f"content-{ep_id}",
        timestamp=datetime.now(UTC),
        source_node="n",
        agent=agent,
        user=user,
        session=session,
    )


async def test_three_tuple_to_two_tuple_widening(tmp_path: Path) -> None:
    """Episode put at 3-tuple is visible to 2-tuple ``recent`` (agent widened)."""
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.bootstrap()

    ep = _episode("ep-1", "Alice", "S1", "rag")
    await store.put(ep, user="Alice", session="S1", agent="rag")

    rows = await store.recent("Alice", session="S1", limit=10)
    assert {r.id for r in rows} == {"ep-1"}


async def test_two_tuple_to_one_tuple_widening(tmp_path: Path) -> None:
    """Episode put at 3-tuple is visible to 1-tuple ``recent`` (session+agent widened)."""
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.bootstrap()

    ep_a = _episode("ep-a", "Alice", "S1", "rag")
    ep_b = _episode("ep-b", "Alice", "S2", "summarizer")
    await store.put(ep_a, user="Alice", session="S1", agent="rag")
    await store.put(ep_b, user="Alice", session="S2", agent="summarizer")

    rows = await store.recent("Alice", limit=10)
    assert {r.id for r in rows} == {"ep-a", "ep-b"}


async def test_one_tuple_global_widening(tmp_path: Path) -> None:
    """Single 3-tuple put is visible to a 1-tuple (user-only) ``recent``.

    The current ``recent`` API requires ``user``; this test pins the
    user-scoped global widen (omit ``session`` + ``agent``) -- the
    "every user" global read is not part of the public API today.
    """
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.bootstrap()

    ep = _episode("ep-1", "Alice", "S1", "rag")
    await store.put(ep, user="Alice", session="S1", agent="rag")

    rows = await store.recent("Alice", limit=10)
    assert {r.id for r in rows} == {"ep-1"}
