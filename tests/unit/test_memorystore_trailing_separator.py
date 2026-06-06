# SPDX-License-Identifier: Apache-2.0
"""Trailing-separator scope key prevents prefix collisions (FR-5, FR-27).

The :class:`stargraph.stores.sqlite_memory.SQLiteMemoryStore` encodes the
``(user, session, agent)`` 3-tuple as a *trailing-separator* key
(``/user/{user}/session/{session}/agent/{agent}/``). Without the
trailing slash, ``LIKE '/user/Alice%'`` would match ``Alice2`` rows
via prefix collision -- this test pins the no-collision invariant.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from stargraph.stores.memory import Episode
from stargraph.stores.sqlite_memory import SQLiteMemoryStore

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


async def test_alice_vs_alice2_no_collision(tmp_path: Path) -> None:
    """``recent(user='Alice')`` returns only the Alice episode, not the Alice2 episode."""
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.bootstrap()

    ts = datetime.now(UTC)
    alice_ep = Episode(
        id="ep-alice",
        content="alice content",
        timestamp=ts,
        source_node="n",
        agent="rag",
        user="Alice",
        session="S1",
    )
    alice2_ep = Episode(
        id="ep-alice2",
        content="alice2 content",
        timestamp=ts,
        source_node="n",
        agent="rag",
        user="Alice2",
        session="S1",
    )
    await store.put(alice_ep, user="Alice", session="S1", agent="rag")
    await store.put(alice2_ep, user="Alice2", session="S1", agent="rag")

    rows = await store.recent("Alice", limit=10)
    assert {r.id for r in rows} == {"ep-alice"}
