# SPDX-License-Identifier: Apache-2.0
"""Consolidation-cadence streaming integration test (FR-29, AC-5.2).

Exercises the streaming ``every: 1`` cadence: each episodic put triggers
an immediate :meth:`stargraph.stores.sqlite_memory.SQLiteMemoryStore.consolidate`
call. Mirrors the Phase-4 cadence-dispatcher contract via a minimal
in-test helper (the auto-wiring into ``MemoryStore.put`` lands later -- see
the docstring on ``test_consolidation_cadence_batch``); this test pins
the data-carrier semantics so the wiring task can rely on them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from stargraph.stores.memory import ConsolidationRule, Episode, MemoryDelta
from stargraph.stores.sqlite_memory import SQLiteMemoryStore

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


_STREAM_RULE = ConsolidationRule(
    id="rule_cadence_stream_1",
    cadence={"every": 1},
    when_filter="",
    then_emits=["facts"],
)


def _episode(ep_id: str, *, idx: int) -> Episode:
    """Build a unique :class:`Episode` keyed by ``idx``."""
    return Episode(
        id=ep_id,
        content=f"alice met person_{idx}",
        timestamp=datetime.now(UTC),
        source_node="test",
        agent="knowledge-agent",
        user="alice",
        session="s1",
        metadata={
            "subject": "alice",
            "predicate": f"met_{idx}",
            "object": f"person_{idx}",
        },
    )


async def _put_with_cadence(
    memory: SQLiteMemoryStore,
    episode: Episode,
    rule: ConsolidationRule,
    counter: list[int],
    fires: list[list[MemoryDelta]],
) -> None:
    """Mirror the planned cadence dispatcher: put + maybe-fire consolidate."""
    await memory.put(
        episode,
        user=episode.user,
        session=episode.session,
        agent=episode.agent,
    )
    counter[0] += 1
    every = int(rule.cadence["every"])
    if every > 0 and counter[0] % every == 0:
        deltas = await memory.consolidate(rule)
        fires.append(deltas)


async def test_every_1_fires_each_episode(tmp_path: Path) -> None:
    """``every=1`` rule: each put triggers exactly one consolidate fire."""
    memory = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    await memory.bootstrap()

    counter = [0]
    fires: list[list[MemoryDelta]] = []

    for i in range(3):
        await _put_with_cadence(
            memory,
            _episode(f"ep-{i:03d}", idx=i),
            _STREAM_RULE,
            counter,
            fires,
        )
        assert len(fires) == i + 1, f"consolidate must fire after each put (i={i})"

    assert counter[0] == 3
    assert len(fires) == 3

    # Each fire sees the cumulative state of the store, so fire #i has i+1
    # episodes; total deltas grow 1, 2, 3.
    assert [len(deltas) for deltas in fires] == [1, 2, 3]
    for deltas in fires:
        for delta in deltas:
            assert delta.rule_id == _STREAM_RULE.id
