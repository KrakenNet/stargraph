# `VectorStore`

Structural contract for vector-store providers (FR-1, FR-2, design §3.1).
Concrete in-tree provider: [`LanceDBVectorStore`](#lancedbvectorstore).

## Protocol surface

```python
from typing import Literal, Protocol, runtime_checkable

@runtime_checkable
class VectorStore(Protocol):
    async def bootstrap(self) -> None: ...
    async def health(self) -> StoreHealth: ...
    async def migrate(self, plan: MigrationPlan) -> None: ...

    async def upsert(self, rows: list[Row]) -> None: ...
    async def search(
        self,
        *,
        vector: list[float] | None = None,
        text: str | None = None,
        filter: str | None = None,
        k: int = 10,
        mode: Literal["vector", "fts", "hybrid"] = "vector",
    ) -> list[Hit]: ...
    async def delete(self, ids: list[str]) -> int: ...
```

`@runtime_checkable` -- `isinstance(provider, VectorStore)` succeeds for
any class that structurally satisfies the contract; no inheritance
required.

## Lifecycle

| Method | Behaviour |
|---|---|
| `bootstrap()` | Idempotent. Creates the table + writes the FR-8 5-tuple drift gate `(model_id, revision, content_hash, ndims, schema_v)` into a sidecar `_stargraph_meta` table. On re-entry, mismatch raises `IncompatibleEmbeddingHashError`. |
| `health()` | Returns `StoreHealth` with `fragment_count`, `embedding_hash`, `fs_type`, `lock_state`. NFS/SMB/CIFS surfaces a warning. |
| `migrate(plan)` | v1: `add_column` only. Narrows / renames / drops raise `MigrationNotSupported`. |

## CRUD

### `upsert(rows: list[Row]) -> None`

Insert-or-replace by `id`. Always accepts a list -- never a single
`Row`. Rows missing a `vector` are embedded via `embedder.embed(kind="document")`
before write. Vector length must equal `embedder.ndims` or `ValueError`
is raised.

### `search(*, vector, text, filter, k=10, mode="vector") -> list[Hit]`

Returns top-`k` `Hit` rows for the requested mode.

| `mode` | Required input | Behaviour |
|---|---|---|
| `"vector"` | `vector` | Pure ANN. Raises `ValueError` if `vector is None`. |
| `"fts"` | `text` | BM25 full-text search. Raises `ValueError` if `text is None`. |
| `"hybrid"` | at least one of `vector` / `text` | Runs each input branch and fuses via `RRFReranker` (FR-16). |

`filter` is a SQL `WHERE`-clause fragment evaluated against metadata
columns. Score semantics: cosine similarity (vector), BM25 (FTS), fused
reciprocal-rank score (hybrid).

!!! info "Ergonomic fallback"
    When `mode="vector"` (the default) and only `text` is supplied,
    `LanceDBVectorStore` silently falls back to `mode="fts"`. Explicit
    `mode="vector"` callers always get strict pure-ANN behaviour.

### `delete(ids: list[str]) -> int`

Returns the number of rows actually deleted (compared against
`count_rows()` before/after).

## Value models

### `Row`

Upsert payload. At least one of `vector` / `text` must be supplied --
vector-only rows feed pure ANN, text-only rows feed FTS, both feed
hybrid.

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | Primary key. |
| `vector` | `list[float] \| None` | Embedded from `text` if absent. |
| `text` | `str \| None` | FTS body. |
| `metadata` | `dict[str, MetadataValue]` | JSON scalars only. |

```python
type MetadataValue = str | int | float | bool
```

Metadata is restricted to JSON scalars so columnar backends (LanceDB /
Arrow) can map metadata to typed columns without per-row schema
inference.

### `Hit`

Search result row -- vectors are **not** echoed back; callers re-fetch
via `id` if they need the raw embedding.

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | Source row id. |
| `score` | `float` | Provider-defined; cosine / BM25 / RRF-fused. |
| `metadata` | `dict[str, MetadataValue]` | Mirrors the upserted scalar dict. |

## `LanceDBVectorStore`

Default in-tree provider (`stargraph.stores.lancedb`). POC scope of FR-2 /
FR-8 / FR-10 / FR-16.

### Constructor

```python
from pathlib import Path
from stargraph.stores import LanceDBVectorStore, MiniLMEmbedder

store = LanceDBVectorStore(
    path=Path("./.lance"),
    embedder=MiniLMEmbedder(),
    table_name="vectors",   # default
    tmp_dir=None,            # defaults to <path>/.tmp
)
```

| Param | Type | Default | Notes |
|---|---|---|---|
| `path` | `Path` | required | LanceDB dataset root. |
| `embedder` | `Embedding` | required | Used for the FR-8 drift gate + auto-embed of text-only rows. |
| `table_name` | `str` | `"vectors"` | Per-store table name. |
| `tmp_dir` | `Path \| None` | `<path>/.tmp` | FTS scratch dir; isolates `LANCE_TEMP_DIR` per store (lance#2461). |

### Dependencies

Optional extra: `stargraph[stores]` (`lancedb`, `pyarrow`, plus `ryugraph`
for the graph half). The provider is loaded lazily through
`stargraph.stores.__getattr__` -- importing `stargraph.stores` without the
extra installed is fine as long as the symbol is not referenced.

### Special behaviours

- **Embed-hash drift gate (FR-8)** -- `bootstrap()` writes the 5-tuple
  `(model_id, revision, content_hash, ndims, schema_v)` into a sidecar
  `_stargraph_meta` table. Subsequent `bootstrap()` calls verify the tuple;
  drift raises `IncompatibleEmbeddingHashError` (force-loud).
- **Single-writer lock (FR-9)** -- every write path
  (`bootstrap` / `upsert` / `delete` / `cleanup_old_versions`) wraps
  `async with _lock_for(self._path)`.
- **`current_version()` / `cleanup_old_versions(older_than_days=7)`** --
  provider extensions outside the Protocol. The version is recorded in
  engine checkpoints alongside `run_id` / `step` for FR-10
  reproducibility; cleanup wraps `AsyncTable.optimize(cleanup_older_than=...)`,
  LanceDB's `VACUUM` analog.
- **Hybrid fusion** -- `mode="hybrid"` always fuses through
  `RRFReranker(k_param=60)` internally. Custom rerankers are wired at
  the `RetrievalNode` level, not inside `LanceDBVectorStore`.

### YAML wiring

```yaml
stores:
  vector: lancedb:./.lance
```

Path is interpreted relative to the `stargraph.yaml` directory.

## Errors raised

| Error | Raised when |
|---|---|
| `IncompatibleEmbeddingHashError` | FR-8 drift gate mismatch on `bootstrap()` re-entry. |
| `EmbeddingModelHashMismatch` | The configured embedder's safetensors sha256 does not match the pinned hash. Bubbles up from the embedder, not the vector store. |
| `MigrationNotSupported` | `migrate(plan)` saw a non-`add_column` op or a non-nullable add. |
| `ValueError` | `mode='vector'` without a vector, vector length != `embedder.ndims`, etc. |
| `StoreError` | Embedder returned the wrong number of vectors. |

See [embeddings.md](embeddings.md) and
[rerankers.md](rerankers.md) for the embedder/reranker surfaces.
