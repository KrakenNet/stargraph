# SPDX-License-Identifier: Apache-2.0
"""Salience-gated consolidation integration test (FR-31, design §3.6/§3.14).

FR-31 mandates a pluggable :class:`stargraph.skills.salience.SalienceScorer`
gate at the episodic -> semantic consolidation seam: episodes scoring
below a caller-chosen threshold are filtered *before* the consolidation
rule body runs (avoids promoting noise per AC-5.5).

The :class:`MemoryStore` Protocol takes only ``rule`` (no SalienceScorer
handle) -- broadening the Protocol is out of scope for the POC, so the
gate is caller-driven (mirrors the cadence dispatcher pattern in
``test_consolidation_cadence_batch.py``). This test exercises the
contract via a minimal in-test gate: score every recent episode, drop
those below ``threshold``, and only emit deltas for the survivors.

Verifies four invariants:

1. Below-threshold episodes never reach the consolidation rule body
   (no MemoryDelta carries their id in ``source_episode_ids``).
2. Above-threshold episodes are consolidated as usual.
3. Threshold = 0 admits every episode (no gating).
4. Threshold = 1.0 admits none (full gating).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003

import aiosqlite
import pytest

from stargraph.skills.salience import RuleBasedScorer, SalienceContext, SalienceScorer
from stargraph.stores.memory import (
    ConsolidationRule,
    Episode,
    MemoryDelta,
)
from stargraph.stores.sqlite_memory import SQLiteMemoryStore

pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


_RULE = ConsolidationRule(
    id="rule_salience_v1",
    cadence={"every": 1},
    when_filter="",
    then_emits=["facts"],
)


class StubScorer:
    """Test-only scorer: returns the value carried in ``Episode.metadata['_salience']``.

    Lets each test pin per-episode salience exactly without depending on
    the Park 2023 recency formula's wallclock-sensitive output. Conforms
    to :class:`SalienceScorer` structurally (Protocol is runtime-checkable).
    """

    async def score(self, memory: Episode, context: SalienceContext) -> float:
        _ = context
        return float(memory.metadata.get("_salience", 0.0))


def _stub_episode(ep_id: str, *, salience: float, predicate: str = "knows") -> Episode:
    """Build an :class:`Episode` carrying a stub salience score in metadata."""
    return Episode(
        id=ep_id,
        content=f"alice {predicate} {ep_id}",
        timestamp=datetime.now(UTC),
        source_node="test",
        agent="knowledge-agent",
        user="alice",
        session="s1",
        metadata={
            "subject": "alice",
            "predicate": predicate,
            "object": ep_id,
            "_salience": salience,
        },
    )


def _ctx() -> SalienceContext:
    return SalienceContext(
        last_access_ts=datetime.now(UTC),
        access_count=1,
        rule_match_count=1,
    )


async def _consolidate_with_salience_gate(
    memory: SQLiteMemoryStore,
    db_path: Path,
    rule: ConsolidationRule,
    scorer: SalienceScorer,
    threshold: float,
    *,
    user: str = "alice",
) -> tuple[list[MemoryDelta], set[str]]:
    """Mirror the planned salience-gated dispatcher (design §3.14 pre-filter).

    Reads recent episodes via the widening read, scores each against
    ``scorer``, drops those below ``threshold``, then runs ``consolidate``.
    Returns ``(deltas, admitted_ids)`` so the test can assert on both the
    survivor set the rule body saw and the typed deltas it emitted.

    Implementation note: the POC ``consolidate`` operates over the full
    ``episodes`` table; to enforce the gate we delete the dropped rows
    upfront via the same on-disk file the store points at. Production
    wiring will pass the survivor set through the rule's ``when_filter``
    SQL fragment, but the observable contract -- "below-threshold
    episodes never reach the rule body" -- is identical.
    """
    candidates = await memory.recent(user=user, limit=1000)
    admitted: set[str] = set()
    dropped: set[str] = set()
    ctx = _ctx()
    for ep in candidates:
        score = await scorer.score(ep, ctx)
        if score >= threshold:
            admitted.add(ep.id)
        else:
            dropped.add(ep.id)

    if dropped:
        # Realise the gate against the underlying store so consolidate's
        # SELECT cannot observe the dropped rows -- mirrors the production
        # when_filter pre-filter contract.
        async with aiosqlite.connect(db_path) as conn:
            placeholders = ",".join("?" for _ in dropped)
            await conn.execute(
                f"DELETE FROM episodes WHERE id IN ({placeholders})",
                tuple(dropped),
            )
            await conn.commit()

    deltas = await memory.consolidate(rule)
    return deltas, admitted


async def test_below_threshold_episodes_filtered_before_rule_body(tmp_path: Path) -> None:
    """Episodes below salience threshold never appear in any MemoryDelta."""
    db_path = tmp_path / "memory.sqlite"
    memory = SQLiteMemoryStore(db_path)
    await memory.bootstrap()

    high = [
        _stub_episode("ep-high-1", salience=0.9, predicate="knows_a"),
        _stub_episode("ep-high-2", salience=0.8, predicate="knows_b"),
    ]
    low = [
        _stub_episode("ep-low-1", salience=0.1, predicate="knows_c"),
        _stub_episode("ep-low-2", salience=0.05, predicate="knows_d"),
    ]
    for ep in (*high, *low):
        await memory.put(ep, user=ep.user, session=ep.session, agent=ep.agent)

    deltas, admitted = await _consolidate_with_salience_gate(
        memory, db_path, _RULE, StubScorer(), threshold=0.5
    )

    # Only the two high-salience episodes survived the gate.
    assert admitted == {"ep-high-1", "ep-high-2"}

    # No delta references a below-threshold episode id.
    seen_in_deltas: set[str] = set()
    for d in deltas:
        seen_in_deltas.update(d.source_episode_ids)
    assert seen_in_deltas == {"ep-high-1", "ep-high-2"}
    for low_ep in low:
        assert low_ep.id not in seen_in_deltas, (
            f"below-threshold episode {low_ep.id!r} leaked into rule body"
        )


async def test_threshold_zero_admits_every_episode(tmp_path: Path) -> None:
    """threshold=0 -> gate is a no-op; every episode reaches the rule body."""
    db_path = tmp_path / "memory.sqlite"
    memory = SQLiteMemoryStore(db_path)
    await memory.bootstrap()

    episodes = [_stub_episode(f"ep-{i}", salience=0.0 + i * 0.1) for i in range(3)]
    for ep in episodes:
        await memory.put(ep, user=ep.user, session=ep.session, agent=ep.agent)

    deltas, admitted = await _consolidate_with_salience_gate(
        memory, db_path, _RULE, StubScorer(), threshold=0.0
    )
    assert admitted == {ep.id for ep in episodes}
    seen = {ep_id for d in deltas for ep_id in d.source_episode_ids}
    assert seen == {ep.id for ep in episodes}


async def test_threshold_one_admits_no_episode(tmp_path: Path) -> None:
    """threshold=1.0 with sub-1 scorer -> empty consolidation pass."""
    db_path = tmp_path / "memory.sqlite"
    memory = SQLiteMemoryStore(db_path)
    await memory.bootstrap()

    episodes = [_stub_episode(f"ep-{i}", salience=0.5) for i in range(3)]
    for ep in episodes:
        await memory.put(ep, user=ep.user, session=ep.session, agent=ep.agent)

    deltas, admitted = await _consolidate_with_salience_gate(
        memory, db_path, _RULE, StubScorer(), threshold=1.0
    )
    assert admitted == set()
    assert deltas == []


async def test_rule_based_scorer_gates_against_protocol(tmp_path: Path) -> None:
    """The shipped :class:`RuleBasedScorer` honours the same gating contract.

    Pins that the v1 default scorer (Park 2023 formula) is interchangeable
    behind the SalienceScorer Protocol -- no ``StubScorer``-only path.
    A threshold above 1.0 must drop every episode regardless of recency.
    """
    db_path = tmp_path / "memory.sqlite"
    memory = SQLiteMemoryStore(db_path)
    await memory.bootstrap()

    episodes = [_stub_episode(f"ep-rb-{i}", salience=0.5) for i in range(2)]
    for ep in episodes:
        await memory.put(ep, user=ep.user, session=ep.session, agent=ep.agent)

    scorer = RuleBasedScorer()
    assert isinstance(scorer, SalienceScorer)
    deltas, admitted = await _consolidate_with_salience_gate(
        memory, db_path, _RULE, scorer, threshold=1.01
    )
    assert admitted == set()
    assert deltas == []
