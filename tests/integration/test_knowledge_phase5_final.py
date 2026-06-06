# SPDX-License-Identifier: Apache-2.0
"""Phase 5 final E2E -- full stack + counterfactual replay (NFR-4, FR-27, FR-30).

Sequel to :mod:`tests.integration.test_knowledge_phase3_ve` (the
VE2-Phase3 broad-stack test). Where Phase-3 pinned the
record/replay/audit-log loop end-to-end, this Phase-5 final test adds
the **counterfactual** half: mutate one promoted triple's confidence,
re-fire :func:`PromoteTriplesToFacts`, and assert the resulting
:class:`RunDiff` carries a fact-level state delta on confidence.

Pipeline (mirrors Phase-3 §1-§5 then layers cf §6-§7):

1. Bootstrap five stores under ``tmp_path``: LanceDB (vectors), Kuzu
   (graph), and three SQLite stores (docs, memory, facts).
2. Drive :class:`~stargraph.skills.refs.wiki.WikiSkill` end-to-end on a
   topic string -- composes :class:`AutoresearchSkill` (claims) and
   formats the markdown wiki entry with provenance citations.
3. Promote KG triples → pinned :class:`~stargraph.stores.fact.Fact` rows
   via :func:`~stargraph.stores.kg_promotion.PromoteTriplesToFacts`. Use a
   small ``_ConfidenceShim`` wrapping the Kuzu store so the Cypher
   result rows carry a per-triple ``confidence`` (Kuzu does not store
   edge-level confidence; the shim is the deterministic injection
   point the cf branch mutates in §6).
4. Consolidate episodes → typed :data:`MemoryDelta` deltas → pin via
   :meth:`SQLiteFactStore.apply_delta`.
5. Record + replay :class:`ReactSkill` against
   :class:`~stargraph.replay.react_cassette.ReactStepReplayCassette`:
   record-pass byte sequence must equal replay-pass byte sequence
   (FR-27 byte-identical replay).
6. Counterfactual: for the same triples, re-fire promotion against a
   second :class:`SQLiteFactStore` with one triple's confidence
   bumped. Build synthetic original + cf :class:`RunHistory` snapshots
   whose state carries the per-triple confidence; call
   :func:`stargraph.replay.compare.compare`. Assert the resulting
   :class:`RunDiff` has exactly one diverged step, the divergence axis
   is ``state``, and the JSONPatch op rewrites the mutated triple's
   confidence path.
7. Persist a final engine :class:`Checkpoint` capturing the run state;
   re-open the checkpointer and call :meth:`read_latest`; assert the
   reloaded state restores wiki topic, promoted-fact count,
   delta count, and vector_versions metadata.

LLM stubs respect the must_stub policy: live LLM stubs fire only on
the record pass; replay path serves recorded outputs from the
cassette (a live invocation on the replay path is a contract break).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import lancedb  # pyright: ignore[reportMissingTypeStubs]
import orjson
import pytest

from stargraph.checkpoint.protocol import Checkpoint
from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.fathom import FathomAdapter
from stargraph.replay.compare import RunDiff, compare
from stargraph.replay.history import RunHistory
from stargraph.replay.react_cassette import (
    ReactStepRecord,
    ReactStepReplayCassette,
    input_hash,
)
from stargraph.skills.react import ReactSkill, ReactState
from stargraph.skills.refs.wiki import WikiSkill, WikiState
from stargraph.stores.embeddings import FakeEmbedder
from stargraph.stores.fact import Fact, FactPattern
from stargraph.stores.graph import NodeRef, ResultSet
from stargraph.stores.kg_promotion import PromoteTriplesToFacts
from stargraph.stores.lancedb import LanceDBVectorStore
from stargraph.stores.memory import (
    AddDelta,
    ConsolidationRule,
    DeleteDelta,
    Episode,
    NoopDelta,
    UpdateDelta,
)
from stargraph.stores.ryugraph import RyuGraphStore
from stargraph.stores.sqlite_doc import SQLiteDocStore
from stargraph.stores.sqlite_fact import SQLiteFactStore
from stargraph.stores.sqlite_memory import SQLiteMemoryStore
from stargraph.stores.vector import Row

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


pytestmark = [
    pytest.mark.knowledge,
    pytest.mark.integration,
    pytest.mark.usefixtures("standin_lm"),
]


_DOCS: tuple[tuple[str, str], ...] = (
    ("doc-alice", "Alice knows Bob and frequently talks about graphs."),
    ("doc-bob", "Bob is a longtime friend of Alice and Carol."),
    ("doc-carol", "Carol works on knowledge graphs with Alice."),
)


# (subject, predicate, object, original_confidence_str)
# Confidence is carried as a Decimal-parseable string -- ``_row_confidence``
# in :mod:`stargraph.stores.kg_promotion` accepts ``Decimal | int | str`` and
# falls back to ``1.0`` on floats / missing values per its POC docstring.
_TRIPLES: tuple[tuple[str, str, str, str], ...] = (
    ("alice", "knows", "bob", "0.7"),
    ("alice", "knows", "carol", "0.9"),
)

_FILTER_CYPHER: str = (
    "MATCH (s:Entity {id: 'alice'})-[r:Rel]->(o:Entity) "
    "RETURN s.id AS subject, r.predicate AS predicate, o.id AS object"
)

_RUN_ID: str = "phase5-final-run"
_CF_RUN_ID: str = "phase5-final-cf-run"


_CONSOLIDATION_RULE = ConsolidationRule(
    id="rule_consolidate_phase5_final_v1",
    cadence={"every": 1},
    when_filter="",
    then_emits=["facts"],
)


class _RecordingEngine:
    """Minimal Fathom ``Engine`` stand-in -- records assertions only."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def assert_fact(self, template: str, slots: dict[str, Any]) -> None:
        self.calls.append((template, slots))


