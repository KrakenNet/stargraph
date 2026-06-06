# SPDX-License-Identifier: Apache-2.0
"""Consolidation-cadence batch integration test (FR-29, AC-5.2).

Exercises the ``every: N`` cadence semantics declared on
:class:`stargraph.stores.memory.ConsolidationRule` against
:meth:`stargraph.stores.sqlite_memory.SQLiteMemoryStore.consolidate`.

The auto-dispatch wiring (a hook on :meth:`MemoryStore.put` that fires
``consolidate`` after every Nth episode using the CLIPS rule scheduler in
``stargraph.fathom``) is a Phase 4 wiring task -- the runtime currently
exposes only the data carrier (``ConsolidationRule.cadence={"every": N}``)
and the ``consolidate`` operation. This test therefore exercises the
cadence-trigger logic via a minimal in-test dispatcher that mirrors the
contract: count put()s under a rule, fire consolidate() when the running
count is a non-zero multiple of ``cadence['every']``.

Done so verifying the cadence semantics does not require waiting on the
Phase-4 wiring of the dispatcher into ``put``.
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


_BATCH_RULE = ConsolidationRule(
    id="rule_cadence_batch_100",
    cadence={"every": 100},
    when_filter="",
    then_emits=["facts"],
)


def _episode(ep_id: str, *, idx: int) -> Episode:
    """Build an :class:`Episode` whose metadata encodes a unique fact-key."""
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
    """Mirror the planned cadence dispatcher: put + maybe-fire consolidate.

    Counter is a single-element list to give the helper a closure-free
    mutable cell. ``fires`` accumulates one entry per ``consolidate`` run.
    """
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


async def test_every_100_fires_after_100_episodes(tmp_path: Path) -> None:
    """``every=100`` rule: 99 episodes -> no fire; 100th -> consolidate fires once."""
    memory = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    await memory.bootstrap()

    counter = [0]
    fires: list[list[MemoryDelta]] = []

    for i in range(99):
        await _put_with_cadence(
            memory,
            _episode(f"ep-{i:03d}", idx=i),
            _BATCH_RULE,
            counter,
            fires,
        )

    assert fires == [], "consolidate must not fire before the 100th episode"
    assert counter[0] == 99

    await _put_with_cadence(
        memory,
        _episode("ep-099", idx=99),
        _BATCH_RULE,
        counter,
        fires,
    )

    assert len(fires) == 1, "consolidate must fire exactly once at the 100th episode"
    assert counter[0] == 100
    deltas = fires[0]
    assert len(deltas) == 100, "batch fire must produce one delta per episode"
    for delta in deltas:
        assert delta.rule_id == _BATCH_RULE.id
