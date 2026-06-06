# Cypher Portable Subset

Stargraph's `GraphStore` Protocol speaks Cypher, but only a **portable subset** that
runs unchanged on both **Kuzu** (the v1 default Provider) and **Neo4j 5** (the
documented swap path for operators who outgrow embedded Kuzu). The subset is
enforced by a single linter — `stargraph.stores.cypher.Linter` — that fires before
any query reaches a backend.

This page is the canonical allow / ban list. The implementation is at
[`src/stargraph/stores/cypher.py`](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/stores/cypher.py)
and the rule set is exercised by the FR-12 two-engine gate test suite.

## Why a subset

Kuzu and Neo4j 5 share the same lexical surface but diverge sharply in semantics
once you leave the read-write core. APOC and GDS are Neo4j-only plugins; CSV
loading uses different syntax; subqueries with `IN TRANSACTIONS` are Neo4j-only.
A query that works on Kuzu but fails on Neo4j (or vice versa) is a portability
hazard — and **silent until the operator swaps backends**.

The linter is a single seam: every Cypher string crossing the `GraphStore`
boundary passes through `Linter.check()` first. Reject-loud, run-everywhere.

## Linter contract

```python
from stargraph.stores.cypher import Linter
from stargraph.errors import UnportableCypherError

linter = Linter()

linter.check("MATCH (n:Person) RETURN n.name")              # ok
linter.check("CALL apoc.coll.zip([1,2], [3,4])")            # raises
# UnportableCypherError(rule='apoc-call', match='apoc.', ...)
```

`Linter.check(cypher)` raises `UnportableCypherError` (subclass of `StargraphError`)
on the first ban-list match. The error carries `context['rule']` (the rule slug)
and `context['match']` (the offending substring) for log surfacing.

`Linter.requires_write(cypher)` is a separate keyword scan used by FR-20
capability gating to decide whether the query mutates graph state.

## Allow list (portable)

Anything not matched by the ban list is allowed. The linter is **explicit about
what it rejects**, not what it accepts; the working set in production is:

| Category | Examples | Kuzu | Neo4j 5 |
|---|---|---|---|
| Read clauses | `MATCH`, `OPTIONAL MATCH`, `WHERE`, `RETURN`, `WITH`, `UNION`, `UNWIND`, `ORDER BY`, `SKIP`, `LIMIT`, `DISTINCT` | yes | yes |
| Write clauses | `CREATE`, `MERGE`, `SET`, `DELETE`, `DETACH DELETE`, `REMOVE` | yes | yes |
| Pattern syntax | Node `(n:Label {prop: $param})`, relationship `-[r:REL]->`, anonymous nodes/rels | yes | yes |
| Bounded variable-length | `*1..3`, `*..5`, `*2..` | yes | yes |
| Schema | `CREATE NODE TABLE`, `CREATE REL TABLE` (Kuzu) / `CREATE (n:Label)` + indexes (Neo4j) | yes (DDL via `stargraph.stores.kuzu` migrations) | yes (mapped by Neo4j Provider) |
| Parameters | `$name`, `$ids` (positional or named) | yes | yes |
| Aggregates | `count()`, `collect()`, `sum()`, `avg()`, `min()`, `max()` | yes | yes |
| String fns | `toLower`, `toUpper`, `trim`, `substring`, `replace`, `split` | yes | yes |
| Numeric fns | `abs`, `ceil`, `floor`, `round`, `sqrt`, `rand` | yes | yes |
| List fns | `size`, `head`, `tail`, `range`, `reverse` | yes | yes |
| Predicates | `IS NULL`, `IS NOT NULL`, `IN`, `STARTS WITH`, `ENDS WITH`, `CONTAINS`, `=~` (regex) | yes | yes |
| `CASE` | `CASE WHEN ... THEN ... ELSE ... END` | yes | yes |

Schema DDL is **issued via the Provider's `migrate()` path**, not handed to the
linter directly — the IR migration block translates to backend-specific DDL so
that `CREATE NODE TABLE` (Kuzu) and `CREATE INDEX` (Neo4j) live behind one
seam.

