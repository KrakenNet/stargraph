# Stores reference

Per-provider reference for the five Stargraph `Store` Protocols and their
default in-tree providers. The high-level concept page lives at
[Knowledge / Stores](../../knowledge/stores.md); these pages document the
exact Protocol surface, payload models, errors, and YAML wiring shape.

## Catalog

| Kind | Protocol | Default provider | Backed by | Reference |
|---|---|---|---|---|
| Vector | `VectorStore` | `LanceDBVectorStore` | LanceDB (Lance columnar) | [vector.md](vector.md) |
| Graph | `GraphStore` | `RyuGraphStore` | RyuGraph (Kuzu fork) | [graph.md](graph.md) |
| Doc | `DocStore` | `SQLiteDocStore` | SQLite (WAL) | [doc.md](doc.md) |
| Memory | `MemoryStore` | `SQLiteMemoryStore` | SQLite (WAL) | [memory.md](memory.md) |
| Fact | `FactStore` | `SQLiteFactStore` | SQLite (WAL) + `FathomAdapter` | [fact.md](fact.md) |

Two supporting surfaces feed the Vector path:

| Role | Protocol | Default | Reference |
|---|---|---|---|
| Embedder | `Embedding` | `MiniLMEmbedder` | [embeddings.md](embeddings.md) |
| Reranker | `Reranker` | `RRFReranker` | [rerankers.md](rerankers.md) |

## Public surface

```python
from stargraph.stores import (
    # Protocols
    VectorStore, GraphStore, DocStore, MemoryStore, FactStore,
    Embedding, Reranker,
    # Default providers
    LanceDBVectorStore, RyuGraphStore,
    SQLiteDocStore, SQLiteMemoryStore, SQLiteFactStore,
    MiniLMEmbedder, RRFReranker,
    # Value models
    Row, Hit,
    NodeRef, Path, ResultSet,
    Document,
    Episode, ConsolidationRule, MemoryDelta,
    AddDelta, UpdateDelta, DeleteDelta, NoopDelta,
    Fact, FactPattern,
    # Shared lifecycle types
    StoreHealth, MigrationPlan,
    # Cypher portable subset
    Linter,
)
```

`LanceDBVectorStore` and `RyuGraphStore` are loaded lazily through PEP 562
`__getattr__` -- importing `stargraph.stores` does **not** force the
`stargraph[stores]` extra (LanceDB / RyuGraph / pyarrow) to be installed.
Engine subsystems that only need the lightweight Protocol surface stay
free of the heavy wheels.

## Shared lifecycle

Every Protocol exposes the same three methods. Concrete behaviour is
documented per provider, but the contract is uniform:

| Method | Returns | Behaviour |
|---|---|---|
| `bootstrap()` | `None` | Idempotent schema/metadata install. Safe to re-run. |
| `health()` | `StoreHealth` | Snapshot for FR-9 (`fs_type`, `lock_state`, warnings). |
| `migrate(plan)` | `None` | Apply a `MigrationPlan`. v1 supports `add_column` only. |

```python
class StoreHealth(BaseModel):
    ok: bool
    version: int
    fragment_count: int | None = None
    node_count: int | None = None
    embedding_hash: str | None = None
    fs_type: str
    lock_state: Literal["free", "held"]
    warnings: list[str] = Field(default_factory=list)

class MigrationPlan(BaseModel):
    target_version: int
    operations: list[dict[str, object]]
```

!!! warning "v1 migration scope"
    Only `add_column` (nullable) is supported. Type narrows, renames, and
    drops raise `MigrationNotSupported` up-front -- they cannot be applied
    forward-safely on Lance fragments without a rewrite.

## Single-writer-per-path

All embedded providers serialise writes through an in-process
`asyncio.Lock` keyed by resolved store path
(`stargraph.stores._common._lock_for`). LanceDB on local FS, RyuGraph
(Kuzu's documented single-writer model), and SQLite-WAL all assume one
writer per file. `health()` warns when `fs_type` is networked
(`nfs / nfs4 / smb / smbfs / cifs`) -- multi-host file locks are not
reliable.

## YAML wiring shape

Stores are declared at the IR top-level under `stores:`. Compact form
keys by store kind, value is `<provider>:<path>`:

```yaml
stores:
  vector: lancedb:./.lance
  graph:  ryugraph:./.ryu
  doc:    sqlite:./.docs
  memory: sqlite:./.memory
  fact:   sqlite:./.facts
```

The runtime parses each entry into a `StoreRef(name, provider)` which
also derives the `db.{name}:read` / `db.{name}:write` capability strings
used by Bosun's policy gates (FR-19/FR-20).

<!-- TODO: verify expanded long-form `stores:` block once IR mounts richer per-provider config -->

See [knowledge/stores.md](../../knowledge/stores.md) for the
architectural overview and [knowledge/cypher-subset.md](../../knowledge/cypher-subset.md)
for the portable-Cypher rationale.
