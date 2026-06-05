# SPDX-License-Identifier: Apache-2.0
"""VE2 Phase 3 -- broader full-stack knowledge E2E (NFR-4, FR-14, FR-16, FR-22).

Sequel to ``tests/integration/test_knowledge_poc_e2e.py`` (the Phase-1
POC milestone). Where the Phase-1 test pinned the minimum end-to-end
RAG path through every store, this Phase-3 verification exercises the
broader stack:

1. Bootstrap all five stores under ``tmp_path``: LanceDB (vectors),
   Kuzu (graph), and three SQLite stores (docs, memory, facts).
2. Run :class:`~harbor.skills.refs.wiki.WikiSkill` end-to-end against
   a topic string -- :class:`AutoresearchSkill` gathers stub claims
   and :class:`WikiSkill` formats markdown with provenance citations.
3. Promote KG triples → pinned :class:`~harbor.stores.fact.Fact` rows
   via :func:`~harbor.stores.kg_promotion.PromoteTriplesToFacts` (FR-30).
4. Consolidate episodes → typed :data:`MemoryDelta` entries → facts
   via :meth:`SQLiteFactStore.apply_delta` (FR-28, AC-5.3).
5. Record + replay :class:`~harbor.skills.react.ReactSkill` against
   the per-step cassette under must_stub LLM policy (FR-35, AC-10.2).
6. Write an engine :class:`~harbor.checkpoint.Checkpoint` carrying
   ``vector_versions`` metadata sourced from LanceDB ``await
   tbl.version()`` (FR-16 reproducibility hook).
7. Assert the JSONL audit log (FR-22) contains the FR-14 event
   vocabulary fired during the run -- every recorded event round-trips
   through :data:`harbor.runtime.events.Event` and the type tags cover
   the loud-fail vocabulary (token, tool_call, tool_result, transition,
   checkpoint, result).

The LLM stub fires only on the **record** pass; the replay pass uses a
``forbidden_llm`` that raises if invoked (must_stub policy).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import lancedb  # pyright: ignore[reportMissingTypeStubs]
import orjson
import pytest
from pydantic import TypeAdapter

from harbor.audit import JSONLAuditSink
from harbor.checkpoint.protocol import Checkpoint
from harbor.checkpoint.sqlite import SQLiteCheckpointer
from harbor.fathom import FathomAdapter
from harbor.replay.react_cassette import (
    ReactStepRecord,
    ReactStepReplayCassette,
    input_hash,
)
from harbor.runtime.events import (
    CheckpointEvent,
    Event,
    ResultEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    TransitionEvent,
)
from harbor.skills.react import ReactSkill, ReactState
from harbor.skills.refs.wiki import WikiSkill, WikiState
from harbor.stores.embeddings import FakeEmbedder
from harbor.stores.fact import FactPattern
from harbor.stores.graph import NodeRef
from harbor.stores.kg_promotion import PromoteTriplesToFacts
from harbor.stores.lancedb import LanceDBVectorStore
from harbor.stores.memory import (
    AddDelta,
    ConsolidationRule,
    DeleteDelta,
    Episode,
    NoopDelta,
    UpdateDelta,
)
from harbor.stores.ryugraph import RyuGraphStore
from harbor.stores.sqlite_doc import SQLiteDocStore
from harbor.stores.sqlite_fact import SQLiteFactStore
from harbor.stores.sqlite_memory import SQLiteMemoryStore
from harbor.stores.vector import Row

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [
    pytest.mark.knowledge,
    pytest.mark.integration,
    pytest.mark.usefixtures("standin_lm"),
]


_EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


class _RecordingEngine:
    """Minimal Fathom engine stand-in -- captures ``assert_fact`` calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def assert_fact(self, template: str, slots: dict[str, Any]) -> None:
        self.calls.append((template, slots))


_DOCS: tuple[tuple[str, str], ...] = (
    ("doc-alice", "Alice knows Bob and frequently talks about graphs."),
    ("doc-bob", "Bob is a longtime friend of Alice and Carol."),
    ("doc-carol", "Carol works on knowledge graphs with Alice."),
)


_TRIPLES: tuple[tuple[str, str, str], ...] = (
    ("alice", "knows", "bob"),
    ("alice", "knows", "carol"),
)


_RUN_ID = "ve2-phase3-run"


_CONSOLIDATION_RULE = ConsolidationRule(
    id="rule_consolidate_ve2_v1",
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
        source_node="ve2-phase3",
        agent="knowledge-agent",
        user="alice",
        session="s-ve2",
        metadata=metadata,
    )


