# `RetrievalNode`

Parallel fan-out retrieval node with RRF fusion (FR-26, AC-4, design §3.8).
Given a list of `StoreRef` bindings and a per-binding `store_resolver` callable,
`execute` opens an `asyncio.TaskGroup`, dispatches one branch per store, awaits
all hit-lists, and returns the fused top-`k` `Hit` list under
`state["retrieved"]`.

## Constructor

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `stores` | `list[StoreRef]` | required (positional) | Declared store bindings — one branch per ref. |
| `rerank` | `Reranker \| None` | `None` → `RRFReranker()` | Fusion strategy applied across per-branch hit lists. |
| `k` | `int` | `5` | Top-`k` for both per-branch dispatch and fused output. |
| `store_resolver` | `Callable[[str], VectorStore \| GraphStore \| DocStore]` | required | Maps `StoreRef.name` → concrete provider instance. |
| `cypher_by_store` | `dict[str, str] \| None` | `None` | Map of graph-store name → compile-time Cypher; used to derive `read` vs `write` capability. |

`stores` is positional; everything after `*` is keyword-only.

## Capabilities

`RetrievalNode.requires` is derived **once at construction time** from
`stores` + `cypher_by_store`. Each declared store contributes
`db.<name>:read` by default; graph branches whose compile-time Cypher contains
a write keyword (per `Linter.requires_write`) escalate to `db.<name>:write`.
This derivation is replay-safe — the capability set never recomputes during
`execute`.

## State contract

- **Reads** — `state.query` (`str | None`); `None` returns `[]` from the
  vector branch.
- **Writes** — `{"retrieved": [Hit, ...]}` — fused, top-`k`.

Per-branch behaviour:

- **Vector** — `store.search(text=query, k=k)`. Provider's default
  `mode="vector"` falls back to `"fts"` when only `text` is supplied (matches
  `LanceDBVectorStore` ergonomics).
- **Doc** — `store.query(filter=None, limit=k)` mapped to `Hit` rows
  (`score=0.0` — `DocStore` has no native ranking; RRF still produces stable
  order via list rank).
- **Graph** — POC skip; full Triple-Cypher dispatch lands in Phase-2.

Events: emits a `stargraph.transition` payload per branch via
`ctx.emit_event` when the context exposes that hook (best-effort; Phase-1
`ExecutionContext` is minimal — silently skipped when absent).

## Side effects + replay

- `side_effects = read` — read-only fan-out.
- Replay re-executes (provider-side determinism is the provider's contract).

## YAML

```yaml
ir_version: "1.0.0"
id: "skill:rag.example"
nodes:
  - id: retrieve
    kind: retrieval
state_schema:
  query: str
  retrieved: list
```

See `tests/fixtures/skills/rag/example.yaml` for the RagSkill POC.

## Errors

- Any provider-raised exception inside the `TaskGroup` aborts the group; sibling
  branches are cancelled. Phase-3 promotes the bare `asyncio.TaskGroup` to the
  engine-managed `stargraph.runtime.parallel.create_task_group`.

## See also

- [`NodeBase`](base.md) — abstract contract.
- [`StoreRef`](../ir-schema.md) — IR binding shape.
- `stargraph.stores.rerankers.RRFReranker` — default fusion strategy.
