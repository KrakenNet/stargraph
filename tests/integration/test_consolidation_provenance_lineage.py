# SPDX-License-Identifier: Apache-2.0
"""Consolidation provenance lineage end-to-end test (FR-28, FR-31, design §3.14).

Pins the contract that every fact landed via the
:meth:`SQLiteMemoryStore.consolidate` -> :meth:`SQLiteFactStore.apply_delta`
seam carries a ``lineage`` row whose ``source_episode_ids`` dereference
back to the originating :class:`Episode.id` rows the consolidation pass
actually saw (design §3.14 -- lineage is NEVER optional).

Three invariants are exercised against a 5-episode batch:

1. ``MemoryDelta.source_episode_ids`` is populated for every emitted delta.
2. ``apply_delta`` writes that id list into ``Fact.lineage[*].source_episode_ids``
   so the FactStore row points back at the originating memory rows.
3. End-to-end: every id in ``fact.lineage[*].source_episode_ids`` is a
   real ``Episode.id`` that exists in the originating
   :class:`SQLiteMemoryStore` (``recent`` widening read), not a synthetic
   placeholder -- the lineage column is dereferenceable, not just present.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from stargraph.stores.fact import FactPattern
from stargraph.stores.memory import (
    AddDelta,
    ConsolidationRule,
    Episode,
    UpdateDelta,
)
from stargraph.stores.sqlite_fact import SQLiteFactStore
from stargraph.stores.sqlite_memory import SQLiteMemoryStore

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


_RULE = ConsolidationRule(
    id="rule_lineage_v1",
    cadence={"every": 1},
    when_filter="",
    then_emits=["facts"],
)


def _episode(ep_id: str, *, subject: str, predicate: str, obj: str) -> Episode:
    """Build an :class:`Episode` with subject/predicate/object metadata."""
    return Episode(
        id=ep_id,
        content=f"{subject} {predicate} {obj}",
        timestamp=datetime.now(UTC),
        source_node="test",
        agent="knowledge-agent",
        user="alice",
        session="s1",
        metadata={"subject": subject, "predicate": predicate, "object": obj},
    )


async def _bootstrap(tmp_path: Path) -> tuple[SQLiteMemoryStore, SQLiteFactStore]:
    memory = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    fact = SQLiteFactStore(tmp_path / "facts.sqlite")
    await memory.bootstrap()
    await fact.bootstrap()
    return memory, fact


async def test_delta_source_episode_ids_populated(tmp_path: Path) -> None:
    """5 episodes -> consolidate -> every MemoryDelta has source_episode_ids."""
    memory, _ = await _bootstrap(tmp_path)
    episodes = [
        _episode(f"ep-{i}", subject="alice", predicate=f"knows_{i}", obj=f"person_{i}")
        for i in range(5)
    ]
    for ep in episodes:
        await memory.put(ep, user=ep.user, session=ep.session, agent=ep.agent)

    deltas = await memory.consolidate(_RULE)
    assert len(deltas) == 5

    seen_ids: set[str] = set()
    for delta in deltas:
        assert delta.source_episode_ids, f"delta {delta!r} missing source_episode_ids"
        seen_ids.update(delta.source_episode_ids)
    assert seen_ids == {ep.id for ep in episodes}


async def test_apply_delta_writes_lineage_with_source_episode_ids(tmp_path: Path) -> None:
    """apply_delta -> fact.lineage rows carry source_episode_ids back to episodes."""
    memory, facts = await _bootstrap(tmp_path)
    episodes = [
        _episode(f"ep-{i}", subject="alice", predicate=f"knows_{i}", obj=f"person_{i}")
        for i in range(5)
    ]
    for ep in episodes:
        await memory.put(ep, user=ep.user, session=ep.session, agent=ep.agent)

    deltas = await memory.consolidate(_RULE)
    for delta in deltas:
        await facts.apply_delta(delta)

    stored = await facts.query(FactPattern(user="alice", agent="knowledge-agent"))
    assert stored, "no facts pinned"
    for fact in stored:
        assert fact.lineage, f"fact {fact.id!r} missing lineage"
        # Every lineage row carries the rule id and a non-empty episode list.
        for entry in fact.lineage:
            assert entry["rule_id"] == _RULE.id
            assert entry["source_episode_ids"], (
                f"lineage row on {fact.id!r} missing source_episode_ids"
            )


async def test_fact_lineage_dereferences_to_real_episode_ids(tmp_path: Path) -> None:
    """End-to-end: fact.lineage -> source_episode_ids resolves to real Episode.id rows."""
    memory, facts = await _bootstrap(tmp_path)
    episodes = [
        _episode(f"ep-{i}", subject="alice", predicate=f"knows_{i}", obj=f"person_{i}")
        for i in range(5)
    ]
    for ep in episodes:
        await memory.put(ep, user=ep.user, session=ep.session, agent=ep.agent)

    deltas = await memory.consolidate(_RULE)
    # All 5 deltas should be ADDs (distinct subject/predicate keys, no dedup).
    assert all(isinstance(d, AddDelta) for d in deltas)
    for delta in deltas:
        await facts.apply_delta(delta)

    # Snapshot the originating Episode.id set via the widening read the
    # MemoryStore actually exposes -- this is the "dereference" path a
    # downstream auditor would walk.
    stored_episodes = await memory.recent(user="alice", limit=100)
    real_episode_ids = {ep.id for ep in stored_episodes}
    assert real_episode_ids == {f"ep-{i}" for i in range(5)}

    # Every lineage row's source_episode_ids must dereference back to a
    # real episode id -- no orphan ids may slip through the seam.
    facts_pinned = await facts.query(FactPattern(user="alice", agent="knowledge-agent"))
    assert facts_pinned, "expected at least one promoted fact"
    seen_lineage_ids: set[str] = set()
    for fact in facts_pinned:
        for entry in fact.lineage:
            for ep_id in entry["source_episode_ids"]:
                assert ep_id in real_episode_ids, (
                    f"lineage id {ep_id!r} on fact {fact.id!r} does not "
                    f"dereference to any stored Episode.id"
                )
                seen_lineage_ids.add(ep_id)

    # All 5 originating episodes must appear in the union of lineage links.
    assert seen_lineage_ids == real_episode_ids


async def test_update_delta_lineage_preserves_originating_episode(tmp_path: Path) -> None:
    """Intra-batch dedup -> UpdateDelta lineage still points at the newer episode."""
    memory, facts = await _bootstrap(tmp_path)
    older = _episode("ep-older", subject="alice", predicate="lives_in", obj="berlin")
    newer = _episode("ep-newer", subject="alice", predicate="lives_in", obj="munich")
    await memory.put(older, user=older.user, session=older.session, agent=older.agent)
    await memory.put(newer, user=newer.user, session=newer.session, agent=newer.agent)

    deltas = await memory.consolidate(_RULE)
    assert len(deltas) == 2
    update = next(d for d in deltas if isinstance(d, UpdateDelta))
    assert update.source_episode_ids == ["ep-newer"]
    assert update.replaces == ["ep-older"]

    for delta in deltas:
        await facts.apply_delta(delta)

    remaining = await facts.query(FactPattern(user="alice", agent="knowledge-agent"))
    # The older fact id was unpinned; the surviving fact's lineage points
    # at the newer originating episode id.
    surviving_ids: set[str] = set()
    for fact in remaining:
        for entry in fact.lineage:
            surviving_ids.update(entry["source_episode_ids"])
    assert "ep-newer" in surviving_ids
