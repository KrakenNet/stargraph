# Knowledge Subsystem

Knowledge is the **agency-and-memory** layer above the engine. It supplies
durable state for skills (RAG, autoresearch, wiki) and the agent-as-subgraph
pattern, while inheriting every replay and provenance guarantee the engine
already enforces.

## What it ships

| Module | Responsibility | Public surface |
|---|---|---|
| `stargraph.stores` | Five storage Protocols + three default embeddable Providers | `VectorStore`, `GraphStore`, `DocStore`, `MemoryStore`, `FactStore` |
| `stargraph.skills` | Skill base class, ReAct primitive, three reference skills | `Skill`, `SkillKind`, `react`, `refs.{rag,autoresearch,wiki}` |
| `stargraph.nodes.retrieval` | Parallel store fan-out node with rank fusion | `RetrievalNode` |
| `stargraph.nodes.memory` | Episode → semantic-fact promotion node | `MemoryWriteNode` |

The Protocol layer mirrors `Checkpointer`'s shape — `bootstrap / health /
migrate` lifecycle plus per-store CRUD. Three defaults ship in-tree:
**LanceDB** (vector), **RyuGraph** (graph), and a **SQLite trio** (doc / memory /
fact). Every embedded backend enforces single-writer concurrency through an
in-process `asyncio.Lock`.

## Map of the section

- [Stores](stores.md) — five Protocols, three default backends, embed-hash
  drift gate, single-writer concurrency.
- [Skills](skills.md) — Skill base class, ReAct, agent-as-subgraph,
  declared output channels.
- [Memory](memory.md) — episodic → semantic consolidation, Mem0-style typed
  deltas, salience scoring (Park 2023).
- [Retrieval](retrieval.md) — `RetrievalNode` parallel fan-out, RRF fusion
  with deterministic sum order.

## Design stance

- **Correctness over speed.** Provenance and replay are load-bearing — every
  promotion to the `FactStore` carries `source_episode_ids`, `promotion_ts`,
  `rule_id`, and `confidence` as Pydantic-required fields.
- **Reuse, not duplicate.** Pluggy loader, `register_skills` /
  `register_stores` hookspecs, `FathomAdapter.assert_with_provenance`, the
  orjson JSONB codec, engine TaskGroup primitives, and replay cassette
  plumbing are imported, not re-implemented.
- **Embed-hash drift is silent corruption.** `IncompatibleEmbeddingHashError`
  fires at every `bootstrap()` re-entry when `(model_id, revision,
  content_hash, ndims)` does not match the table-level metadata.
- **Declared output channels only.** Skill subgraphs cannot mutate parent
  state via undeclared keys (LangGraph #4182 mitigation). The boundary
  translator consumes `state_schema` field names as the write whitelist.
