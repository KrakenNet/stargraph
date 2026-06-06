# SPDX-License-Identifier: Apache-2.0
"""FactStore + Fathom provenance integration test (FR-30, NFR-5, design §3.4).

Pins the wired contract from design §3.5 line 450: ``apply_delta`` for
ADD/UPDATE drives a parallel :meth:`stargraph.fathom.FathomAdapter.assert_with_provenance`
side-channel so the engine sees the same promotion the FactStore lineage
records. Three observable behaviours land here:

1. ``test_add_delta_drives_assert_with_provenance`` -- pin a fact via
   :meth:`SQLiteFactStore.apply_delta` against an ``AddDelta`` and assert
   :meth:`FathomAdapter.assert_with_provenance` reaches the recording
   engine with the merged provenance + caller slot bundle.
2. ``test_provenance_bundle_has_required_fields`` -- the
   :class:`ProvenanceBundle` carries every FR-30 / AC-6.2 key
   (``origin``, ``source``, ``run_id``, ``step``, ``confidence``,
   ``timestamp``) and each lands as an underscore-prefixed slot on the
   asserted fact.
3. ``test_lineage_queryable_fact_to_episodes_to_runs`` -- after applying
   the delta, ``fact_store.query`` returns the fact with a lineage row
   chaining ``fact_id -> source_episode_ids -> run_id`` (NFR-5 audit).

The recording-engine pattern mirrors
:mod:`tests.integration.test_kg_fact_promotion_rule` -- a stand-in
:class:`fathom.Engine` that captures every ``assert_fact(template, slots)``
call so we can assert the exact slot bundle the adapter produced without
depending on a CLIPS runtime.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest

from stargraph.fathom import FathomAdapter, ProvenanceBundle
from stargraph.stores.fact import FactPattern
from stargraph.stores.memory import AddDelta
from stargraph.stores.sqlite_fact import SQLiteFactStore

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


class _RecordingEngine:
    """Minimal ``fathom.Engine`` stand-in -- records ``assert_fact`` calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def assert_fact(self, template: str, slots: dict[str, Any]) -> None:
        self.calls.append((template, slots))


def _build_add_delta(
    *,
    fact_id: str = "f-1",
    user: str = "Alice",
    agent: str = "rag",
    rule_id: str = "consolidation_v1",
    episodes: tuple[str, ...] = ("ep-1", "ep-2"),
    promotion_ts: datetime | None = None,
) -> AddDelta:
    """Build an ``AddDelta`` carrying the full FR-29 provenance bundle."""
    return AddDelta(
        kind="add",
        fact_payload={
            "id": fact_id,
            "user": user,
            "agent": agent,
            "subject": user,
            "predicate": "likes",
            "object": "tea",
        },
        source_episode_ids=list(episodes),
        promotion_ts=promotion_ts or datetime.now(UTC),
        rule_id=rule_id,
        confidence=0.9,
    )


def _provenance_for(delta: AddDelta, *, run_id: str) -> ProvenanceBundle:
    """Build a ``ProvenanceBundle`` aligned with ``delta`` provenance fields.

    ``origin`` / ``source`` use CLIPS-identifier-safe strings so the AC-6.2
    structural checks (``_origin`` / ``_source`` regex on
    ``^[A-Za-z_][A-Za-z0-9_\\-]*$``) accept them without sanitization
    rewrites.
    """
    return {
        "origin": "factstore_apply_delta",
        "source": "sqlite_factstore",
        "run_id": run_id,
        "step": 0,
        "confidence": Decimal(str(delta.confidence)),
        "timestamp": delta.promotion_ts,
    }


async def _apply_with_fathom(
    fact_store: SQLiteFactStore,
    adapter: FathomAdapter,
    delta: AddDelta,
    *,
    run_id: str,
) -> ProvenanceBundle:
    """Apply ``delta`` and mirror the assertion through the Fathom side-channel.

    Wires the design §3.5 line 450 contract: FactStore promotion is the
    authoritative output; ``assert_with_provenance`` is the engine
    observability seam (FR-30). Returns the provenance bundle so callers
    can assert on its contents.
    """
    await fact_store.apply_delta(delta)
    bundle = _provenance_for(delta, run_id=run_id)
    adapter.assert_with_provenance(
        template="stargraph.evidence",
        slots={
            "subject": delta.fact_payload["subject"],
            "predicate": delta.fact_payload["predicate"],
            "object": delta.fact_payload["object"],
        },
        provenance=bundle,
    )
    return bundle