## Ban list (rejected)

Each row is a rule slug exposed via `UnportableCypherError.context['rule']`.
The pattern is the regex in `_BAN_PATTERNS` (case-insensitive unless noted);
matching is non-greedy and tolerates whitespace where Cypher allows it.

| Rule | Pattern | Why rejected | Kuzu | Neo4j 5 |
|---|---|---|---|---|
| `apoc-call` | `apoc.` | APOC is a Neo4j plugin; absent in Kuzu | no | yes (plugin) |
| `gds-call` | `gds.` | Graph Data Science is Neo4j Enterprise; absent in Kuzu | no | yes (plugin) |
| `call-in-transactions` | `CALL { ... } IN TRANSACTIONS` | Neo4j-only; Kuzu rejects | no | yes |
| `load-csv` | `LOAD CSV` | Neo4j syntax; Kuzu uses `COPY FROM` | no | yes |
| `load-from` | `LOAD FROM` | Kuzu syntax for ingest; Neo4j has no equivalent | yes | no |
| `show-functions` | `SHOW FUNCTIONS` | Neo4j-only introspection | no | yes |
| `show-indexes` | `SHOW INDEXES` | Neo4j-only introspection | no | yes |
| `show-constraints` | `SHOW CONSTRAINTS` | Neo4j-only introspection | no | yes |
| `yield-star` | `YIELD *` | Neo4j-only; Kuzu requires explicit yield list | no | yes |
| `shortest-path` | `shortestPath(...)` | Neo4j builtin; Kuzu uses recursive rels | no | yes |
| `dynamic-label` | `:$(...)` | Neo4j 5+ dynamic labels; Kuzu rejects | no | yes |
| `map-projection` | `{.prop, ...}` | Neo4j map projection syntax; Kuzu rejects | no | yes |
| `path-comprehension` | `[(n)-->(m) \| n]` | Neo4j-only path comprehension | no | yes |
| `collect-subquery` | `COLLECT { ... }` | Neo4j 5.18+ subquery; absent in Kuzu | no | yes |
| `varlen-unbounded` | `*` (without bounds) | Unbounded path expansion is unsafe across engines | no | yes (degenerate) |
| `mutating-subquery` | `CALL { ... RETURN ... }` | Subqueries with returns aren't supported in Kuzu's subset | no | yes |

## Two-engine compatibility matrix

The "Kuzu / Neo4j 5" columns above feed a single invariant: **every rule in the
ban list must reject at least one of the two engines, and the allow list must
run unchanged on both**. The FR-12 gate test suite enforces this by:

1. Running each allow-listed example query against an in-process Kuzu instance
   and (when the integration env var is set) a containerized Neo4j 5.
2. Asserting `Linter.check()` raises for every ban-list canary.
3. Asserting that any string that passes `Linter.check()` parses on **both**
   engines without semantic divergence (count/order checked).

If either engine ever shifts behaviour (Kuzu adds `LOAD CSV`, Neo4j removes
`shortestPath`), the linter is the single file to update — and the gate test
catches the drift the next CI run.

## Capability hand-off

The same linter feeds FR-20 capability gating:

```python
linter = Linter()
linter.check(query)                         # portability gate
needs_write = linter.requires_write(query)  # capability gate
required = "db.graph:write" if needs_write else "db.graph:read"
```

`requires_write` is a keyword scan (`CREATE | MERGE | SET | DELETE | REMOVE |
DROP | ALTER | COPY`). False positives are safe (over-request capability);
false negatives would let a write through a read capability and are not.

## Reuse map

| Concern | Reused from | Why |
|---|---|---|
| Error class | `stargraph.errors.UnportableCypherError` | One hierarchy across stack (NFR-4) |
| Capability strings | engine FR-26 capability registry | One vocabulary |
| Backend swap seam | `GraphStore` Protocol | Linter is engine-agnostic by construction |

See [design §3.2](https://github.com/KrakenNet/stargraph/blob/main/specs/stargraph-knowledge/design.md)
for the full Provider implementation notes.
