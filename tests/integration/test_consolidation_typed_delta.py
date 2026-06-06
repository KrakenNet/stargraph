# SPDX-License-Identifier: Apache-2.0
"""Mem0-style typed-delta consolidation integration tests (FR-28, AC-5.3, NFR-4).

Exercises :meth:`stargraph.stores.sqlite_memory.SQLiteMemoryStore.consolidate`
across the four typed-delta variants (ADD / UPDATE / DELETE / NOOP) and
verifies each delta round-trips through
:meth:`stargraph.stores.sqlite_fact.SQLiteFactStore.apply_delta` with the
expected pin/unpin behaviour and full lineage (design §4.2).

POC scope: episodes carry an optional ``metadata["intent"]`` selector
(``"add"`` / ``"update"`` / ``"delete"`` / ``"noop"``) plus an optional
``metadata["replaces"]`` id list. The consolidation pass also performs
intra-batch exact-match dedup on ``(subject, predicate)`` keys --
newer episodes for the same key emit ``UpdateDelta`` against the older
fact id. Embedding-similarity dedup against existing FactStore rows is
deferred to a later phase (see task 3.22 note); the
``MemoryStore.consolidate`` Protocol takes only ``rule``, so cross-store
dedup against existing facts cannot run from inside ``consolidate``
without broadening the Protocol -- callers that need pre-existing-fact
classification encode the intent on the episode.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from stargraph.stores.fact import Fact, FactPattern
from stargraph.stores.memory import (
    AddDelta,
    ConsolidationRule,
    DeleteDelta,
    Episode,
    NoopDelta,
    UpdateDelta,
)
from stargraph.stores.sqlite_fact import SQLiteFactStore
from stargraph.stores.sqlite_memory import SQLiteMemoryStore

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


_RULE = ConsolidationRule(
    id="rule_consolidate_v1",
    cadence={"every": 1},
    when_filter="",
    then_emits=["facts"],
)


def _episode(
    ep_id: str,
    *,
    subject: str,
    predicate: str,
    obj: str,
    intent: str | None = None,
    replaces: list[str] | None = None,
    user: str = "alice",
    agent: str = "knowledge-agent",
    session: str = "s1",
) -> Episode:
    """Build an :class:`Episode` whose metadata encodes consolidation intent."""
    metadata: dict[str, object] = {
        "subject": subject,
        "predicate": predicate,
        "object": obj,
    }
    if intent is not None:
        metadata["intent"] = intent
    if replaces is not None:
        metadata["replaces"] = replaces
    return Episode(
        id=ep_id,
        content=f"{subject} {predicate} {obj}",
        timestamp=datetime.now(UTC),
        source_node="test",
        agent=agent,
        user=user,
        session=session,
        metadata=metadata,
    )


async def _bootstrap(tmp_path: Path) -> tuple[SQLiteMemoryStore, SQLiteFactStore]:
    memory = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    fact = SQLiteFactStore(tmp_path / "facts.sqlite")
    await memory.bootstrap()
    await fact.bootstrap()
    return memory, fact


async def test_consolidate_returns_list_of_memorydelta(tmp_path: Path) -> None:
    """Put N episodes, run consolidate, assert returns ``list[MemoryDelta]``."""
    memory, _ = await _bootstrap(tmp_path)
    episodes = [
        _episode("ep-1", subject="alice", predicate="knows", obj="bob"),
        _episode("ep-2", subject="alice", predicate="knows", obj="carol"),
        _episode("ep-3", subject="bob", predicate="likes", obj="graphs"),
    ]
    for ep in episodes:
        await memory.put(ep, user=ep.user, session=ep.session, agent=ep.agent)

    deltas = await memory.consolidate(_RULE)
    assert isinstance(deltas, list)
    assert len(deltas) == len(episodes)
    for delta in deltas:
        assert isinstance(delta, AddDelta | UpdateDelta | DeleteDelta | NoopDelta)
        assert delta.rule_id == _RULE.id
        assert delta.source_episode_ids
        assert delta.confidence == 1.0


async def test_apply_each_delta_pins_facts(tmp_path: Path) -> None:
    """Apply each delta via :meth:`FactStore.apply_delta`; assert facts present with lineage."""
    memory, facts = await _bootstrap(tmp_path)
    episodes = [
        _episode("ep-a", subject="alice", predicate="knows", obj="bob"),
        _episode("ep-b", subject="bob", predicate="likes", obj="graphs"),
    ]
    for ep in episodes:
        await memory.put(ep, user=ep.user, session=ep.session, agent=ep.agent)

    deltas = await memory.consolidate(_RULE)
    for delta in deltas:
        await facts.apply_delta(delta)

    stored = await facts.query(FactPattern(user="alice", agent="knowledge-agent"))
    stored.extend(await facts.query(FactPattern(user="alice", agent="knowledge-agent")))
    # Dedupe by id since the second query returns the same rows.
    by_id: dict[str, Fact] = {f.id: f for f in stored}
    assert len(by_id) >= 1
    for fact in by_id.values():
        assert fact.lineage, f"fact {fact.id!r} missing lineage"
        rule_ids = {entry.get("rule_id") for entry in fact.lineage}
        assert _RULE.id in rule_ids
        episode_ids = {
            ep_id for entry in fact.lineage for ep_id in entry.get("source_episode_ids", [])
        }
        assert episode_ids, f"fact {fact.id!r} missing source_episode_ids"


async def test_update_delta_replaces_old_fact(tmp_path: Path) -> None:
    """Pin a fact, then run consolidate with an update episode; old unpinned, new pinned."""
    memory, facts = await _bootstrap(tmp_path)

    old_fact = Fact(
        id="fact-old",
        user="alice",
        agent="knowledge-agent",
        payload={"subject": "alice", "predicate": "lives_in", "object": "berlin"},
        lineage=[{"rule_id": "seed", "source_episode_ids": ["seed-ep"]}],
        confidence=0.9,
        pinned_at=datetime.now(UTC),
    )
    await facts.pin(old_fact)
    assert {f.id for f in await facts.query(FactPattern(user="alice"))} == {"fact-old"}

    update_ep = _episode(
        "ep-update",
        subject="alice",
        predicate="lives_in",
        obj="munich",
        intent="update",
        replaces=["fact-old"],
    )
    await memory.put(
        update_ep,
        user=update_ep.user,
        session=update_ep.session,
        agent=update_ep.agent,
    )

    deltas = await memory.consolidate(_RULE)
    assert len(deltas) == 1
    delta = deltas[0]
    assert isinstance(delta, UpdateDelta)
    assert delta.replaces == ["fact-old"]

    await facts.apply_delta(delta)
    remaining = await facts.query(FactPattern(user="alice"))
    remaining_ids = {f.id for f in remaining}
    assert "fact-old" not in remaining_ids
    assert remaining, "expected at least one new fact pinned by UpdateDelta"
    new_fact = next(f for f in remaining if f.id != "fact-old")
    assert new_fact.payload["object"] == "munich"


async def test_delete_delta_unpins(tmp_path: Path) -> None:
    """Pin a fact; delete-intent episode produces DeleteDelta that unpins it."""
    memory, facts = await _bootstrap(tmp_path)

    fact = Fact(
        id="fact-delete-me",
        user="alice",
        agent="knowledge-agent",
        payload={"subject": "alice", "predicate": "owns", "object": "obsolete"},
        lineage=[{"rule_id": "seed", "source_episode_ids": ["seed-ep"]}],
        confidence=0.7,
        pinned_at=datetime.now(UTC),
    )
    await facts.pin(fact)
    assert {f.id for f in await facts.query(FactPattern(user="alice"))} == {"fact-delete-me"}

    delete_ep = _episode(
        "ep-del",
        subject="alice",
        predicate="owns",
        obj="obsolete",
        intent="delete",
        replaces=["fact-delete-me"],
    )
    await memory.put(
        delete_ep,
        user=delete_ep.user,
        session=delete_ep.session,
        agent=delete_ep.agent,
    )

    deltas = await memory.consolidate(_RULE)
    assert len(deltas) == 1
    delta = deltas[0]
    assert isinstance(delta, DeleteDelta)
    assert delta.replaces == ["fact-delete-me"]

    await facts.apply_delta(delta)
    remaining = {f.id for f in await facts.query(FactPattern(user="alice"))}
    assert remaining == set()


async def test_noop_delta_audit_only(tmp_path: Path) -> None:
    """Run consolidate where no change is needed; NoopDelta returned, no fact mutation."""
    memory, facts = await _bootstrap(tmp_path)

    noop_ep = _episode(
        "ep-noop",
        subject="alice",
        predicate="knows",
        obj="bob",
        intent="noop",
    )
    await memory.put(
        noop_ep,
        user=noop_ep.user,
        session=noop_ep.session,
        agent=noop_ep.agent,
    )

    deltas = await memory.consolidate(_RULE)
    assert len(deltas) == 1
    delta = deltas[0]
    assert isinstance(delta, NoopDelta)
    assert delta.source_episode_ids == ["ep-noop"]
    assert delta.rule_id == _RULE.id

    await facts.apply_delta(delta)  # audit-only; no mutation
    remaining = await facts.query(FactPattern(user="alice"))
    assert remaining == []


async def test_intra_batch_dedup_emits_update(tmp_path: Path) -> None:
    """Two episodes with same (subject, predicate); newer one emits UpdateDelta."""
    memory, _ = await _bootstrap(tmp_path)
    older = _episode(
        "ep-older",
        subject="alice",
        predicate="lives_in",
        obj="berlin",
    )
    newer = _episode(
        "ep-newer",
        subject="alice",
        predicate="lives_in",
        obj="munich",
    )
    await memory.put(older, user=older.user, session=older.session, agent=older.agent)
    await memory.put(newer, user=newer.user, session=newer.session, agent=newer.agent)

    deltas = await memory.consolidate(_RULE)
    assert len(deltas) == 2
    kinds = {d.kind for d in deltas}
    assert kinds == {"add", "update"}
    update = next(d for d in deltas if isinstance(d, UpdateDelta))
    assert update.replaces == ["ep-older"]
    assert update.fact_payload["object"] == "munich"