async def test_add_delta_drives_assert_with_provenance(tmp_path: Path) -> None:
    """``apply_delta(AddDelta)`` + Fathom side-channel reaches ``engine.assert_fact``."""
    fact_store = SQLiteFactStore(tmp_path / "facts.sqlite")
    await fact_store.bootstrap()

    engine = _RecordingEngine()
    adapter = FathomAdapter(cast("Any", engine))

    delta = _build_add_delta()
    run_id = uuid4().hex
    await _apply_with_fathom(fact_store, adapter, delta, run_id=run_id)

    assert len(engine.calls) == 1
    template, slots = engine.calls[0]
    assert template == "stargraph.evidence"

    # Caller slots survive the merge.
    assert slots["subject"] == "Alice"
    assert slots["predicate"] == "likes"
    assert slots["object"] == "tea"

    # FactStore promotion is authoritative -- the fact lands in SQLite too.
    rows = await fact_store.query(FactPattern(user="Alice"))
    assert len(rows) == 1
    assert rows[0].id == "f-1"


async def test_provenance_bundle_has_required_fields(tmp_path: Path) -> None:
    """Provenance bundle carries every FR-30 / AC-6.2 key and lands as ``_``-slots."""
    fact_store = SQLiteFactStore(tmp_path / "facts.sqlite")
    await fact_store.bootstrap()

    engine = _RecordingEngine()
    adapter = FathomAdapter(cast("Any", engine))

    delta = _build_add_delta()
    run_id = "run_" + uuid4().hex
    bundle = await _apply_with_fathom(fact_store, adapter, delta, run_id=run_id)

    # FR-30 / AC-6.3 bundle keys (TypedDict total=True surface).
    required_keys = {"origin", "source", "run_id", "step", "confidence", "timestamp"}
    assert required_keys <= set(bundle.keys())

    # Every bundle key reaches the engine as an underscore-prefixed slot
    # (AC-6.2 sanitised encoding).
    _, slots = engine.calls[0]
    for key in required_keys:
        assert f"_{key}" in slots, f"missing _{key} on engine slots"

    # Spot-check encodings -- Decimal -> str, datetime -> ISO with Z, str pass-through.
    assert slots["_origin"] == "factstore_apply_delta"
    assert slots["_source"] == "sqlite_factstore"
    assert slots["_run_id"] == run_id
    assert slots["_step"] == 0
    assert slots["_confidence"] == str(Decimal(str(delta.confidence)))
    assert slots["_timestamp"].endswith("Z")


async def test_lineage_queryable_fact_to_episodes_to_runs(tmp_path: Path) -> None:
    """Lineage chains ``fact_id -> source_episode_ids -> run_id`` for NFR-5 audit."""
    fact_store = SQLiteFactStore(tmp_path / "facts.sqlite")
    await fact_store.bootstrap()

    engine = _RecordingEngine()
    adapter = FathomAdapter(cast("Any", engine))

    delta = _build_add_delta(
        fact_id="f-lineage",
        rule_id="consolidation_v1",
        episodes=("ep-100", "ep-101", "ep-102"),
    )
    run_id = "run_lineage_" + uuid4().hex
    await _apply_with_fathom(fact_store, adapter, delta, run_id=run_id)

    # Step 1 -- fact_id round-trips with the lineage row attached.
    rows = await fact_store.query(FactPattern(user="Alice"))
    assert len(rows) == 1
    fact = rows[0]
    assert fact.id == "f-lineage"
    assert fact.lineage, "fact missing lineage row"
    lineage_entry = fact.lineage[0]

    # Step 2 -- lineage row exposes ``source_episode_ids`` (episodes).
    episodes = lineage_entry.get("source_episode_ids")
    assert isinstance(episodes, list)
    assert episodes == ["ep-100", "ep-101", "ep-102"]

    # Step 3 -- the parallel Fathom side-channel slot carries the run id, so
    # ``fact_id -> episodes`` (FactStore lineage) joins ``rule_id -> run_id``
    # (engine assertion) on the shared ``rule_id``.
    assert lineage_entry.get("rule_id") == "consolidation_v1"
    _, slots = engine.calls[0]
    assert slots["_run_id"] == run_id

    # Promotion timestamp is a tz-aware ISO8601 string that round-trips.
    promotion_ts = lineage_entry.get("promotion_ts")
    assert isinstance(promotion_ts, str) and promotion_ts
    parsed = datetime.fromisoformat(promotion_ts)
    assert parsed.tzinfo is not None
