# `FactStore`

Semantic-fact storage contract (FR-6, design §3.5). Concrete in-tree
provider: [`SQLiteFactStore`](#sqlitefactstore). Differs from
`MemoryStore` in scope: facts are session-independent, keyed at
`(user, agent)`.

## Protocol surface

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class FactStore(Protocol):
    async def bootstrap(self) -> None: ...
    async def health(self) -> StoreHealth: ...
    async def migrate(self, plan: MigrationPlan) -> None: ...

    async def pin(self, fact: Fact) -> None: ...
    async def query(self, pattern: FactPattern) -> list[Fact]: ...
    async def unpin(self, fact_id: str) -> None: ...
```

`SQLiteFactStore` additionally exposes `apply_delta(delta: MemoryDelta) -> None`
as a provider extension -- the only acceptable promotion path from a
`MemoryStore` (design §4.2 lineage).

## Lifecycle

| Method | Behaviour |
|---|---|
| `bootstrap()` | Idempotent. Creates the `facts` table and applies the WAL pragma block. |
| `health()` | `StoreHealth` with `fragment_count` (fact count). |
| `migrate(plan)` | v1 stub -- validation enforced; replay lands in Phase 3. |

## CRUD

### `pin(fact: Fact) -> None`

Insert-or-replace by `fact.id`. `payload`, `lineage`, `metadata` all
round-trip through orjson JSONB.

### `query(pattern: FactPattern) -> list[Fact]`

POC equality match: filters on the `user` / `agent` columns, then
post-filters in Python on the decoded `payload` for `subject` /
`predicate` / `object` slot equality. Wildcard (`None`) slots match
everything. Full pattern semantics (regex, wildcard composition) land
in Phase 3.

### `unpin(fact_id: str) -> None`

Removes the row by `fact_id`. No-op if absent.

### `apply_delta(delta: MemoryDelta) -> None` (provider extension)

Switches on the discriminated union:

| `delta.kind` | Action |
|---|---|
| `"add"` | `pin()` a new fact built from `fact_payload` + provenance. |
| `"update"` | `unpin()` every id in `replaces`, then `pin()` the new fact. |
| `"delete"` | `unpin()` every id in `replaces`. |
| `"noop"` | Audit-only; no store mutation. |

Provenance fields (`rule_id`, `source_episode_ids`, `promotion_ts`,
`confidence`, plus `replaces` for UPDATE/DELETE) are validated
non-empty before any mutation runs.

!!! note "Phase-1 deferrals"
    Embedding-similarity dedup over the `add` path is deferred to
    Phase 3 -- the POC trusts the consolidation rule output verbatim.
    Lineage chaining for UPDATE/DELETE (linking the new fact's lineage
    to the unpinned predecessors) also lands in Phase 3.

## Value models

### `Fact`

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | Primary key. |
| `user` | `str` | Scope axis. |
| `agent` | `str` | Scope axis. |
| `payload` | `dict[str, Any]` | Fact body; orjson JSONB. |
| `lineage` | `list[dict[str, Any]]` | **Mandatory.** Each entry traces back to the originating episode ids / triple ids / rule firings (design §4.2). |
| `confidence` | `float` | Caller-supplied or copied from delta. |
| `pinned_at` | `datetime` | Set at `pin()` / `apply_delta()` time. |
| `metadata` | `dict[str, Any]` | Free-form; defaults to `{}`. |

`lineage` is **never optional** -- enforced by an AST-walker test
inherited from engine NFR-6. Lineage rows are nested
`{"kind", "source_id", ...}` objects (or `{"rule_id", "source_episode_ids", "promotion_ts"}`
shape from `apply_delta`) -- a scalar-only union could not express them,
which is why `payload` / `lineage` / `metadata` stay `dict[str, Any]`
rather than restricted scalars.

### `FactPattern`

| Field | Type | Default | Behaviour |
|---|---|---|---|
| `subject` | `str \| None` | `None` | Wildcard if `None`, else equality match on `payload["subject"]`. |
| `predicate` | `str \| None` | `None` | As above on `payload["predicate"]`. |
| `object` | `str \| None` | `None` | As above on `payload["object"]`. |
| `user` | `str \| None` | `None` | Column equality filter. |
| `agent` | `str \| None` | `None` | Column equality filter. |

## `SQLiteFactStore`

Default in-tree provider (`stargraph.stores.sqlite_fact`). POC scope of
FR-6 / FR-13 / FR-30.

### Constructor

```python
from pathlib import Path
from stargraph.stores import SQLiteFactStore

store = SQLiteFactStore(path=Path("./.facts"))
await store.bootstrap()
```

| Param | Type | Notes |
|---|---|---|
| `path` | `Path` | SQLite database file (created on bootstrap; parent dirs auto-created). |

### Dependencies

`aiosqlite` is a base dependency -- no optional extra required.

### Schema

```sql
CREATE TABLE IF NOT EXISTS facts (
  fact_id    TEXT PRIMARY KEY,
  user       TEXT NOT NULL,
  agent      TEXT NOT NULL,
  payload    BLOB NOT NULL,   -- orjson JSONB
  lineage    BLOB NOT NULL,   -- orjson JSONB
  confidence REAL NOT NULL,
  metadata   BLOB NOT NULL,   -- orjson JSONB
  pinned_at  TEXT NOT NULL
)
```

### Special behaviours

- **WAL pragma block** -- inherits the engine FR-17 SQLite pragma set.
- **Single-writer lock** -- every write path serialises through
  `_lock_for(self._path)`.
- **`apply_delta` is the lineage seam** -- every fact written through
  `apply_delta` carries a lineage row containing the consolidation
  rule's `rule_id`, the originating episode ids, and the promotion
  timestamp. Direct `pin()` callers are responsible for supplying their
  own `lineage` (the AST-walker test will fail if they leave it empty).

### YAML wiring

```yaml
stores:
  fact: sqlite:./.facts
```

## `PromoteTriplesToFacts`

Graph→fact promotion path (FR-30 / AC-6.x, design §3.13). Lives at
`stargraph.stores.kg_promotion`. Mirrors `apply_delta` for the graph side:
selects rows from a portable Cypher query and pins each as a `Fact`.

```python
from stargraph.stores.kg_promotion import PromoteTriplesToFacts

facts = await PromoteTriplesToFacts(
    graph_store, fact_store, fathom_adapter,
    filter_cypher="MATCH (s:Entity)-[r:Rel]->(o:Entity) RETURN s.id AS subject, r.predicate AS predicate, o.id AS object",
    rule_id="rule.promotion.evidence",
    agent_id="agent.curator",
)
```

| Step | Behaviour |
|---|---|
| 1 | `Linter.check(filter_cypher)` -- portable subset only. |
| 2 | `Linter.requires_write(filter_cypher)` -- mutating queries rejected. Promotion must be read-only. |
| 3 | `graph_store.query(filter_cypher)` materialises a `ResultSet`. |
| 4 | For each row: builds `{subject, predicate, object, source}` slots and best-effort calls `fathom_adapter.assert_with_provenance(...)`. Adapter failures are logged and tolerated. |
| 5 | `fact_store.pin(fact)` with lineage `[{triple_id, rule_id, agent_id, promotion_ts}]`. |

!!! warning "One-way semantics"
    Triple deletion in the underlying graph does **not** auto-retract
    the promoted `Fact`. Callers needing bidirectional linkage must
    invoke `FactStore.unpin()` themselves.

## Errors raised

| Error | Raised when |
|---|---|
| `MigrationNotSupported` | `migrate` saw a non-`add_column` op or non-nullable add. |
| `UnportableCypherError` | `PromoteTriplesToFacts` filter is non-portable or mutating. |
| `FactConflictError` | (Reserved) duplicate-id pin under future strict mode. |
| `ValueError` | (From `_validate_delta_provenance`) provenance fields missing on `apply_delta`. |