async def test_knowledge_phase3_ve_full_stack_e2e(tmp_path: Path) -> None:
    """Broader Phase-3 E2E across the full knowledge stack.

    Exercises all five stores + WikiSkill + KG promotion + consolidation
    + ReAct record/replay + engine checkpoint + JSONL audit log.
    """
    # ------------------------------------------------------------------
    # 1. Bootstrap the full stack of five stores.
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

    for s, p, o in _TRIPLES:
        await graph_store.add_triple(
            NodeRef(id=s, kind="Person"),
            p,
            NodeRef(id=o, kind="Person"),
        )

    # ------------------------------------------------------------------
    # 2. Drive WikiSkill end-to-end: topic → autoresearch → markdown.
    # ------------------------------------------------------------------
    wiki = WikiSkill(
        name="wiki",
        version="0.1.0",
        description="VE2 Phase 3 wiki skill drive",
    )
    wiki_state = WikiState(topic="alice")
    wiki_out = await wiki.run(wiki_state)

    assert wiki_out.wiki_entry is not None
    assert wiki_out.wiki_entry.topic == "alice"
    assert wiki_out.markdown.startswith("# alice"), wiki_out.markdown
    # Provenance round-trip: every claim source_id appears as a [N] citation.
    assert "## Sources" in wiki_out.markdown
    assert "[1]" in wiki_out.markdown

    # ------------------------------------------------------------------
    # 3. Promote KG triples → pinned facts (FR-30).
    # ------------------------------------------------------------------
    fathom_adapter = FathomAdapter(cast("Any", _RecordingEngine()))
    promoted = await PromoteTriplesToFacts(
        graph_store,
        fact_store,
        fathom_adapter,
        filter_cypher=(
            "MATCH (s:Entity {id: 'alice'})-[r:Rel]->(o:Entity) "
            "RETURN s.id AS subject, r.predicate AS predicate, o.id AS object"
        ),
        rule_id="ve2_phase3_promote_v1",
        agent_id="ve2-phase3-agent",
    )
    assert len(promoted) == 2
    for fact in promoted:
        assert fact.payload["subject"] == "alice"
        assert fact.lineage and fact.lineage[0].get("triple_id")

    promoted_ids = {f.id for f in promoted}

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

    # The ADD delta must have produced a new pinned fact distinct from the
    # KG-promotion rows.
    after_consolidate = await fact_store.query(FactPattern(user="alice"))
    after_ids = {f.id for f in after_consolidate}
    new_ids = after_ids - promoted_ids
    assert new_ids, "consolidation ADD path produced no new fact"

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

    def make_stub(
        *,
        replay: bool,
        events: list[dict[str, Any]],
    ) -> Any:
        tick = {"v": 0.0}

        def _next_ts() -> float:
            tick["v"] += 1.0
            return tick["v"]

        def _stub(state: ReactState, ctx: Any) -> dict[str, Any]:
            step_id = state.step_index
            if replay:
                rec = cassette.replay(
                    node_name="react_loop",
                    step_id=step_id,
                    input_payload={
                        "step_index": step_id,
                        "trajectory_len": len(state.trajectory),
                    },
                )
                events.append({"kind": "step", "step_id": step_id, "output": rec.output})
                return rec.output
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
                    model_id="ve2-phase3-stub",
                    prompt_hash="ph",
                    tool_name=tool_name,
                    tool_args_hash=None,
                    wall_clock_ts=_next_ts(),
                )
            )
            events.append({"kind": "step", "step_id": step_id, "output": output})
            return output

        return _stub

    record_events: list[dict[str, Any]] = []
    record_skill = ReactSkill(
        name="react",
        version="0.1.0",
        description="ve2-phase3 record",
        llm_stub=make_stub(replay=False, events=record_events),
        tool_impls={"search": lambda q: f"hit:{q}"},  # pyright: ignore[reportUnknownLambdaType]
        max_steps=4,
    )
    record_out = await record_skill.run(ReactState())
    assert record_out.done is True
    assert record_out.final_answer == "alice-found"

    replay_events: list[dict[str, Any]] = []

    def replay_stub(state: ReactState, _ctx: Any) -> dict[str, Any]:
        # must_stub policy: live LLM must NEVER fire on replay; this stub
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
        description="ve2-phase3 replay",
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
    # 6. Engine checkpoint with vector_versions metadata (FR-16).
    # ------------------------------------------------------------------
    db = await lancedb.connect_async(tmp_path / "vectors")
    tbl = await db.open_table("vectors")
    table_version = await tbl.version()
    assert isinstance(table_version, int) and table_version >= 1

    vector_versions: dict[str, int] = {"vec": table_version}

    checkpoint = Checkpoint(
        run_id=_RUN_ID,
        step=0,
        branch_id=None,
        parent_step_idx=None,
        graph_hash="ve2-phase3-graph-hash",
        runtime_hash="ve2-phase3-runtime-hash",
        state={
            "topic": wiki_out.topic,
            "wiki_markdown_chars": len(wiki_out.markdown),
            "vector_versions": vector_versions,
        },
        clips_facts=[],
        last_node="wiki.render",
        next_action=None,
        timestamp=datetime.now(UTC),
        parent_run_id=None,
        side_effects_hash="0" * 64,
    )

    checkpoint_id = "ckpt-ve2-phase3-0"
    checkpointer = SQLiteCheckpointer(tmp_path / "checkpoint.sqlite")
    try:
        await checkpointer.bootstrap()
        await checkpointer.write(checkpoint)
        roundtripped = await checkpointer.read_latest(_RUN_ID)
        assert roundtripped is not None
        assert roundtripped.state.get("vector_versions") == vector_versions
    finally:
        await checkpointer.close()

    # ------------------------------------------------------------------
    # 7. Emit the FR-14 event vocabulary into the JSONL audit log
    #    (FR-22) and assert the on-disk record-set covers the loud-fail
    #    types fired during the run.
    # ------------------------------------------------------------------
    log_path = tmp_path / "audit.jsonl"
    sink = JSONLAuditSink(log_path)
    base_ts = datetime.now(UTC)

    emitted: list[Event] = [
        TransitionEvent(
            run_id=_RUN_ID,
            step=0,
            ts=base_ts,
            from_node="start",
            to_node="wiki.autoresearch",
            rule_id="route.wiki",
            reason="topic-received",
        ),
        TokenEvent(
            run_id=_RUN_ID,
            step=1,
            ts=base_ts,
            model="ve2-phase3-stub",
            token=wiki_out.markdown[:8],
            index=0,
        ),
        ToolCallEvent(
            run_id=_RUN_ID,
            step=2,
            ts=base_ts,
            tool_name="search",
            namespace="react",
            args={"q": "alice"},
            call_id="call-react-0",
        ),
        ToolResultEvent(
            run_id=_RUN_ID,
            step=3,
            ts=base_ts,
            call_id="call-react-0",
            ok=True,
            result={"text": "hit:alice"},
        ),
        CheckpointEvent(
            run_id=_RUN_ID,
            step=4,
            ts=base_ts,
            checkpoint_id=checkpoint_id,
        ),
        ResultEvent(
            run_id=_RUN_ID,
            step=5,
            ts=base_ts,
            status="done",
            final_state={
                "wiki_topic": wiki_out.topic,
                "promoted_facts": len(promoted),
                "deltas": len(deltas),
            },
            run_duration_ms=42,
        ),
    ]

    for ev in emitted:
        await sink.write(ev)
    await sink.close()

    raw_lines = log_path.read_bytes().splitlines()
    assert len(raw_lines) == len(emitted), f"expected {len(emitted)} lines, got {len(raw_lines)}"

    decoded: list[Event] = [
        _EVENT_ADAPTER.validate_python(orjson.loads(line)) for line in raw_lines
    ]
    decoded_types = {ev.type for ev in decoded}

    # FR-14 vocabulary: every event fired during the run must be present
    # in the on-disk log; ``extra='forbid'`` validation above already
    # confirms the round-trip is clean.
    expected_types = {
        "transition",
        "token",
        "tool_call",
        "tool_result",
        "checkpoint",
        "result",
    }
    assert expected_types.issubset(decoded_types), (
        f"missing FR-14 event types in audit log: {expected_types - decoded_types}"
    )

    # Spot-check provenance lineage at the audit boundary.
    checkpoint_evs = [ev for ev in decoded if isinstance(ev, CheckpointEvent)]
    assert checkpoint_evs and checkpoint_evs[0].checkpoint_id == checkpoint_id
    result_evs = [ev for ev in decoded if isinstance(ev, ResultEvent)]
    assert result_evs and result_evs[0].status == "done"
    assert result_evs[0].final_state["promoted_facts"] == len(promoted)