class _ConfidenceShim:
    """Wrap a :class:`RyuGraphStore` so query() rows carry per-triple confidence.

    The Kuzu schema does not store edge-level confidence -- promotion
    rules that need it inject confidence at the row level. This shim is
    the deterministic injection point the counterfactual branch mutates
    (§6 of the test): the original run uses the canonical confidence
    map; the cf run mutates one triple's value and re-fires promotion.
    """

    def __init__(
        self,
        inner: RyuGraphStore,
        confidences: Mapping[tuple[str, str, str], str],
    ) -> None:
        self._inner = inner
        self._confidences = dict(confidences)
        # ``_path`` is read by :func:`_graph_source` in kg_promotion.
        self._path = inner._path  # pyright: ignore[reportPrivateUsage]

    async def bootstrap(self) -> None:
        await self._inner.bootstrap()

    async def query(
        self,
        cypher: str,
        params: Mapping[str, Any] | None = None,
    ) -> ResultSet:
        rs = await self._inner.query(cypher, params)
        new_rows: list[dict[str, Any]] = []
        for row in rs.rows:
            enriched = dict(row)
            key = (
                str(row.get("subject")),
                str(row.get("predicate")),
                str(row.get("object")),
            )
            if key in self._confidences:
                enriched["confidence"] = self._confidences[key]
            new_rows.append(enriched)
        return ResultSet(rows=new_rows, columns=rs.columns)


def _episode(
    ep_id: str,
    *,
    subject: str,
    predicate: str,
    obj: str,
    intent: str | None = None,
) -> Episode:
    metadata: dict[str, object] = {
        "subject": subject,
        "predicate": predicate,
        "object": obj,
    }
    if intent is not None:
        metadata["intent"] = intent
    return Episode(
        id=ep_id,
        content=f"{subject} {predicate} {obj}",
        timestamp=datetime.now(UTC),
        source_node="phase5-final",
        agent="knowledge-agent",
        user="alice",
        session="s-phase5-final",
        metadata=metadata,
    )


def _confidence_state(promoted: list[Fact]) -> dict[str, float]:
    """Project promoted facts into a deterministic ``{triple_id: confidence}`` map."""
    out: dict[str, float] = {}
    for fact in promoted:
        head = fact.lineage[0] if fact.lineage else {}
        triple_id = str(head.get("triple_id", fact.id))
        out[triple_id] = fact.confidence
    return out


