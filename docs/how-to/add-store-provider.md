# How to Add a Store Provider

## Goal

Implement a new provider for one of Stargraph's five Store Protocols
(Vector, Graph, Doc, Memory, Fact) and register it via the
`stargraph.stores` entry-point group + `register_stores` hook.

## Prerequisites

- Stargraph installed (`pip install stargraph[stores]>=0.2`).
- A backend you want to wrap (e.g. Postgres-pgvector, Neo4j, Mongo).
- Familiarity with [Stores reference](../knowledge/stores.md).

## Steps

### 1. Pick the Protocol

| Protocol | Module | Use when |
| --- | --- | --- |
| [`VectorStore`][vector] | `stargraph.stores.vector` | ANN, FTS, hybrid search over embedded text. |
| [`GraphStore`][graph] | `stargraph.stores.graph` | Triple writes + portable-subset Cypher. |
| `DocStore` | `stargraph.stores.doc` | Document store with metadata-filter querying. |
| `MemoryStore` | `stargraph.stores.memory` | Episode/fact-of-event persistence. |
| `FactStore` | `stargraph.stores.fact` | CLIPS fact persistence + pattern queries. |

All five share the lifecycle triple `bootstrap` / `health` / `migrate`
plus per-store CRUD. Decorate with `@runtime_checkable` already on the
Protocol — your class doesn't need to subclass anything.

### 2. Implement the Protocol structurally

```python
# my_stores/pgvector.py
from pathlib import Path
from typing import Literal

from stargraph.stores import Hit, MigrationPlan, Row, StoreHealth
from stargraph.stores._common import _detect_fs_type, _lock_for, _nfs_warning


class PGVectorStore:
    """Sketch — wraps Postgres pgvector behind the VectorStore Protocol."""

    def __init__(self, dsn: str, *, table: str = "stargraph_vec") -> None:
        self.dsn = dsn
        self.table = table
        self._pool = None  # asyncpg pool created in bootstrap

    async def bootstrap(self) -> None:
        """Idempotent schema + FR-8 embed-hash gate."""
        ...

    async def health(self) -> StoreHealth:
        return StoreHealth(
            ok=True,
            version=1,
            fragment_count=None,
            fs_type="pg",
            lock_state="held",
        )

    async def migrate(self, plan: MigrationPlan) -> None:
        """v1 supports add_column only — reject anything else with MigrationNotSupported."""
        ...

    async def upsert(self, rows: list[Row]) -> None:
        ...

    async def search(
        self,
        *,
        vector: list[float] | None = None,
        text: str | None = None,
        filter: str | None = None,
        k: int = 10,
        mode: Literal["vector", "fts", "hybrid"] = "vector",
    ) -> list[Hit]:
        ...

    async def delete(self, ids: list[str]) -> int:
        ...
```

### 3. Honor the single-writer lock

Process-local `asyncio.Lock` instances guard concurrent writes per
resolved path. Use `_lock_for(path)` from
[`stargraph.stores._common`][common] inside any method that mutates state:

```python
async def upsert(self, rows: list[Row]) -> None:
    async with _lock_for(Path(self.dsn)):
        await self._do_upsert(rows)
```

`StoreHealth.lock_state` should report `"held"` while a write is in
flight, `"free"` otherwise.

### 4. Surface FS warnings

Networked filesystems (NFS / SMB / CIFS) cannot enforce single-writer
locks across hosts. `_detect_fs_type` + `_nfs_warning` produce the
canonical warning string for `StoreHealth.warnings`:

```python
async def health(self) -> StoreHealth:
    fs = _detect_fs_type(Path(self.dsn))
    warnings = []
    if (w := _nfs_warning(fs)) is not None:
        warnings.append(w)
    return StoreHealth(ok=True, version=1, fs_type=fs, lock_state="free", warnings=warnings)
```

### 5. Honor the FR-8 embed-hash gate (vector stores)

Vector providers must persist a 5-tuple sidecar (`model_id`, `revision`,
`content_hash`, `ndims`, `schema_v`) on first bootstrap and verify on
re-entry. Use `_write_embed_metadata` / `_verify_embed_metadata` from
`_common.py` — they raise `IncompatibleEmbeddingHashError` on drift.

### 6. Register via `register_stores`

```python
# my_stores/_pack.py
from stargraph.ir import StoreSpec
from stargraph.plugin._markers import hookimpl


@hookimpl
def register_stores() -> list[StoreSpec]:
    return [
        StoreSpec(
            name="pgvec",
            provider="my_stores.pgvector:PGVectorStore",
            protocol="vector",
            config_schema={
                "type": "object",
                "properties": {"dsn": {"type": "string"}, "table": {"type": "string"}},
                "required": ["dsn"],
            },
            capabilities=[],   # empty -> default db.{name}:read|write derived
        ),
    ]
```

`StoreSpec.effective_capabilities()` returns
`["db.pgvec:read", "db.pgvec:write"]` when `capabilities=[]`.

### 7. Test against the conformance suite

```python
# tests/test_pgvector_conformance.py
from my_stores.pgvector import PGVectorStore

# Use the bundled Protocol-shape test harness
from tests.conformance.vector_suite import VectorStoreConformance


class TestPGVector(VectorStoreConformance):
    @pytest.fixture
    async def store(self, postgres_dsn):
        return PGVectorStore(postgres_dsn)
```

<!-- TODO: verify the public path of the conformance suite once it lands under tests/. -->

## Wire it up

```toml
# pyproject.toml
[project.entry-points."stargraph"]
stargraph_plugin = "my_stores._plugin:stargraph_plugin"

[project.entry-points."stargraph.stores"]
pgvector = "my_stores._pack"
```

Reference from a graph:

```yaml
# stargraph.yaml
stores:
  - name: kb_vec
    provider: pgvector            # matches StoreSpec.name? See ir-schema.md StoreRef
```

## Verify

```bash
pip install -e .
STARGRAPH_TRACE_PLUGINS=1 python -c "
from stargraph.plugin.loader import build_plugin_manager
pm = build_plugin_manager()
for specs in pm.hook.register_stores():
    for s in specs:
        print(s.name, s.protocol, s.provider)
"
```

You should see `pgvec vector my_stores.pgvector:PGVectorStore`.

## Troubleshooting

!!! warning "Common failure modes"
    - **`IncompatibleEmbeddingHashError`** — the 5-tuple sidecar drifted
      from the live embedder. Re-bootstrap a fresh table or pin the
      embedder's `(model_id, revision)`.
    - **`MigrationNotSupported`** — v1 only supports
      `add_column (nullable=True)`. Drops, renames, type narrows are
      forward-unsafe; rebuild instead.
    - **`StoreHealth.warnings` includes `networked filesystem detected`**
      — locks are unsafe across hosts; move to a local mount or
      single-host deployment.

## See also

- [Stores reference](../knowledge/stores.md)
- [Cypher subset](../knowledge/cypher-subset.md) — for `GraphStore`
  providers.
- [`stargraph.stores._common`][common]
- [`VectorStore`][vector] / [`GraphStore`][graph] Protocols
- [Bundled providers](https://github.com/KrakenNet/stargraph/tree/main/src/stargraph/stores)

[vector]: https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/stores/vector.py
[graph]: https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/stores/graph.py
[common]: https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/stores/_common.py
