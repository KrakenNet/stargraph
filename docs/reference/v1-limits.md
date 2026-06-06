# v1 limits — stub-vs-real boundaries

Stargraph v1 ships a working core (Phase 0–5) plus a handful of intentionally
narrow stubs to keep the surface honest while later phases land. This page
catalogs every place where what's documented in design and what's wired
in code diverge, so callers don't trip on them.

> Each row links to the source so the truth is one click away. When a
> Phase-2/3 task lands, the row should be deleted (not edited) — the page
> exists to disappear over time.

## Runtime

### `POST /v1/runs` returns a synthetic run id

The serve API's `POST /v1/runs` handler currently returns
`run_id="poc-{graph_id}"` rather than persisting a real run record. It is
a stub for graph-id round-tripping; the route does not invoke the engine
loop.

- Source: [`src/stargraph/serve/api.py:705`](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/serve/api.py#L705)
- Workaround: drive runs via `stargraph run --checkpoint <db>` and read run
  state from the SQLite checkpointer directly. The CLI path is real;
  only the HTTP convenience handler is stubbed.

### Manual trigger run id is synthetic

`stargraph.triggers.manual.enqueue` returns `f"poc-{graph_id}"` for the same
reason as the serve handler — both share the placeholder.

- Source: [`src/stargraph/triggers/manual.py:129`](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/triggers/manual.py#L129)

### `awaiting-input` resume requires a cold restart

When a graph hits an `InterruptAction`, the loop emits
`WaitingForInputEvent`, transitions `state="awaiting-input"`, and
returns. `GraphRun.respond()` flips state back to `"running"` and
asserts the response as a Fathom evidence fact, but the loop has
already exited — it does not poll for an in-process transition.

The supported resume path is cold restart via
`GraphRun.resume(checkpoint)` from a new process. Warm in-process
resume is **not** implemented in v1.

- Source: [`src/stargraph/graph/loop.py:81-94`](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/graph/loop.py#L81-L94) (module docstring)
- Supported flow: stop the process when you see
  `WaitingForInputEvent` → call `GraphRun.respond()` to record the
  response → restart with `stargraph run --resume <checkpoint>`.

## Storage and replay

### `WriteArtifactNode` replay paths raise

The node-level cassette layer is not built. `WriteArtifactNode` has two
`replay_policy` modes (`must_stub`, `fail_loud`); both raise
`ArtifactStoreError` on `ctx.is_replay=True` because no upstream
cassette is wired to surface a recorded `ArtifactRef`. The replay
contract for write-side-effect nodes is currently false on any graph
that uses artifact writes.

- Source: [`src/stargraph/nodes/artifacts/write_artifact_node.py:115-124`](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/nodes/artifacts/write_artifact_node.py#L115-L124)
- Workaround: don't replay graphs that contain `WriteArtifactNode`
  (or set `replay_policy="fail_loud"` to make the wiring gap loud).

### ~~Cypher portable-subset linter is regex, not AST~~ (resolved)

The linter at `stargraph.stores.cypher` now parses every Cypher string
via graphglot's neo4j dialect AST. Parse errors translate to
`UnportableCypherError`; AST walkers reject banned procedure
namespaces (`apoc`/`gds`/`db`), unbounded variable-length paths,
`YIELD *`, path comprehension, and `CALL { ... }` subqueries.
Whitespace / casing bypasses can no longer fool the linter — the
procedure name is normalized to its canonical dotted form regardless
of source tokenization.

Note: the portable subset is now narrower than the prior regex
allowed. COUNT subqueries (`COUNT { MATCH ... RETURN ... }`) are
rejected because RyuGraph does not execute them; this matches the
runtime contract.

## Plugins and entry-points

### `stargraph.checkpointers` entry-point group is unverified

The how-to guide at [`how-to/checkpointer.md`](../how-to/checkpointer.md)
references a `stargraph.checkpointers` entry-point group, but no consumer
in `src/stargraph/` reads from that group today. Distribution path for
third-party checkpointer plugins is undefined in v1.x.

- Implication: write a custom checkpointer by passing a
  `Checkpointer` instance into `GraphRun(...)` directly. Don't ship it
  as a wheel + entry-point until the group lands.

### ~~MCP adapter has no entry-point group~~ (resolved)

The `stargraph.mcp_adapters` entry-point group now exists. Plugins
register a `register_mcp_adapters() -> list[MCPAdapterSpec]` hookimpl;
serve / engine wiring drives
[`stargraph.adapters.mcp.collect_mcp_adapters(pm)`](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/adapters/mcp.py)
to aggregate them. See
[`how-to/add-mcp-server.md`](../how-to/add-mcp-server.md) for the
plugin path; imperative wiring still works for ad-hoc cases.

### `stargraph.brokers` group does not exist

References to a `stargraph.brokers` group in design notes are aspirational.
The Nautilus broker is the only broker integration today and it lives
in `src/stargraph/tools/nautilus/`.

## IR

### No `EdgeSpec` / `EdgeRef` model

Routing in v1 is entirely via `RuleSpec.then` actions plus implicit
fall-through node order. Concept docs sometimes use the word "edge";
that is a mental model, not an IR-level type. There is no edge model
to look up or list.

- Implication: a graph's transitions are derived, not declared. Read
  them from `RuleSpec.then` (Fathom `goto`/`halt`/`parallel` actions)
  and from the `nodes` ordering for fall-through.

### `stargraph.ir._validate` runs four stages

The IR validator runs four passes (schema, type, capability, rule
integrity). Two checks live elsewhere:

- Namespace conflict detection — runs in the plugin loader, not IR
  validate time.
- Cypher portable-subset linting — fires at provider call sites, not
  IR validate time.

Design notes hint at IR-level placement; until that consolidation lands,
running `IRDocument.model_validate(...)` is not a complete preflight.

### ~~`stargraph.plugin.hookspecs` has `Any` placeholders~~ (resolved)

Phase-2 backfill landed. `PluginManager`, `ToolCall`, `ToolResult`,
`StoreSpec`, `PackSpec` now resolve to real types in
[`stargraph.plugin.types`](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/plugin/types.py)
and the hookspec module imports them. The catalog at
[`reference/plugin-manifest.md`](plugin-manifest.md) is authoritative.

`Route` remains `Any` until the serve module's FastAPI dependency is
unconditional; the alias is centralised so the tightening is a
one-line change.

## Optional extras

### `CrossEncoderReranker` is a stub

The `stargraph.rerankers` entry-point group ships no concrete reranker
plugins. `RetrievalQuery(mode="hybrid")` defaults to RRF (sound, no
reranker required). The cross-encoder claim in design is not currently
backed by code.

- Source: [`src/stargraph/stores/rerankers.py`](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/stores/rerankers.py)
- Implication: stick with `mode="hybrid"` (RRF) or `mode="vector"` until
  a reranker plugin ships.

## Detection rails (not stubs, but worth knowing)

### DSPy fallback needle is a verbatim string match

`stargraph.adapters.dspy.FALLBACK_NEEDLE` is the literal warning text
DSPy emits when JSONAdapter fallback fires. The CI canary at
[`tests/integration/test_dspy_loud_fallback.py::test_fallback_needle_present_in_installed_dspy`](https://github.com/KrakenNet/stargraph/blob/main/tests/integration/test_dspy_loud_fallback.py)
asserts the needle is still present in the installed `dspy.adapters`
package. If a DSPy patch-bump rewords the warning the canary fails
loudly with a "needle drifted" message; update `FALLBACK_NEEDLE` and
re-test.

- Source: [`src/stargraph/adapters/dspy.py:47`](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/adapters/dspy.py#L47)

## What is real

Everything not on this page. The engine loop, Fathom integration,
checkpointer + replay (for read-side-effect nodes and tool calls),
the Bosun signing chain, JSONL audit, capability gate, parallel
fan-out, HITL with timeout/`on_timeout`, cron + manual + webhook
triggers, and the four shipped reference skills (`rag`,
`autoresearch`, `wiki`, plus the Shipwright bundle) are wired and
tested.
