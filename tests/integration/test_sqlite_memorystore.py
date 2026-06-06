# SPDX-License-Identifier: Apache-2.0
"""Integration tests for :class:`stargraph.stores.sqlite_memory.SQLiteMemoryStore`.

Covers the widening LIKE read (FR-5, FR-13, FR-27) at the basic shape (3-tuple put,
2-tuple recent). The exhaustive 3-tuple -> 2-tuple -> 1-tuple cascade
lives in ``test_episodic_widening_read.py``; the trailing-separator
no-collision invariant lives in
``tests/unit/test_memorystore_trailing_separator.py``.
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


async def test_put_recent_widening(tmp_path: Path) -> None:
    """Episode written at 3-tuple scope is visible to a 2-tuple widening read."""
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.bootstrap()

    episode = Episode(
        id="ep-1",
        content="hello",
        timestamp=datetime.now(UTC),
        source_node="node-a",
        agent="rag",
        user="Alice",
        session="S1",
    )
    await store.put(episode, user="Alice", session="S1", agent="rag")

    # Widen on agent (2-tuple): omit ``agent`` -> ``%`` wildcard.
    rows = await store.recent("Alice", session="S1", limit=10)
    assert len(rows) == 1
    assert rows[0].id == "ep-1"
    assert rows[0].content == "hello"
