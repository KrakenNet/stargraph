# `DocStore`

Document storage contract (FR-4, design §3.3). Concrete in-tree
provider: [`SQLiteDocStore`](#sqlitedocstore).

## Protocol surface

```python
from typing import Any, Protocol, runtime_checkable

@runtime_checkable
class DocStore(Protocol):
    async def bootstrap(self) -> None: ...
    async def health(self) -> StoreHealth: ...
    async def migrate(self, plan: MigrationPlan) -> None: ...

    async def put(
        self,
        doc_id: str,
        content: str | bytes,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...
    async def get(self, doc_id: str) -> Document | None: ...
    async def query(
        self,
        filter: str | None = None,
        *,
        limit: int = 100,
    ) -> list[Document]: ...
```

## Lifecycle

| Method | Behaviour |
|---|---|
| `bootstrap()` | Idempotent. Creates the `documents` table and applies the shared SQLite WAL pragma block. |
| `health()` | `StoreHealth` with `fragment_count` (row count) and `version` from the `_migrations` ledger. |
| `migrate(plan)` | v1: `add_column` only via `ALTER TABLE ADD COLUMN`; bumps `_migrations.version`. |

## CRUD

### `put(doc_id, content, *, metadata=None)`

Insert-or-replace by `doc_id`. `content` may be `str` (encoded UTF-8 to
BLOB with `is_text=1`) or `bytes` (stored raw with `is_text=0`).
`metadata` round-trips through orjson JSONB.

### `get(doc_id) -> Document | None`

Returns `None` for unknown `doc_id`; otherwise reconstructs the original
`str` / `bytes` content based on the stored `is_text` flag.

### `query(filter=None, *, limit=100) -> list[Document]`

Returns up to `limit` documents matching the SQL `WHERE` fragment in
`filter`. `filter=None` returns the first `limit` rows.

!!! warning "Filter is raw SQL"
    `filter` is interpolated directly into the query, with no parameter
    binding. Callers must scope it themselves -- in IR-driven graphs the
    filter is operator-authored and source-controlled, so this is by
    design.

## Value model

### `Document`

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | Primary key. |
| `content` | `str \| bytes` | Provider restores the original Python type via `is_text`. |
| `metadata` | `dict[str, Any]` | Round-trips through orjson JSONB; nested dicts/lists preserved. |
| `created_at` | `datetime` | Set at `put()` time (`datetime.now(UTC)`). |

`metadata` stays typed as `dict[str, Any]` (rather than the JSON-scalar
union used by `stargraph.stores.vector.Row`): DocStore is the catch-all
unstructured-payload tier, the column round-trips through JSONB, and
the columnar restrictions that justify scalar-only metadata for
columnar backends do not apply here.

## `SQLiteDocStore`

Default in-tree provider (`stargraph.stores.sqlite_doc`). POC scope of
FR-4 / FR-13.

### Constructor

```python
from pathlib import Path
from stargraph.stores import SQLiteDocStore

store = SQLiteDocStore(path=Path("./.docs"))
await store.bootstrap()
```

| Param | Type | Notes |
|---|---|---|
| `path` | `Path` | SQLite database file (created on bootstrap; parent dirs auto-created). |

### Dependencies

`aiosqlite` is a base dependency -- no optional extra required.
SQLite ships with Python.

### Schema

```sql
CREATE TABLE IF NOT EXISTS documents (
  doc_id     TEXT PRIMARY KEY,
  content    BLOB NOT NULL,
  is_text    INTEGER NOT NULL,
  metadata   BLOB NOT NULL,
  created_at TEXT NOT NULL
)
```

The shared `_migrations` ledger tracks `target_version` after each
applied `add_column` op.

### Special behaviours

- **WAL pragma block** -- `_apply_pragmas` sets `journal_mode=WAL`,
  `synchronous=NORMAL`, `busy_timeout=5000`, `foreign_keys=ON` on every
  connection (engine FR-17 standard).
- **Single-writer lock** -- every write path (`bootstrap`, `migrate`,
  `put`) serialises through `_lock_for(self._path)`.
- **`is_text` flag** -- preserves the str/bytes distinction so
  `get()` / `query()` round-trip the exact Python type.

### YAML wiring

```yaml
stores:
  doc: sqlite:./.docs
```

## Errors raised

| Error | Raised when |
|---|---|
| `MigrationNotSupported` | `migrate` saw a non-`add_column` op, a non-nullable add, or an `add_column` op missing `table` / `column` strings. |
| `OperationalError` (aiosqlite) | Bad `filter` SQL, locked database, etc. -- not wrapped in v1. |
