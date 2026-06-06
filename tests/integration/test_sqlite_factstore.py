# SPDX-License-Identifier: Apache-2.0
"""Integration tests for :class:`stargraph.stores.sqlite_fact.SQLiteFactStore` (FR-6, FR-13).

Covers four observable behaviours task 3.20 calls out:

1. ``test_pin_query_roundtrip`` -- ``pin`` then ``query`` returns the fact.
2. ``test_unpin_marks_replaces`` -- ``pin`` then ``unpin`` returns empty
   query results (POC ``unpin`` is a hard delete; an audit table will
   replace this in Phase 3).
3. ``test_apply_delta_paths`` -- exercise Add / Update / Delete / Noop
   paths through :meth:`apply_delta` and verify each updates the fact
   rows correctly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from stargraph.stores.fact import Fact, FactPattern
from stargraph.stores.memory import AddDelta, DeleteDelta, NoopDelta, UpdateDelta
from stargraph.stores.sqlite_fact import SQLiteFactStore

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


def _make_fact(fact_id: str = "f-1", subject: str = "Alice") -> Fact:
    """Build a :class:`Fact` with the minimum lineage / payload for round-trip tests."""
    return Fact(
        id=fact_id,
        user="Alice",
        agent="rag",
        payload={"subject": subject, "predicate": "likes", "object": "tea"},
        lineage=[
            {
                "rule_id": "r-1",
                "source_episode_ids": ["ep-1"],
                "promotion_ts": datetime.now(UTC).isoformat(),
            }
        ],
        confidence=0.9,
        pinned_at=datetime.now(UTC),
    )


async def test_pin_query_roundtrip(tmp_path: Path) -> None:
    """``pin`` then ``query`` (by user) returns the pinned fact."""
    store = SQLiteFactStore(tmp_path / "facts.db")
    await store.bootstrap()

    fact = _make_fact()
    await store.pin(fact)

    rows = await store.query(FactPattern(user="Alice"))
    assert len(rows) == 1
    assert rows[0].id == "f-1"
    assert rows[0].payload == fact.payload
    assert rows[0].lineage == fact.lineage


async def test_unpin_marks_replaces(tmp_path: Path) -> None:
    """``pin`` then ``unpin`` removes the fact from query results."""
    store = SQLiteFactStore(tmp_path / "facts.db")
    await store.bootstrap()

    fact = _make_fact()
    await store.pin(fact)
    assert len(await store.query(FactPattern(user="Alice"))) == 1

    await store.unpin(fact.id)
    assert await store.query(FactPattern(user="Alice")) == []


async def test_apply_delta_paths(tmp_path: Path) -> None:
    """Exercise Add / Update / Delete / Noop paths through :meth:`apply_delta`."""
    store = SQLiteFactStore(tmp_path / "facts.db")
    await store.bootstrap()

    promotion_ts = datetime.now(UTC)

    # ADD -- new fact lands.
    add_delta = AddDelta(
        kind="add",
        fact_payload={
            "id": "f-1",
            "user": "Alice",
            "agent": "rag",
            "subject": "Alice",
            "predicate": "likes",
            "object": "tea",
        },
        source_episode_ids=["ep-1"],
        promotion_ts=promotion_ts,
        rule_id="r-1",
        confidence=0.9,
    )
    await store.apply_delta(add_delta)
    rows = await store.query(FactPattern(user="Alice"))
    assert len(rows) == 1
    assert rows[0].id == "f-1"

    # UPDATE -- replaces f-1, pins f-2.
    update_delta = UpdateDelta(
        kind="update",
        replaces=["f-1"],
        fact_payload={
            "id": "f-2",
            "user": "Alice",
            "agent": "rag",
            "subject": "Alice",
            "predicate": "likes",
            "object": "coffee",
        },
        source_episode_ids=["ep-2"],
        promotion_ts=promotion_ts,
        rule_id="r-1",
        confidence=0.9,
    )
    await store.apply_delta(update_delta)
    rows = await store.query(FactPattern(user="Alice"))
    assert len(rows) == 1
    assert rows[0].id == "f-2"
    assert rows[0].payload["object"] == "coffee"

    # NOOP -- audit-only; no mutation.
    noop_delta = NoopDelta(
        kind="noop",
        source_episode_ids=["ep-3"],
        promotion_ts=promotion_ts,
        rule_id="r-1",
        confidence=1.0,
    )
    await store.apply_delta(noop_delta)
    rows = await store.query(FactPattern(user="Alice"))
    assert len(rows) == 1
    assert rows[0].id == "f-2"

    # DELETE -- unpins f-2.
    delete_delta = DeleteDelta(
        kind="delete",
        replaces=["f-2"],
        source_episode_ids=["ep-4"],
        promotion_ts=promotion_ts,
        rule_id="r-1",
        confidence=1.0,
    )
    await store.apply_delta(delete_delta)
    assert await store.query(FactPattern(user="Alice")) == []
