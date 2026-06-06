# SPDX-License-Identifier: Apache-2.0
"""KG promotion ↔ counterfactual replay contract (FR-30 / AC-6.4).

Phase-3 task 3.35. Pins the contract that promoted :class:`Fact` rows are
faithful subjects of the engine's FR-27 byte-identical-replay and
counterfactual-refusal invariants -- so that promotion side-effects
participate in the same Temporal-style "cannot change the past" guarantees
that govern node outputs and CLIPS facts.

Two contract assertions:

1. :func:`test_replay_byte_identical_facts` -- replaying the same promotion
   inputs (same triples, same rule pack version, same ``rule_id`` /
   ``agent_id``) yields a fact set whose semantic content is byte-identical
   to the original: identical subject/predicate/object/source payloads,
   identical lineage ``triple_id``/``rule_id``/``agent_id`` slots, and
   identical confidence values. Per FR-27 the engine drives replay through
   cassettes that hold the original ids + timestamps; until that wiring
   reaches :func:`PromoteTriplesToFacts` (deferred to the engine fork-loop
   integration in a follow-up Phase-3 task), the strict byte-for-byte
   ``id`` + ``pinned_at`` half is xfail-tagged (see :pep:`xfail` block).
2. :func:`test_counterfactual_rule_pack_mutation_refused` -- a
   :class:`CounterfactualMutation` with ``rule_pack_version`` set produces
   a cf-derived graph hash that is distinct from the original
   (domain-separated by ``b"stargraph-cf-v1"``). This is the prefix-hash
   contract that the engine's resume path enforces via
   :class:`CheckpointError(reason="cf-prefix-hash-refused")` per AC-3.4 /
   FR-27. Pinning the hash divergence here ensures that any future cf
   replay attempting to swap the rule pack between record and replay
   would be detected at the checkpoint layer before promotion runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from stargraph.fathom import FathomAdapter
from stargraph.replay.counterfactual import CounterfactualMutation, derived_graph_hash
from stargraph.stores.fact import FactPattern
from stargraph.stores.graph import NodeRef
from stargraph.stores.kg_promotion import PromoteTriplesToFacts
from stargraph.stores.ryugraph import RyuGraphStore
from stargraph.stores.sqlite_fact import SQLiteFactStore

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


_TRIPLES: tuple[tuple[str, str, str], ...] = (
    ("alice", "knows", "bob"),
    ("alice", "knows", "carol"),
)

_FILTER_CYPHER: str = (
    "MATCH (s:Entity {id: 'alice'})-[r:Rel]->(o:Entity) "
    "RETURN s.id AS subject, r.predicate AS predicate, o.id AS object"
)


class _RecordingEngine:
    """Minimal Fathom ``Engine`` stand-in -- records assertions only."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def assert_fact(self, template: str, slots: dict[str, Any]) -> None:
        self.calls.append((template, slots))


async def _seed_kuzu(tmp_path: Path, name: str) -> RyuGraphStore:
    store = RyuGraphStore(tmp_path / name)
    await store.bootstrap()
    for s, p, o in _TRIPLES:
        await store.add_triple(
            NodeRef(id=s, kind="Person"),
            p,
            NodeRef(id=o, kind="Person"),
        )
    return store


