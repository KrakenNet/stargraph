# `MemoryStore`

Episodic-memory contract (FR-5, FR-27, FR-28, FR-29, design §3.4).
Concrete in-tree provider: [`SQLiteMemoryStore`](#sqlitememorystore).

## Protocol surface

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class MemoryStore(Protocol):
    async def bootstrap(self) -> None: ...
    async def health(self) -> StoreHealth: ...
    async def migrate(self, plan: MigrationPlan) -> None: ...

    async def put(
        self,
        episode: Episode,
        *,
        user: str,
        session: str,
        agent: str,
    ) -> None: ...

    async def recent(
        self,
        user: str,
        session: str | None = None,
        agent: str | None = None,
        *,
        limit: int,
    ) -> list[Episode]: ...

    async def consolidate(
        self,
        rule: ConsolidationRule,
    ) -> list[MemoryDelta]: ...
```

## Lifecycle

| Method | Behaviour |
|---|---|
| `bootstrap()` | Idempotent. Creates the `episodes` table + `idx_episodes_scope_ts(scope_key, timestamp DESC)` index. Applies WAL pragmas. |
| `health()` | `StoreHealth` with `fragment_count` (episode count). |
| `migrate(plan)` | v1 stub -- validation enforced; full replay lands in Phase 3. |

## CRUD

### `put(episode, *, user, session, agent)`

Inserts an episode keyed at `(user, session, agent)`. Scope is encoded
as a trailing-separator key:

```
/user/{user}/session/{session}/agent/{agent}/
```

The trailing slash prevents prefix collisions on widening `LIKE`
reads -- without it, `/user/alice/...` would match `/user/aliceX/...`
under `LIKE '/user/alice%'` (FR-27).

### `recent(user, session=None, agent=None, *, limit) -> list[Episode]`

Widening read. Omitted scope levels become `%` LIKE wildcards;
trailing separator is preserved. Returns the most-recent `limit`
episodes ordered by `timestamp DESC`.

| Call | LIKE pattern |
|---|---|
| `recent("alice", "S1", "rag", limit=10)` | `/user/alice/session/S1/agent/rag/` |
| `recent("alice", "S1", limit=10)` | `/user/alice/session/S1/agent/%/` |
| `recent("alice", limit=10)` | `/user/alice/session/%/agent/%/` |

### `consolidate(rule) -> list[MemoryDelta]`

Runs `rule.when_filter` (a SQL `WHERE` fragment over `episodes`) against
stored episodes, classifies each match into a Mem0-style typed delta,
and returns the list. The classification table:

| Source | Resulting `MemoryDelta` |
|---|---|
| `metadata["intent"] == "noop"` | `NoopDelta` (audit only) |
| `metadata["intent"] == "delete"` + `replaces` | `DeleteDelta` |
| `metadata["intent"] == "update"` + `replaces` | `UpdateDelta` |
| Default (no intent set) | `AddDelta` |
| Default + intra-batch `(subject, predicate)` repeat | `UpdateDelta` (replaces prior episode id) |

!!! note "POC scope"
    Cross-store dedup against existing `Fact` rows is **not** performed
    here -- the `MemoryStore` Protocol takes only `rule` (no `FactStore`
    handle). Callers needing pre-existing-fact classification encode the
    intent on the episode upstream (Mem0 pattern). Embedding-similarity
    dedup over `add` lands in Phase 3.

## Value models

### `Episode`

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | Primary key. |
| `content` | `str` | Episode body. |
| `timestamp` | `datetime` | Episode time (caller-supplied). |
| `source_node` | `str` | Originating graph node id. |
| `agent` | `str` | Agent id (also part of scope). |
| `user` | `str` | User id (also part of scope). |
| `session` | `str` | Session id (also part of scope). |
| `metadata` | `dict[str, Any]` | Round-trips through orjson JSONB. Honoured keys: `intent`, `replaces`, `subject`, `predicate`, `object`. |

### `ConsolidationRule`

IR-declared episodic→semantic consolidation rule (FR-28, FR-29).

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | Stamped onto every emitted delta as `rule_id`. |
| `cadence` | `dict[str, Any]` | IR knob -- `{every: N}` or `{cron: "..."}`. Re-uses the CLIPS rule scheduling in `stargraph.fathom`. |
| `when_filter` | `str` | SQL `WHERE` fragment selecting eligible episodes. Empty string == match-all. |
| `then_emits` | `list[str]` | Fact-channel names this rule promotes into. |

### `MemoryDelta` union

Pydantic-discriminated union over `kind`. The only acceptable promotion
path into `FactStore.apply_delta` (design §4.2 lineage). Provenance
fields are mandatory on every variant -- a malformed delta cannot
silently land in the `lineage` column.

| Variant | `kind` | Required fields |
|---|---|---|
| `AddDelta` | `"add"` | `fact_payload`, `source_episode_ids`, `promotion_ts`, `rule_id`, `confidence` |
| `UpdateDelta` | `"update"` | + `replaces: list[str]` |
| `DeleteDelta` | `"delete"` | `replaces`, `source_episode_ids`, `promotion_ts`, `rule_id`, `confidence` |
| `NoopDelta` | `"noop"` | `source_episode_ids`, `promotion_ts`, `rule_id`, `confidence` |

```python
MemoryDelta = Annotated[
    AddDelta | UpdateDelta | DeleteDelta | NoopDelta,
    Field(discriminator="kind"),
]
```

## `SQLiteMemoryStore`

Default in-tree provider (`stargraph.stores.sqlite_memory`). POC scope of
FR-5 / FR-13 / FR-27 / FR-28.

### Constructor

```python
from pathlib import Path
from stargraph.stores import SQLiteMemoryStore

store = SQLiteMemoryStore(path=Path("./.memory"))
await store.bootstrap()
```

| Param | Type | Notes |
|---|---|---|
| `path` | `Path` | SQLite database file (created on bootstrap; parent dirs auto-created). |

### Dependencies

`aiosqlite` is a base dependency -- no optional extra required.

### Schema

```sql
CREATE TABLE IF NOT EXISTS episodes (
  id         TEXT PRIMARY KEY,
  scope_key  TEXT NOT NULL,
  content    BLOB NOT NULL,   -- orjson JSONB of the Episode payload
  timestamp  TEXT NOT NULL,
  metadata   BLOB NOT NULL    -- orjson JSONB
);
CREATE INDEX IF NOT EXISTS idx_episodes_scope_ts
  ON episodes(scope_key, timestamp DESC);
```

### Special behaviours

- **WAL pragma block** -- inherits the engine FR-17 SQLite pragma set.
- **Single-writer lock** -- every write path serialises through
  `_lock_for(self._path)`.
- **Trailing-separator scope key** -- prevents widening-read prefix
  collisions across user/session/agent boundaries.
- **Default ADD with intra-batch dedup** -- inside a single
  `consolidate()` pass, a repeat `(subject, predicate)` from
  `metadata` is automatically promoted to `UpdateDelta` against the
  prior episode id.

### YAML wiring

```yaml
stores:
  memory: sqlite:./.memory
```

## Errors raised

| Error | Raised when |
|---|---|
| `MigrationNotSupported` | `migrate` saw a non-`add_column` op or non-nullable add. |
| `MemoryScopeError` | (Reserved) widening-read scope mis-use. |
| `ConsolidationRuleError` | (Reserved) malformed `ConsolidationRule`. |

Provenance validation is performed via
`stargraph.stores._delta._validate_delta_provenance` on every emitted
delta -- see [fact.md](fact.md) for the receiving end.

## Promotion path

Memory→fact promotion is one-way: `MemoryStore.consolidate()` produces
typed `MemoryDelta` instances which are then applied to a `FactStore`
via `FactStore.apply_delta(delta)`. The graph-side analog
(`PromoteTriplesToFacts`) lives in `stargraph.stores.kg_promotion` and is
covered on the [`FactStore` reference](fact.md#promotetriplestofacts).
