# `GraphStore`

Property-graph storage contract (FR-1, FR-3, design §3.2). Concrete
in-tree provider: [`RyuGraphStore`](#ryugraphstore).

## Protocol surface

```python
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

@runtime_checkable
class GraphStore(Protocol):
    async def bootstrap(self) -> None: ...
    async def health(self) -> StoreHealth: ...
    async def migrate(self, plan: MigrationPlan) -> None: ...

    async def add_triple(
        self,
        s: NodeRef,
        p: str,
        o: NodeRef,
        *,
        props: Mapping[str, str] | None = None,
    ) -> None: ...

    async def query(
        self,
        cypher: str,
        params: Mapping[str, Any] | None = None,
    ) -> ResultSet: ...

    async def expand(
        self,
        node: NodeRef,
        hops: int = 1,
        *,
        predicates: Sequence[str] | None = None,
    ) -> list[GraphPath]: ...
```

## Lifecycle

| Method | Behaviour |
|---|---|
| `bootstrap()` | Idempotent schema-on-first-write. Installs `Entity` node table + `Rel` edge table (with reserved `t_valid` / `t_invalid` bitemporal columns). |
| `health()` | `StoreHealth` with `node_count` (`Entity` count) and `fragment_count` (edge count). |
| `migrate(plan)` | v1 stub: validation enforced, execution `NotImplementedError` for now. |

## CRUD

### `add_triple(s, p, o, *, props=None)`

Upserts `(s)-[p]->(o)` via parameterised `MERGE` against the `Entity` /
`Rel` schema. Both endpoints are MERGE'd by `id`; the edge is MERGE'd
on `predicate`.

### `query(cypher, params=None) -> ResultSet`

Executes `cypher` after passing it through
[`stargraph.stores.cypher.Linter.check`](#cypher-portable-subset). Returns
a `ResultSet` of column-keyed dicts. Out-of-subset Cypher raises
`UnportableCypherError`.

### `expand(node, hops=1, *, predicates=None) -> list[GraphPath]`

Variable-length walk starting at `node`. Bounds: `0 < hops <= 10`.
Variable-length bounds cannot be parameterised in Cypher, so `hops` is
interpolated as a literal after validation.

!!! warning "Walk vs trail (AC-9.5)"
    Variable-length matches return **walks** -- vertices and edges may
    repeat. RyuGraph always returns walk semantics; Neo4j 5 under the
    same shape returns *trails* (edges unique). Treat row count as
    provider-dependent for any pattern that can re-traverse an edge.

## Value models

### `NodeRef`

Identifier-plus-kind handle. No payload.

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | Node primary key. |
| `kind` | `str` | Distinguishes node tables / labels (`"Entity"` is the v1 default). |

### `GraphPath`

Walk returned by `expand`. `nodes[0]` is the start; `edges[i]` connects
`nodes[i]` to `nodes[i+1]`, so `len(edges) == len(nodes) - 1`. Named
`GraphPath` (re-exported as `Path` from `stargraph.stores`) to avoid
clashing with `pathlib.Path`.

| Field | Type |
|---|---|
| `nodes` | `list[NodeRef]` |
| `edges` | `list[dict[str, Any]]` |

### `ResultSet`

Cypher result envelope.

| Field | Type | Notes |
|---|---|---|
| `rows` | `list[dict[str, Any]]` | Column-keyed; values stay `Any` because Cypher legitimately returns node maps, edge maps, and `nodes(p)` / `rels(p)` lists. |
| `columns` | `list[str]` | Preserves declared `RETURN` order. |

## Cypher portable subset

`stargraph.stores.cypher.Linter` is the single seam every Cypher string
crosses before reaching RyuGraph (or, in future, Neo4j 5). The allow-list
is implicit -- anything not matching a ban pattern passes.

### Ban-list

| Rule | Pattern (case-insensitive) | Reason |
|---|---|---|
| `apoc-call` | `apoc.` | APOC plugin only exists on Neo4j. |
| `gds-call` | `gds.` | Graph Data Science plugin Neo4j-only. |
| `call-in-transactions` | `CALL { ... } IN TRANSACTIONS` | Neo4j-only batched-write idiom. |
| `load-csv` | `LOAD CSV` | Neo4j-specific bulk-loader. |
| `load-from` | `LOAD FROM` | Provider-specific bulk-loader. |
| `show-functions` | `SHOW FUNCTIONS` | Introspection surface differs. |
| `show-indexes` | `SHOW INDEXES` | Introspection surface differs. |
| `show-constraints` | `SHOW CONSTRAINTS` | Introspection surface differs. |
| `yield-star` | `YIELD *` | Provider column projections diverge. |
| `shortest-path` | `shortestPath` | Algorithm semantics differ; use bounded `expand` instead. |
| `dynamic-label` | `:$(...)` | Cypher 5 dynamic labels not in RyuGraph. |
| `map-projection` | `{.field, ...}` | Map-projection syntax not in RyuGraph. |
| `path-comprehension` | `[(...)\|...]` | Path comprehensions not in RyuGraph. |
| `collect-subquery` | `COLLECT { ... }` | Subqueries not in RyuGraph. |
| `varlen-unbounded` | bare `*` after relationship | Variable-length paths must be bounded (`*1..3`). |
| `mutating-subquery` | `CALL { ... RETURN ... }` | Subqueries with RETURN bodies not portable. |

A failed check raises `UnportableCypherError` carrying
`context['rule']` (the rule name) and `context['match']` (the matched
substring).

### Write-keyword scan

`Linter.requires_write(cypher) -> bool` keyword-scans for
`CREATE / MERGE / SET / DELETE / REMOVE / DROP / ALTER / COPY`. Used by
FR-20 capability gating to decide whether a query mutates graph state
(false positives are safe; false negatives would not be).

## `RyuGraphStore`

Default in-tree provider (`stargraph.stores.ryugraph`). RyuGraph is the
community fork of Kuzu (predictable-labs/ryugraph) after Kuzu's GitHub
repo was archived 2025-10-10 following Apple's acquisition of Kuzu Inc.
Python API surface (`Database` / `AsyncConnection` / `QueryResult`)
unchanged across the fork.

### Constructor

```python
from pathlib import Path
from stargraph.stores import RyuGraphStore

store = RyuGraphStore(
    path=Path("./.ryu"),
    read_only=False,
    buffer_pool_size=256 * 1024 * 1024,   # 256 MB
    max_db_size=1024 * 1024 * 1024,        # 1 GB
)
```

| Param | Type | Default | Notes |
|---|---|---|---|
| `path` | `Path` | required | RyuGraph database directory. |
| `read_only` | `bool` | `False` | Open the underlying `ryugraph.Database` read-only. |
| `buffer_pool_size` | `int` | 256 MiB | RyuGraph default is ~80% of RAM; capped for tests. |
| `max_db_size` | `int` | 1 GiB | RyuGraph default is 8 TiB virtual; capped to avoid mmap exhaustion. |

### Dependencies

Optional extra: `stargraph[stores]` (`ryugraph>=25.9.2,<26`). Loaded lazily
through `stargraph.stores.__getattr__`.

### Special behaviours

- **Schema on bootstrap** -- single `Entity(id PRIMARY KEY, kind)` node
  table plus a `Rel(FROM Entity TO Entity, predicate, t_valid, t_invalid)`
  edge table. Bitemporal columns are reserved in v1 (always NULL); future
  schemas will populate them.
- **Linter on every Cypher string** -- `add_triple`, `query`, and
  `expand` all call `Linter.check` before executing.
- **Singleton-per-path registry** -- a module-level `_RYUGRAPH_INSTANCES`
  dict shares one `Database` + `AsyncConnection` across `RyuGraphStore`
  handles pointing at the same resolved path. RyuGraph holds an exclusive
  write lock at open time, so multiple concurrent in-process readers
  only work via this shared connection.
- **Single-writer lock** -- `bootstrap`, `add_triple`, and `bulk_copy`
  serialise through `_lock_for(self._path)`. `query` and `expand` do not
  take the lock (read paths).
- **`bulk_copy(*, entities_csv, edges_csv)`** -- provider extension
  (FR-11, AC-12.4), **not** part of the `GraphStore` Protocol. Wraps
  RyuGraph's native `COPY FROM`. Callers that opt in hold a
  `RyuGraphStore` reference directly; `GraphStore`-typed callers see
  only the portable surface.

### YAML wiring

```yaml
stores:
  graph: ryugraph:./.ryu
```

## Errors raised

| Error | Raised when |
|---|---|
| `UnportableCypherError` | Linter rejects a query (see ban-list). |
| `ValueError` | `expand` called with `hops <= 0` or `hops > 10`. |
| `MigrationNotSupported` | `migrate` saw a non-`add_column` op or a non-nullable add. |
| `NotImplementedError` | `migrate` execution path (validation passes, execution not yet implemented in POC). |
| `StoreError` | Connection accessed before `bootstrap()` was called. |

See [knowledge/cypher-subset.md](../../knowledge/cypher-subset.md)
for the full rationale on the portable subset, and
[`PromoteTriplesToFacts`](fact.md#promotetriplestofacts) for the
graph-to-fact promotion path.
