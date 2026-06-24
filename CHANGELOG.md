# Changelog

## [0.5.0](https://github.com/KrakenNet/stargraph/compare/v0.4.0...v0.5.0) (2026-06-24)


### Features

* make the repo AI-development-friendly (DOX docs, examples, errors, context dump) ([8d3a61e](https://github.com/KrakenNet/stargraph/commit/8d3a61e3f2e7a08b968c886906faf04ea15fa58e))
* **nodesmith:** self-improving graph that builds Stargraph nodes ([680f9ef](https://github.com/KrakenNet/stargraph/commit/680f9ef5d16bad191ccc9db47bdc711df87b109e))
* **nodesmith:** trainset curator — seeds, CLI, Textual TUI, edit-to-gold, doctor ([0f3a4e5](https://github.com/KrakenNet/stargraph/commit/0f3a4e5c3931f20b07b81de8545874963b1f06b7))
* **nodesmith:** TUI is the full console — Generate / Curate / Doctor / Stats ([f1e223a](https://github.com/KrakenNet/stargraph/commit/f1e223a95faa1d4017301adba9c9b9ed328d183f))
* **skills:** five reference skill bundles + a plugin example ([66f5ece](https://github.com/KrakenNet/stargraph/commit/66f5ece3983413f8c11ab5fd5f677cc4682f6a40))
* **skills:** five reference skill bundles + a plugin example ([0db9898](https://github.com/KrakenNet/stargraph/commit/0db9898c7ef5fc747179ab0b99c1ff8c1b7d4cd4))
* **smith:** generalize nodesmith into the full smith family ([8fb75f6](https://github.com/KrakenNet/stargraph/commit/8fb75f61424a1ebb7e45ad7d38913531f4566657))
* **smith:** generalize nodesmith into the full smith family ([21b723e](https://github.com/KrakenNet/stargraph/commit/21b723e3149f64a227942fd58062ade06c92a7a7))
* **soc-triage:** port demo from pre-rename branch to stargraph main ([296e498](https://github.com/KrakenNet/stargraph/commit/296e49864af8bf150e9f8a73b4552a0d4f4323d4))


### Bug Fixes

* **meta:** repair dead docs/site URLs, enrich keywords/classifiers, add llms.txt + LangGraph positioning ([#99](https://github.com/KrakenNet/stargraph/issues/99)) ([e7fd403](https://github.com/KrakenNet/stargraph/commit/e7fd403dcc269322bd2b1dfe993c5fb891559b60))
* **release:** commit missing .release-please-manifest.json (was gitignored) ([#100](https://github.com/KrakenNet/stargraph/issues/100)) ([a7d3db3](https://github.com/KrakenNet/stargraph/commit/a7d3db3aebf65404c9f5565759bd38c348597175))
* **runtime:** resolve four run-lifecycle bugs ([#65](https://github.com/KrakenNet/stargraph/issues/65), [#67](https://github.com/KrakenNet/stargraph/issues/67), [#68](https://github.com/KrakenNet/stargraph/issues/68), [#81](https://github.com/KrakenNet/stargraph/issues/81)) ([9483619](https://github.com/KrakenNet/stargraph/commit/9483619c3ea6f4e8fa5b79c9879fdfac7d36a25d))
* **runtime:** resolve four run-lifecycle bugs ([#65](https://github.com/KrakenNet/stargraph/issues/65), [#67](https://github.com/KrakenNet/stargraph/issues/67), [#68](https://github.com/KrakenNet/stargraph/issues/68), [#81](https://github.com/KrakenNet/stargraph/issues/81)) ([2e97f4f](https://github.com/KrakenNet/stargraph/commit/2e97f4f7316871dc15e37ebca24e910a80c78b4c))
* **serve:** resolve botched main-merge in api.py/scheduler.py (PR [#84](https://github.com/KrakenNet/stargraph/issues/84) CI) ([d705f8a](https://github.com/KrakenNet/stargraph/commit/d705f8acb0d9e4256fad46b8623ec73dda96c3ed))


### Documentation

* flesh out Getting Started; bump stale __version__ to 0.4.0 ([05cf2e7](https://github.com/KrakenNet/stargraph/commit/05cf2e7b6a0d75ba6c8ba65172dc8077628695c3))
* flesh out Getting Started; bump stale __version__ to 0.4.0 ([114fad3](https://github.com/KrakenNet/stargraph/commit/114fad36515ef8f8f828b6f33c3123dc0561e942))

## [Unreleased]

### Changed (BREAKING — harbor renamed to stargraph)
- Project renamed wholesale: package `src/harbor` → `src/stargraph`,
  CLI console script `harbor` → `stargraph`, entry-point groups
  `harbor.*` → `stargraph.*`, env vars `HARBOR_*` → `STARGRAPH_*`,
  config conventions `harbor.yaml` → `stargraph.yaml` and `~/.harbor`
  → `~/.stargraph`, repo `KrakenNet/harbor` → `KrakenNet/stargraph`
  (GitHub redirect active). Clean break, no compat shims: old
  run/checkpoint state hashed under `harbor.*` module paths will not
  replay or hash-verify.

### Changed (CI pipeline green campaign)
- Typing-only pyright strict cleanup across `src/stargraph` and `tests`
  (527 errors → 0), including annotation-only edits in
  `src/stargraph/ir/_validate.py`. No IR schema or runtime behavior
  changes.
- Knowledge CI job: coverage flag changed from
  `--cov=stargraph.stores --cov=stargraph.skills` to `--cov=stargraph` — dotted
  `source_pkgs` triggered a re-entrant numpy import under coverage's
  lazy `find_spec`, crashing with "cannot load module more than once
  per process".
- Integration skill tests now run under a canned-JSON standin LM
  (`tests/fixtures/lm_stub.py` + opt-in `standin_lm` fixture) instead
  of requiring a live model.
- Regenerated `docs/reference/openapi.json` to match the current serve
  surface.

The stargraph-knowledge surface — Stores, Skills, retrieval, memory, and
consolidation built on top of stargraph-engine.

### Changed (graph-store backend)
- Replaced `kuzu==0.11.3` with `ryugraph>=25.9.2,<26` in the `stores`
  optional-dependency group. RyuGraph is the community fork of Kuzu
  (predictable-labs/ryugraph) after Kuzu's GitHub repo was archived
  2025-10-10 following Apple's acquisition of Kuzu Inc. The Python API
  surface (`Database` / `AsyncConnection` / `QueryResult`) is unchanged
  across the fork, so the swap was a one-module rename behind the
  `GraphStore` Protocol — `stargraph.stores.kuzu.KuzuGraphStore` is now
  `stargraph.stores.ryugraph.RyuGraphStore`. Provenance source URIs
  emitted by `PromoteTriplesToFacts` change from `kuzu:<path>` to
  `ryugraph:<path>` (FR-11, AC-12.1).

### Added (Plan 1.5 — Shipwright runnable via `stargraph run`)
- IR `state_class: str | None` field — declare a Pydantic `BaseModel`
  subclass via `module.path:ClassName` instead of the primitive
  `state_schema: dict[str, str]` placeholder. Mutually exclusive with a
  non-empty `state_schema`.
- `module.path:ClassName` resolution for `NodeSpec.kind` — short kinds
  (`echo`/`halt`/`dspy`) still match the static factory table; any kind
  containing `:` is imported via `importlib` and validated as a
  `NodeBase` subclass.
- `stargraph run --lm-url/--lm-model/--lm-key/--lm-timeout` flags —
  `stargraph run` calls `dspy.configure(lm=dspy.LM(...))` before driving
  the graph when `--lm-url` and `--lm-model` are both set.
- `--inputs key=value` honors `state_class` by walking the resolved
  BaseModel's `model_fields`.

### Changed
- Bumped `nautilus-rkm` pin 0.1.3 → 0.1.4 (0.1.3 shipped Py2 except
  syntax that crashed import on Python 3.13). Mirrors three dropped
  `BrokerResponse` fields (`cap_breached`, `fact_set_hash`,
  `source_session_signatures`) and the dropped `fact_set_hash` kwarg
  on `Broker.arequest`. Removes the local sibling-path override now
  that nautilus is on PyPI.
- `SpecSlot.confidence`: `float = 1.0` → `int = 100` (percent). FR-4
  forbids floats anywhere in the structural-hash payload, including
  `model_json_schema` defaults; the field's old default tripped the
  hash the moment `State` landed under `state_class`.

### Added
- Five Store Protocols (`stargraph.stores`): `VectorStore`, `GraphStore`,
  `DocStore`, `MemoryStore`, `FactStore` — each with `bootstrap`, `health`,
  `migrate` lifecycle methods and capability strings — FR-1 through FR-5.
- Three default embeddable backends: LanceDB (`VectorStore`), Kuzu
  (`GraphStore`), and the SQLite trio (`DocStore`/`MemoryStore`/`FactStore`)
  with single-writer `asyncio.Lock` per store path — FR-6, FR-7, FR-8.
- Embedding-hash drift gate: 5-tuple `(model_id, revision, content_hash,
  ndims, schema_v)` written to LanceDB table metadata at `bootstrap()`,
  verified at every re-entry; `IncompatibleEmbeddingHashError` mirrors the
  engine's `IncompatibleModelHashError` — FR-9.
- `Skill` base class plus three reference skills (`rag`, `autoresearch`,
  `wiki`) shipped in-tree, composed via the agent-as-subgraph pattern with
  declared-output-channels-only contracts — FR-12, FR-13, FR-36.
- `ReActSkill` with replay determinism: tool stubs matched by
  `(node_name, step_id)` (not `(tool_name, args)`) so LLM-output drift cannot
  desynchronize replay; `must_stub` LLM nodes guarantee byte-identical
  re-execution — FR-19.
- `RetrievalNode` (`stargraph.nodes.retrieval`): parallel fan-out across stores
  with `asyncio.TaskGroup` and Reciprocal Rank Fusion (RRF) merge; per-store
  embedder is fixed (no cross-store re-embedding) — FR-22, FR-23.
- `MemoryWriteNode`: 3-tuple episode write `(observation, thought, action)`
  with provenance enforcement via the Fathom adapter — FR-25.
- KG promotion (one-way): triples land in `GraphStore`, salient triples are
  promoted to `FactStore` with provenance preserved; retraction is a separate
  rule (asymmetric per Graphiti) — FR-27.
- Mem0-style typed-delta consolidation (`MemoryDelta` Pydantic model with
  required provenance fields) and Park 2023 salience scoring (recency +
  relevance + importance), with `relevance` and `importance` zero in v1 —
  FR-28, FR-31.
- `FathomAdapter` provenance enforcement: every consolidated fact carries
  `(source_episode_id, model_id, prompt_hash, ts)` — FR-28.
- Plugin manifest factory and namespace conflict detection: skills and stores
  registered via `pluggy` with loud-fail on namespace collision — FR-34,
  FR-35.

### stargraph-serve-and-bosun

The serve / scheduler / triggers / Bosun packs / Nautilus / HITL / artifacts
surface — single-process FastAPI HTTP+WebSocket runtime built on top of
stargraph-engine + stargraph-knowledge, plus the in-tree `stargraph.bosun.*`
reference packs and the Nautilus broker integration.

#### Added
- `stargraph serve` HTTP+WebSocket API (FastAPI 0.115+, OpenAPI 3.1) —
  `POST /v1/runs`, `GET /v1/runs/{id}`, `POST /v1/runs/{id}/{resume,cancel,pause,respond,counterfactual}`,
  `GET /v1/graphs`, `GET /v1/registry/{tools|skills|stores}`,
  `GET /v1/runs/{id}/artifacts`, `GET /v1/artifacts/{artifact_id}`, and
  WebSocket `/v1/runs/{id}/stream` — FR-50 through FR-71.
- Scheduler with cron-trigger DST-safe (cronsim) + idempotency dedup
  (BLAKE3-keyed `dedup_key`) + per-graph `anyio.CapacityLimiter` honoring
  IR `concurrency` — FR-43, FR-46, FR-47.
- 3 trigger plugins (`manual`, `cron`, `webhook`) registered under the
  `stargraph.triggers` entry-point group; emit `TriggerEvent` → scheduler queue
   — FR-40, FR-41, FR-42.
- 4 in-tree Bosun reference packs (`stargraph.bosun.budgets@1.0`, `audit@1.0`,
  `safety_pii@1.0`, `retries@1.0`) signed with Ed25519; cleared deployments
  verify signatures on load (Fathom attestation pattern) — FR-58 through
  FR-65.
- Nautilus integration via `stargraph.nodes.nautilus.BrokerNode` +
  `stargraph.tools.nautilus.broker_request` + lifespan-singleton `Broker` —
  FR-66, FR-67.
- HITL primitives: `InterruptAction` IR variant,
  `stargraph.nodes.interrupt.InterruptNode`, `WaitingForInputEvent` +
  `InterruptTimeoutEvent` event variants, `POST /v1/runs/{id}/respond` +
  `GraphRun.respond()` async method + `stargraph respond` CLI — FR-83 through
  FR-87.
- `stargraph.artifacts` namespace: `ArtifactStore` Protocol +
  `FilesystemArtifactStore` (BLAKE3 content-addressable, POSIX-local-only) +
  `WriteArtifactNode` built-in + `ArtifactWrittenEvent` typed variant —
  FR-90 through FR-95.
- Deployment profiles (`oss-default` + `cleared`) with default-deny
  capability enforcement; cleared profile requires TLS, audit, and
  signed packs — FR-72 through FR-75.
- STRIDE threat model (`docs/security/threat-model.md`) + sign-off rubric
  + air-gap deployment guide (`docs/deployment/air-gap.md`).
- 4 new CLI subcommands: `stargraph inspect <run_id>` (timeline + state-at-step
  + fact diffs), `stargraph replay` (drives counterfactual API), `stargraph respond`
  (HITL response posting), `stargraph serve` (production-ready uvicorn entry).

#### Changed
- Extended OpenAPI 3.1 spec coverage; mkdocs nav with 12 Serve topic pages.
- Pack signing pubkey rotated; trust-store TOFU + static allow-list
  (capability-tag pattern from Fathom).
- `runtime/run.GraphRun` now supports mid-run cancel/pause/resume/respond
  (engine TODO at `src/stargraph/graph/run.py:329` resolved) — FR-76 to FR-82.

## [v0.2.0] - 2026-04-29

The stargraph-engine release (v0.2.0). Implements the runtime, checkpoint, replay, audit,
adapters, ML, and node subsystems on top of the stargraph-foundation contracts,
plus the foundation surface extensions required to support them (FR-33).

### Foundation surface (engine extension)
- `ToolSpec.side_effects` upgraded from `str` to a closed `SideEffect` enum
  (`none | read | write | external`) — FR-33, FR-21.
- `ToolSpec.replay_policy` field added (`must-stub | fail-loud`) governing how
  side-effecting tools behave during replay — FR-33, FR-21, NFR-8.
- `@tool` decorator (`stargraph.registry.tool`) for declarative tool registration
  with stable-ID and signature validation — FR-26, FR-33.
- Stable-ID validators for `node_id`, `tool_id`, and `run_id` enforcing the
  ASCII slug grammar required by graph hashing and resume — FR-33, FR-4.

### Engine subsystems
- `runtime/parallel` — `asyncio.TaskGroup` + `anyio` cancel scopes for
  fan-out/fan-in nodes, with structured cancellation — FR-10, FR-13.
- `runtime/bus` — bounded `anyio.MemoryObjectStream` event bus with
  back-pressure and the `stargraph.transition` / `stargraph.evidence` event
  vocabulary — FR-14, FR-15.
- `runtime/merge` — Mirror field-level merge reducers with deterministic
  last-write semantics and `race`/`any` rejection — FR-11, FR-12.
- `runtime/tool_exec` — tool execution path enforcing replay-policy gating,
  provenance assertion, and audit emission — FR-21, FR-24, NFR-8.
- `checkpoint/sqlite` — `aiosqlite` checkpointer in WAL mode with the
  `Checkpointer` Protocol — FR-16, FR-17.
- `checkpoint/postgres` — `asyncpg` checkpointer, pgbouncer-safe (no
  prepared-statement reuse across connections) — FR-18.
- `checkpoint/clips_facts` — round-trip persistence of CLIPS provenance facts
  alongside graph state — FR-16, NFR-4.
- `adapters/dspy` — `DSPyNode` adapter with the FR-6 loud-fail guard for
  unconfigured/missing DSPy modules — FR-5, FR-6.
- `adapters/mcp` — Model Context Protocol bind path for tool registration
  via `stargraph.registry` — FR-25.
- `replay` — cassettes, determinism contract, counterfactual runs (new
  derived `graph_hash`, fresh `run_id`), history inspection, and compare —
  FR-19, FR-20, FR-27, FR-28, FR-29, NFR-2.
- `ml` — ONNX/sklearn loaders, tiny model registry, and `MLNode` with
  explicit-provider session reuse — FR-30, FR-31, FR-32.
- `nodes` — `DSPyNode`, `MLNode`, and `SubGraphNode` node primitives wired
  into the runtime loop — FR-1, FR-5, FR-7, FR-30.
- `audit` — JSONL audit-log sink with optional Ed25519 signing — FR-22.

### CLI
- `stargraph run` validate + execute + JSONL log + `--inspect` — FR-8.
- `stargraph.simulate(ir, fixtures)` programmatic entrypoint — FR-9.
- `stargraph counterfactual` CLI for replay mutations — FR-29.

### Cross-reference
This release closes FR-1 through FR-33 (all 33 functional requirements from
`specs/stargraph-engine/requirements.md`), plus NFR-1 through NFR-10. The
foundation extensions in FR-33 are a prerequisite for FR-21/FR-24/FR-26 and
were landed first.

## [0.1.0] - 2026-04-26

### Added
- Initial release: stargraph-foundation contracts.