async def test_knowledge_phase5_final_full_stack_counterfactual(tmp_path: Path) -> None:
    """Final E2E: full knowledge stack + counterfactual fact-level RunDiff (NFR-4)."""
    # ------------------------------------------------------------------
    # 1. Bootstrap the five stores.
    # ------------------------------------------------------------------
    embedder = FakeEmbedder()
    vector_store = LanceDBVectorStore(tmp_path / "vectors", embedder)
    graph_store = RyuGraphStore(tmp_path / "graph")
    doc_store = SQLiteDocStore(tmp_path / "docs.sqlite")
    memory_store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    fact_store = SQLiteFactStore(tmp_path / "facts.sqlite")

    await vector_store.bootstrap()
    await graph_store.bootstrap()
    await doc_store.bootstrap()
    await memory_store.bootstrap()
    await fact_store.bootstrap()

    # Seed minimal vector + doc payloads so LanceDB has a real version().
    rows = [Row(id=doc_id, text=text) for doc_id, text in _DOCS]
    await vector_store.upsert(rows)
    for doc_id, text in _DOCS:
        await doc_store.put(doc_id, text, metadata={"doc_id": doc_id})

    for s, p, o, _conf in _TRIPLES:
        await graph_store.add_triple(
            NodeRef(id=s, kind="Person"),
            p,
            NodeRef(id=o, kind="Person"),
        )

    # ------------------------------------------------------------------
    # 2. Drive WikiSkill end-to-end (autoresearch + formatter).
    # ------------------------------------------------------------------
    wiki = WikiSkill(
        name="wiki",
        version="0.1.0",
        description="Phase 5 final wiki drive",
    )
    wiki_state = WikiState(topic="alice")
    wiki_out = await wiki.run(wiki_state)

    assert wiki_out.wiki_entry is not None
    assert wiki_out.wiki_entry.topic == "alice"
    assert wiki_out.markdown.startswith("# alice"), wiki_out.markdown
    assert "## Sources" in wiki_out.markdown
    assert "[1]" in wiki_out.markdown

    # ------------------------------------------------------------------
    # 3. Promote KG triples → pinned facts (FR-30) via the confidence shim.
    # ------------------------------------------------------------------
    fathom_adapter = FathomAdapter(cast("Any", _RecordingEngine()))
    original_confidences: dict[tuple[str, str, str], str] = {
        (s, p, o): conf for s, p, o, conf in _TRIPLES
    }
    shim = _ConfidenceShim(graph_store, original_confidences)

    promoted = await PromoteTriplesToFacts(
        cast("Any", shim),
        fact_store,
        fathom_adapter,
        filter_cypher=_FILTER_CYPHER,
        rule_id="phase5_final_promote_v1",
        agent_id="phase5-final-agent",
    )
    assert len(promoted) == len(_TRIPLES)
    promoted_ids = {f.id for f in promoted}

    orig_confidence_state = _confidence_state(promoted)
    assert set(orig_confidence_state.values()) == {0.7, 0.9}, orig_confidence_state
    # Sanity: every triple_id corresponds to one of the seeded triples.
    assert set(orig_confidence_state) == {f"{s}|{p}|{o}" for s, p, o, _ in _TRIPLES}

    # ------------------------------------------------------------------
    # 4. Consolidate episodes → typed deltas → apply_delta against facts.
    # ------------------------------------------------------------------
    episodes = [
        _episode("ep-add-1", subject="alice", predicate="lives_in", obj="berlin"),
        _episode("ep-noop-1", subject="alice", predicate="enjoys", obj="graphs", intent="noop"),
    ]
    for ep in episodes:
        await memory_store.put(ep, user=ep.user, session=ep.session, agent=ep.agent)

    deltas = await memory_store.consolidate(_CONSOLIDATION_RULE)
    assert len(deltas) == len(episodes)
    delta_kinds = {d.kind for d in deltas}
    assert "add" in delta_kinds
    for delta in deltas:
        assert isinstance(delta, AddDelta | UpdateDelta | DeleteDelta | NoopDelta)
        assert delta.rule_id == _CONSOLIDATION_RULE.id
        await fact_store.apply_delta(delta)

    after_consolidate = await fact_store.query(FactPattern(user="alice"))
    after_ids = {f.id for f in after_consolidate}
    assert after_ids - promoted_ids, "consolidation ADD path produced no new fact"

    # ------------------------------------------------------------------
    # 5. Record + replay ReactSkill via the per-step cassette (must_stub).
    # ------------------------------------------------------------------
    cassette = ReactStepReplayCassette()

    iteration = {"n": 0}

    def live_llm(_state: ReactState, _ctx: Any) -> dict[str, Any]:
        iteration["n"] += 1
        if iteration["n"] == 1:
            return {
                "reasoning": "look up alice",
                "tool_call": {"name": "search", "arguments": {"q": "alice"}},
                "done": False,
                "final_answer": None,
            }
        return {
            "reasoning": "done",
            "tool_call": None,
            "done": True,
            "final_answer": "alice-found",
        }

    record_events: list[dict[str, Any]] = []
    record_tick = {"v": 0.0}

    def _next_record_ts() -> float:
        record_tick["v"] += 1.0
        return record_tick["v"]

    def record_stub(state: ReactState, ctx: Any) -> dict[str, Any]:
        step_id = state.step_index
        output = live_llm(state, ctx)
        tc = output.get("tool_call")
        tool_name: str | None = cast("str", tc["name"]) if isinstance(tc, dict) else None
        cassette.record(
            ReactStepRecord(
                step_id=step_id,
                node_name="react_loop",
                input_hash=input_hash(
                    {"step_index": step_id, "trajectory_len": len(state.trajectory)}
                ),
                output=output,
                model_id="phase5-final-stub",
                prompt_hash="ph",
                tool_name=tool_name,
                tool_args_hash=None,
                wall_clock_ts=_next_record_ts(),
            )
        )
        record_events.append({"kind": "step", "step_id": step_id, "output": output})
        return output

    record_skill = ReactSkill(
        name="react",
        version="0.1.0",
        description="phase5-final record",
        llm_stub=record_stub,
        tool_impls={"search": lambda q: f"hit:{q}"},  # pyright: ignore[reportUnknownLambdaType]
        max_steps=4,
    )
    record_out = await record_skill.run(ReactState())
    assert record_out.done is True
    assert record_out.final_answer == "alice-found"

    replay_events: list[dict[str, Any]] = []

    def replay_stub(state: ReactState, _ctx: Any) -> dict[str, Any]:
        # must_stub: live LLM must NEVER fire on replay; this stub
        # serves recorded outputs from the cassette only.
        step_id = state.step_index
        rec = cassette.replay(
            node_name="react_loop",
            step_id=step_id,
            input_payload={
                "step_index": step_id,
                "trajectory_len": len(state.trajectory),
            },
        )
        replay_events.append({"kind": "step", "step_id": step_id, "output": rec.output})
        return rec.output

    replay_skill = ReactSkill(
        name="react",
        version="0.1.0",
        description="phase5-final replay",
        llm_stub=replay_stub,
        tool_impls={"search": lambda q: f"hit:{q}"},  # pyright: ignore[reportUnknownLambdaType]
        max_steps=4,
    )
    replay_out = await replay_skill.run(ReactState())
    assert replay_out.done is True
    assert replay_out.final_answer == record_out.final_answer
    # Per-step output dicts must be byte-identical record vs replay.
    assert orjson.dumps(record_events) == orjson.dumps(replay_events)

    # ------------------------------------------------------------------
    # 6. Counterfactual: mutate one triple's confidence, re-fire promotion,
    #    build synthetic RunHistory pair, assert RunDiff carries the
    #    fact-level state delta.
    # ------------------------------------------------------------------
    cf_target_triple = ("alice", "knows", "bob")
    cf_confidences: dict[tuple[str, str, str], str] = dict(original_confidences)
    cf_confidences[cf_target_triple] = "0.25"  # mutated value (was "0.7")
    cf_shim = _ConfidenceShim(graph_store, cf_confidences)

    cf_fact_store = SQLiteFactStore(tmp_path / "facts-cf.sqlite")
    await cf_fact_store.bootstrap()

    cf_promoted = await PromoteTriplesToFacts(
        cast("Any", cf_shim),
        cf_fact_store,
        fathom_adapter,
        filter_cypher=_FILTER_CYPHER,
        rule_id="phase5_final_promote_v1",
        agent_id="phase5-final-agent",
    )
    assert len(cf_promoted) == len(_TRIPLES)

    cf_confidence_state = _confidence_state(cf_promoted)
    # Exactly the targeted triple_id changed; all other rule-bound
    # triple_ids carry the original confidence.
    target_triple_id = f"{cf_target_triple[0]}|{cf_target_triple[1]}|{cf_target_triple[2]}"
    assert orig_confidence_state[target_triple_id] == 0.7
    assert cf_confidence_state[target_triple_id] == 0.25
    other_triple_ids = set(orig_confidence_state) - {target_triple_id}
    for tid in other_triple_ids:
        assert orig_confidence_state[tid] == cf_confidence_state[tid], tid

    # Build a synthetic original + cf RunHistory whose state captures the
    # per-triple confidence map. compare() is positional on checkpoints,
    # so a single-step history with the diverging confidence in state is
    # the canonical "fact-level delta" surface (FR-27 / design §3.8.6).
    base_ts = datetime.now(UTC)
    orig_graph_hash = "0" * 64
    cf_graph_hash = "1" * 64
    runtime_hash = "phase5-final-runtime-hash"

    orig_ckpt = Checkpoint(
        run_id=_RUN_ID,
        step=0,
        branch_id=None,
        parent_step_idx=None,
        graph_hash=orig_graph_hash,
        runtime_hash=runtime_hash,
        state={"confidence_by_triple": orig_confidence_state},
        clips_facts=[],
        last_node="kg.promote",
        next_action=None,
        timestamp=base_ts,
        parent_run_id=None,
        side_effects_hash="0" * 64,
    )
    cf_ckpt = Checkpoint(
        run_id=_CF_RUN_ID,
        step=0,
        branch_id=None,
        parent_step_idx=None,
        graph_hash=cf_graph_hash,
        runtime_hash=runtime_hash,
        state={"confidence_by_triple": cf_confidence_state},
        clips_facts=[],
        last_node="kg.promote",
        next_action=None,
        timestamp=base_ts,
        parent_run_id=_RUN_ID,
        side_effects_hash="0" * 64,
    )

    orig_history = RunHistory(run_id=_RUN_ID, checkpoints=[orig_ckpt])
    cf_history = RunHistory(run_id=_CF_RUN_ID, checkpoints=[cf_ckpt])

    diff: RunDiff = compare(orig_history, cf_history)
    assert diff.original_run_id == _RUN_ID
    assert diff.counterfactual_run_id == _CF_RUN_ID
    assert diff.derived_hash == cf_graph_hash
    # Exactly one diverged step (the synthetic single-tick history) and
    # the divergence axis is state (per StepDiff precedence).
    assert len(diff.steps) == 1, diff.model_dump()
    step_diff = diff.steps[0]
    assert step_diff.diverged_at == "state", step_diff.model_dump()
    # Fact-level delta: the JSONPatch ops rewrite the mutated triple's
    # confidence path under ``/confidence_by_triple/<triple_id>``.
    expected_path = f"/confidence_by_triple/{target_triple_id}"
    matching_ops = [op for op in step_diff.state_diff if op.get("path") == expected_path]
    assert matching_ops, (
        f"expected fact-level delta at {expected_path!r}; got ops={step_diff.state_diff!r}"
    )
    op = matching_ops[0]
    assert op.get("op") == "replace", op
    assert op.get("value") == 0.25, op
    # Final-state diff carries the same fact-level delta.
    assert any(o.get("path") == expected_path for o in diff.final_state_diff), diff.final_state_diff

    # ------------------------------------------------------------------
    # 7. Checkpoint reload restores the full state.
    # ------------------------------------------------------------------
    db = await lancedb.connect_async(tmp_path / "vectors")
    tbl = await db.open_table("vectors")
    table_version = await tbl.version()
    assert isinstance(table_version, int) and table_version >= 1
    vector_versions: dict[str, int] = {"vec": table_version}

    final_state: dict[str, Any] = {
        "topic": wiki_out.topic,
        "wiki_markdown_chars": len(wiki_out.markdown),
        "promoted_facts": len(promoted),
        "deltas": len(deltas),
        "vector_versions": vector_versions,
        "confidence_by_triple": orig_confidence_state,
    }

    final_checkpoint = Checkpoint(
        run_id=_RUN_ID,
        step=1,
        branch_id=None,
        parent_step_idx=0,
        graph_hash=orig_graph_hash,
        runtime_hash=runtime_hash,
        state=final_state,
        clips_facts=[],
        last_node="run.finalize",
        next_action=None,
        timestamp=datetime.now(UTC),
        parent_run_id=None,
        side_effects_hash="0" * 64,
    )

    checkpointer = SQLiteCheckpointer(tmp_path / "checkpoint.sqlite")
    try:
        await checkpointer.bootstrap()
        await checkpointer.write(final_checkpoint)
    finally:
        await checkpointer.close()

    # Re-open a fresh checkpointer instance against the same path: the
    # FR-16 reproducibility hook says checkpoint reload must restore the
    # full state.
    reload_checkpointer = SQLiteCheckpointer(tmp_path / "checkpoint.sqlite")
    try:
        await reload_checkpointer.bootstrap()
        roundtripped = await reload_checkpointer.read_latest(_RUN_ID)
    finally:
        await reload_checkpointer.close()

    assert roundtripped is not None
    assert roundtripped.run_id == _RUN_ID
    assert roundtripped.step == 1
    assert roundtripped.last_node == "run.finalize"
    assert roundtripped.state.get("topic") == wiki_out.topic
    assert roundtripped.state.get("wiki_markdown_chars") == len(wiki_out.markdown)
    assert roundtripped.state.get("promoted_facts") == len(promoted)
    assert roundtripped.state.get("deltas") == len(deltas)
    assert roundtripped.state.get("vector_versions") == vector_versions
    assert roundtripped.state.get("confidence_by_triple") == orig_confidence_state