async def _promote(
    tmp_path: Path,
    *,
    name: str,
    rule_id: str,
    agent_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run promotion against a fresh KG/FactStore; return (payloads, lineage_heads)."""
    graph = await _seed_kuzu(tmp_path, f"graph-{name}")
    fact_store = SQLiteFactStore(tmp_path / f"facts-{name}.sqlite")
    await fact_store.bootstrap()
    fathom = FathomAdapter(cast("Any", _RecordingEngine()))

    promoted = await PromoteTriplesToFacts(
        graph,
        fact_store,
        fathom,
        filter_cypher=_FILTER_CYPHER,
        rule_id=rule_id,
        agent_id=agent_id,
    )

    # FactStore round-trip -- promotion is durable.
    stored = await fact_store.query(FactPattern(agent=agent_id))
    assert len(stored) == len(promoted) == len(_TRIPLES)

    # Strip ``source`` -- it carries the absolute kuzu path, which is
    # tmp_path-bound per invocation. The (subject, predicate, object)
    # triple is the rule-pack-pinned content surface.
    payloads = sorted(
        ({k: v for k, v in f.payload.items() if k != "source"} for f in promoted),
        key=lambda p: (p["subject"], p["predicate"], p["object"]),
    )
    # Strip the volatile ``promotion_ts`` from the lineage head -- the
    # rule-pack-pinned half (triple_id / rule_id / agent_id) is the
    # contract surface we replay against.
    lineage_heads = [
        {k: v for k, v in promoted_f.lineage[0].items() if k != "promotion_ts"}
        for promoted_f in sorted(
            promoted,
            key=lambda f: (f.payload["subject"], f.payload["predicate"], f.payload["object"]),
        )
    ]
    return payloads, lineage_heads


async def test_replay_byte_identical_facts(tmp_path: Path) -> None:
    """Same triples + same rule pack → byte-identical promoted-fact content.

    Contract: replaying a promotion run with identical inputs (same KG
    triples, same ``rule_id`` standing in for the rule pack version,
    same ``agent_id``) yields semantically byte-identical facts -- same
    payload, same lineage rule_id/triple_id/agent_id. This is the FR-27
    byte-identical-replay contract specialised to the KG-promotion
    side-channel.

    The strict ``Fact.id`` (uuid4) + ``Fact.pinned_at`` (now()) half is
    deferred to the engine-driven cassette replay path; today
    :func:`PromoteTriplesToFacts` mints fresh ids/timestamps on each
    invocation, so the run-twice harness here cannot pin those without
    the cassette layer wiring promotion in. The xfail block below
    documents the deferred edge.
    """
    payloads_a, lineage_a = await _promote(
        tmp_path, name="a", rule_id="promote_alice_v1", agent_id="agent-replay"
    )
    payloads_b, lineage_b = await _promote(
        tmp_path, name="b", rule_id="promote_alice_v1", agent_id="agent-replay"
    )

    # Byte-identical promoted-fact content -- payloads + rule-bound lineage.
    assert payloads_a == payloads_b, "promoted payloads must be byte-identical across replay"
    assert lineage_a == lineage_b, "promoted lineage must be byte-identical across replay"

    # The contract surface today: every fact carries the same triple_id,
    # rule_id, and agent_id under both runs -- the rule-pack-pinned
    # provenance is fully deterministic.
    for head in lineage_a:
        assert head["rule_id"] == "promote_alice_v1"
        assert head["agent_id"] == "agent-replay"
        assert head["triple_id"], "triple_id must be present and non-empty"


@pytest.mark.xfail(
    reason=(
        "Strict Fact.id (uuid4) + Fact.pinned_at byte-identity awaits the "
        "engine cassette layer hooking into PromoteTriplesToFacts (deferred "
        "Phase-3 wiring). Today promotion mints fresh ids/timestamps per call."
    ),
    strict=True,
)
async def test_replay_byte_identical_fact_ids_and_timestamps(tmp_path: Path) -> None:
    """xfail: strict ``Fact.id`` + ``Fact.pinned_at`` byte-identity (deferred wiring).

    Documents the FR-27 contract that under cassette-driven replay the
    full :class:`Fact` row -- including ``id`` and ``pinned_at`` --
    must be byte-identical to the original. Lands when the engine's
    cf/replay machinery routes through the promotion side-channel.
    """
    graph_a = await _seed_kuzu(tmp_path, "graph-ids-a")
    graph_b = await _seed_kuzu(tmp_path, "graph-ids-b")
    fact_store_a = SQLiteFactStore(tmp_path / "facts-ids-a.sqlite")
    fact_store_b = SQLiteFactStore(tmp_path / "facts-ids-b.sqlite")
    await fact_store_a.bootstrap()
    await fact_store_b.bootstrap()
    fathom = FathomAdapter(cast("Any", _RecordingEngine()))

    promoted_a = await PromoteTriplesToFacts(
        graph_a,
        fact_store_a,
        fathom,
        filter_cypher=_FILTER_CYPHER,
        rule_id="promote_alice_v1",
        agent_id="agent-replay",
    )
    promoted_b = await PromoteTriplesToFacts(
        graph_b,
        fact_store_b,
        fathom,
        filter_cypher=_FILTER_CYPHER,
        rule_id="promote_alice_v1",
        agent_id="agent-replay",
    )

    ids_a = sorted(f.id for f in promoted_a)
    ids_b = sorted(f.id for f in promoted_b)
    pinned_a = sorted(f.pinned_at for f in promoted_a)
    pinned_b = sorted(f.pinned_at for f in promoted_b)

    # Currently fails: uuid4 + datetime.now() are fresh per call.
    assert ids_a == ids_b
    assert pinned_a == pinned_b


def test_counterfactual_rule_pack_mutation_refused() -> None:
    """``CounterfactualMutation.rule_pack_version`` triggers a distinct cf-derived hash.

    Contract: a counterfactual that pins the run to a different rule
    pack version than the original produces a cf-derived ``graph_hash``
    that is provably distinct from the original. Per design §3.8.3 /
    AC-3.4 the engine's resume path refuses any checkpoint whose
    ``graph_hash`` carries the cf-prefix signature with a
    :class:`CheckpointError(reason="cf-prefix-hash-refused")` -- this
    test pins the divergence at the hash layer, which is the property
    the engine's resume-time refusal relies on.
    """
    original_hash = "a" * 64

    # Empty/no-op mutation still derives a distinct hash via the
    # ``b"stargraph-cf-v1"`` domain-separation tag.
    no_op = CounterfactualMutation()
    derived_no_op = derived_graph_hash(original_hash, no_op)
    assert len(derived_no_op) == 64
    assert derived_no_op != original_hash

    # Same original, two distinct rule_pack_version mutations -- the
    # derived hashes must diverge from each other AND from the original.
    mutation_v2 = CounterfactualMutation(rule_pack_version="v2")
    mutation_v3 = CounterfactualMutation(rule_pack_version="v3")
    derived_v2 = derived_graph_hash(original_hash, mutation_v2)
    derived_v3 = derived_graph_hash(original_hash, mutation_v3)

    assert derived_v2 != original_hash
    assert derived_v3 != original_hash
    assert derived_v2 != derived_v3, (
        "rule_pack_version mutations must produce distinct cf-derived hashes "
        "so the engine's resume path can refuse mismatched replays per AC-3.4"
    )

    # Determinism: same inputs → same derived hash (so the refusal is
    # reproducible across record/replay).
    assert derived_graph_hash(original_hash, mutation_v2) == derived_v2
