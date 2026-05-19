## High level - "shipping" the product

- Is streaming supported?
- Run TUI
- Serve TUI
- Finish shipwrite
- Finish code-graph
- code-graph/shipwrite TUI
- Implement graph knowledge graphs
- Custom nodes
- Provider nodes
- Store nodes
- Rules, Skills, Plugins registry
- Integrations:
  - LangGraph + import/export to
  - CrewAI + import/export to
  - Agno + import/export to
  - Identify other integrations that would be useful.
- Add default agents, tools, skills, packs, plugins

## Interactive `harbor run` polish (branch: cli/interactive-run, after commit c091bc8)

Backup for scheduled job df3d9088 (Tue 2026-05-05 9:17am local, session-only — may not survive restart).

- [x] Phantom empty step line at end of run. `_progress.py:_on_transition` opens a new `_NodeInflight` for the synthetic `__end__` sentinel. Filter out `to_node in _SENTINELS` before opening.
- [x] `done in 0ms` in summary. `_summary.py` derives duration from `last_step_at - started_at`, but both are stamped at run-end on fast graphs. Capture `ResultEvent.run_duration_ms` in `ProgressPrinter` and pass to `SummaryRenderer.render(duration_ms_override=...)`.
- [x] `inspect: harbor inspect <ckpt> --run-id <uuid>` line wraps mid-UUID. Print on a continuation line: `\n  inspect:\n    harbor inspect <ckpt> --run-id <uuid>`.

Smoke test after fixes: `rm -rf /tmp/ck && uv run harbor run tests/fixtures/sample-graph.yaml --checkpoint /tmp/ck.sqlite` — no phantom line, real duration, inspect on its own line.

## Findings from docs build (2026-05-04)

Surfaced while writing reference / how-to / tutorial pages against the source tree. Grouped by severity.

### Code / API gaps (blocking docs accuracy)

- [x] **No `EdgeSpec` / `EdgeRef` model in IR.** ~~Routing is entirely via `RuleSpec.then` actions plus implicit fall-through node order. Concept docs and design language assume edges exist. Either rename "edges" → "transitions" everywhere or add the model.~~ Resolved (docs path): `concepts/ir.md` rewritten — describes IR as "nodes, routing rules, state, policy" + explicit terminology note that "edges" is a mental model only; `reference/v1-limits.md` documents the no-EdgeSpec boundary. Adding the model is a future Phase decision; docs no longer claim it exists.
- [x] **`harbor.plugin.hookspecs` still has `Any` placeholders** ~~for `PluginManager`, `ToolCall`, `ToolResult`, `StoreSpec`, `PackSpec`. Phase-2 TODO inline. Hookspec reference doc cites `Any`, so plugin authors get no contract.~~ Resolved: created `harbor.plugin.types` shared module with concrete types (PluginManager=pluggy alias, ToolCall=frozen dataclass, ToolResult re-export, StoreSpec re-export, PackSpec=new dataclass); hookspecs.py imports from it; reference/plugin-manifest.md rewritten with real catalog + type contract section.
- [x] **`harbor.ir._validate` only runs 4 stages.** ~~Namespace conflict detection lives in plugin loader, not IR validator. Cypher linting fires at provider call sites, not IR time. Design hints suggest IR-level — gap.~~ Resolved: extended `harbor.ir._validate` with stage 5 (`_detect_within_ir_duplicates` — flags duplicate ids within nodes/rules/governance/tools/skills lists and duplicate names within stores; cross-namespace collisions allowed) and stage 6 (`_lint_cypher_in_node_configs` — walks each NodeSpec.config for keys matching `cypher` or `*_cypher` and runs the canonical `harbor.stores.cypher.Linter` against string values, surfacing parse errors and unportable-subset rejections as structured `ValidationError` rows). Linter is lazily imported so YAML-only IRs don't pay graphglot's import cost. 8 new unit tests in `tests/unit/test_ir_validate.py`.
- [x] **`WriteArtifactNode` replay paths both raise.** ~~Cassette layer for nodes is not built. Replay claim is currently false for any graph using artifact writes.~~ Resolved: added per-node `NodeCassette` Protocol + `InMemoryNodeCassette` impl in `harbor.replay.cassettes` (extensible — any write-side-effect node opts in via `ctx.node_cassette` getattr). `WriteArtifactNode.execute` now records the `ArtifactRef` payload on live runs and reads it back on replay; misses still raise loudly per `replay_policy`. `dispatch_node` stamps `run.node_id` before the node body and clears after; `GraphRun` carries `node_cassette: Any = None` and `node_id: str = ""`. 4 unit tests cover live record / replay-hit / replay-miss / state round-trip.
- [x] **`POST /v1/runs` returns synthetic `poc-{graph_id}` run id.** ~~Stub. `serve-and-replay.md` had to redirect users to `harbor run --checkpoint <db>` for real runs. Serve API surface is half-real.~~ Resolved: `Scheduler.enqueue` now returns `EnqueueHandle(run_id, future)` so the route hands back the canonical Scheduler-derived id synchronously; new `Scheduler.set_deps()` injects the lifespan deps container, and `_run_one` resolves `deps["graphs"][graph_id]` to a `Graph`, builds a `GraphRun` with the wired checkpointer + per-graph node registry, registers handle in `deps["runs"]` + `EventBroadcaster` in `deps["broadcasters"]`, drives `run.start()` to terminal state. Synthetic-summary fallback preserved when no graph is registered (unit tests). `cli/serve.py` lifespan calls `set_deps`. `tests/integration/serve/test_post_runs_real_engine.py` covers the live path. `ManualTrigger.enqueue` returns canonical id; integration-test stub mirrors new return shape.
- [x] **`harbor.checkpointers` entry-point group existence unverified.** ~~Checkpointer plugin distribution path unclear in v1.x. `how-to/checkpointer.md` flagged with TODO.~~ Resolved: confirmed no group exists (no entries in pyproject, no consumer in code); rewrote `how-to/checkpointer.md` with imperative wiring + Protocol contract + explicit "no entry-point group in v1" note; cross-link to `reference/v1-limits.md`.
- [x] **MCP adapter has no entry-point group.** ~~Library-only. `how-to/add-mcp-server.md` is wire-it-yourself, not pluggable.~~ Resolved: added `harbor.mcp_adapters` group (pyproject + loader.GROUPS), `MCPAdapterSpec` in `harbor.plugin.types`, `register_mcp_adapters()` hookspec, `collect_mcp_adapters(pm)` aggregator helper; how-to documents both pluggable + imperative paths; integration test asserts hookimpl aggregation.

### Inconsistencies

- [x] **Duplicate air-gap docs.** ~~`docs/knowledge/air-gap.md` and `docs/guides/air-gap-deployment.md` both exist with overlapping content and two nav entries. Pick one canonical home.~~ Resolved: kept both (distinct concerns — model staging vs operator playbook); nav titles disambiguated.
- [x] **`docs/validation/cve-pipeline-report.md` exists but is not in nav.** ~~Triggers a strict-build notice. Either add to nav or exclude via mkdocs glob.~~ Resolved: added under Reference > Validation reports.
- [x] **`docs/reference/python/index.md` is a TODO stub** ~~despite mkdocstrings being configured in `mkdocs.yml`. Wire `:::` directives or remove the nav entry.~~ Resolved: removed stub + nav entry; landing-page link redirected to `reference/ir-schema.md`. Re-add when public API stabilizes.
- [x] **Reference-skill catalog mismatch (FR-33 / FR-34).** ~~`autoresearch` and `wiki` referenced in design, but `src/harbor/skills/refs/` only ships `rag`.~~ Stale — `autoresearch.py` (212 lines) and `wiki.py` (178 lines) both ship in `src/harbor/skills/refs/`.

### Fragility

- [x] **DSPy `FALLBACK_NEEDLE` is a verbatim string match** against upstream warning text. Any DSPy patch-bump that rewords it = silent fallback returns. ~~Add a CI canary that asserts the needle still matches the latest DSPy release.~~ Resolved: `tests/integration/test_dspy_loud_fallback.py::test_fallback_needle_present_in_installed_dspy` scans `dspy.adapters` package source for the verbatim needle; loud-fail message points operators at `FALLBACK_NEEDLE` to update.
- [x] **Cypher portable-subset linter is regex ban-list, not AST.** ~~Bypassable with whitespace gymnastics. Phase-2 AST replacement should land before any prod claim.~~ Resolved: rewrote `harbor.stores.cypher.Linter` to parse via graphglot's neo4j dialect (Apache 2.0, 100% openCypher TCK). Parse errors translate to `UnportableCypherError`; AST walkers reject banned procedure namespaces (`apoc`/`gds`/`db`) in both `NamedProcedureCall` and `Anonymous` (function-call form) nodes, unbounded variable-length paths, `YIELD *`, path comprehension, and `CALL { ... }` subqueries. `requires_write` walks for `CreateClause`/`MergeClause`/`SetStatement`/`DeleteStatement`/`RemoveStatement`. 39 cypher tests + property test pass; ~~regex bypass via whitespace eliminated~~. Scope shift: portable subset is now graphglot's neo4j-2025+ accept set — narrower than the prior regex (e.g. COUNT subqueries now rejected since RyuGraph cannot execute them); test corpus updated to reflect.
- [x] **`CrossEncoderReranker` is a stub.** ~~`harbor.rerankers` entry-point group ships no concrete reranker plugins. `mode="hybrid"` defaults to RRF (fine), but the cross-encoder claim in design isn't backed by code.~~ Resolved: replaced the `NotImplementedError` stub with a real sentence-transformers `CrossEncoder`-backed implementation. Lazy model load on first `fuse()` (loud `HarborRuntimeError` if `skills-rag` extra missing); inference runs in `asyncio.to_thread`. Document text resolved from `metadata["text" | "content" | "body" | "passage" | "chunk"]` with `hit.id` fallback. Hits deduped across per-store lists; first-seen metadata retained. `Reranker` Protocol extended with optional `query` kwarg (RRF ignores; cross-encoder requires non-empty — raises if missing). `RetrievalNode.execute` now forwards `query` through `fuse()`. Registered as `cross-encoder` under `harbor.rerankers` entry-point group in pyproject. 9 new unit tests in `tests/unit/test_cross_encoder_reranker.py` use a fake encoder so no model download is needed.

### Suggestions (docs / DX)

- [x] **Add a "v1 limits" page** ~~under Reference or Concepts listing exact stub-vs-real boundaries (POC run id, no cassette, regex Cypher linter, no `harbor.brokers` group, etc.) so users don't trip on them.~~ Resolved: `docs/reference/v1-limits.md` consolidates 11 stub-vs-real boundaries with source links; nav entry under Reference; strict build passes.
- [x] **Trigger threat model.** ~~Webhook HMAC is well-documented. Cron + manual trust boundary is not articulated — `manual.enqueue` trusts caller; cron trusts nothing (good); should note in `security/threat-model.md`.~~ Resolved: added "Trigger trust boundaries" section to `security/threat-model.md` covering all 3 trigger types with code refs.
- [ ] **Ship a second example skill bundle.** `shipwright` is the only one in tree. Adding a tiny "echo skill" alongside would let `how-to/build-skill.md` link two patterns.
- [x] **`harbor inspect` vs `harbor run --inspect` confusion.** ~~Both exist with different surfaces. Tutorial agent had to clarify ordering. Consider renaming the run flag to `--summary` or similar.~~ Resolved: `--dry-run` is now the primary name; `--inspect` kept as backward-compat alias. Help text disambiguates.
- [x] **Surface Bosun pack signing semantics in `security/threat-model.md`.** ~~`harbor.bosun.signing` is alg-strict EdDSA-only with TOFU drift; currently only mentioned in `how-to/bosun-pack.md`.~~ Resolved: added "Bosun pack signing — TOFU drift" section to threat-model with cases the pin catches and what it doesn't.
- [x] **`status="awaiting-input"` resume requires cold-restart.** ~~`docs/engine/` should make explicit that warm in-process resume isn't supported in v1.~~ Resolved: `docs/engine/index.md` lifecycle section now lists the full `RunState` set incl. `awaiting-input` and explains cold-restart-only resume; cross-links to `reference/v1-limits.md`.
- Add support for neo4j

---

## DX / Tracing / Audit / Governance — observed gaps from cve_remediation live-run (2026-05-04)

Surfaced while running the cve_remediation pipeline end-to-end against a real Nautilus broker + ServiceNow PDI (`ven06858`). The story sold for this project is "DX, attestations, auditing, governance, tracing, debugging" — these are the gaps between that pitch and what an operator actually sees today.

### DX

- [ ] **`harbor inspect` timeline shows 4 of 4 columns blank.** `transition=-`, `tool_calls=[-]`, `rules=[-]` for every step. Just a node-id list. Populate from checkpoint's `last_node` → `next_action.target`, JSONL audit log filtered by step, and `clips_facts` delta. Add `dt_ms` (per-step latency) and `delta` (state-snapshot diff vs prev step). Without this, "tracing" is a node-name-list. Estimated ~half day.
- [ ] **No state-delta inspect view.** `harbor inspect <run> --diff-states 30 31` would show only fields that changed between two checkpoints. Required to debug things like "why did `cve_id` reset to empty by step 38?" — currently you `--step 30 --step 31 --step 32 ...` and diff JSON blobs by hand. Estimated ~3hr.
- [ ] **Two run paths drift.** `harbor run` CLI vs `live_test.py` in-process FastAPI have different lifespan composition order. Caused a real ContextVar inheritance bug this session — broker_lifespan engaged AFTER scheduler.start() in live_test.py, so worker tasks never inherited the broker contextvar; demo nodes silently fell back to offline despite `CVE_REM_LIVE_BROKER=1`. Status reported `done`. Either factor lifespan composition into a single helper both call, or add a startup self-check that fails loud when the contextvar isn't set under `--live-broker`.
- [ ] **`harbor run` runtime warnings leak.** Pydantic StrEnum serializer warnings spam the operator's terminal on every live run (filtered in pytest only). Either fix at source (return enum members not strings from real_nodes) or filter at runtime. Filtering at runtime is wrong — it hides real serialization issues — so prefer fixing the source.
- [ ] **CI does not run the demo's own live-broker path.** The new `cve-remediation-live-test` CI step exercises offline only. Add a `cve-remediation-live-broker` job that brings up the demo's docker-compose stack and runs `--live-broker` against it (the SN call still dry-run since CI has no real PDI).

### Attestations

- [ ] **Nautilus audit JSONL is double-JSON-encoded.** Each line is `{"metadata": {"nautilus_audit_entry": "<JSON STRING>"}}`. Reading the audit needs `json.loads(json.loads(line)['metadata']['nautilus_audit_entry'])`. This is upstream Nautilus shape but Harbor should ship a thin reader (`harbor.audit.nautilus_reader.iter_entries(path) -> Iterator[NautilusAuditEntry]`) so consumers don't reverse-engineer it.
- [ ] **No `harbor verify-audit` CLI.** Operator currently has no way to verify the Ed25519 JWS attestation chain end-to-end. Need a CLI that takes a JSONL path, walks every entry, verifies the signature against the wired Ed25519 public key, asserts hash-chain continuity, and reports per-entry status. Estimated ~half day.
- [ ] **Attestation tokens not surfaced in `harbor inspect`.** A run produces N broker calls, each with a JWS token. Inspect output never references them. Add a column `attestation` to the timeline that maps step → token id (truncated) so an operator can trace from "step 12 of run X" to the exact JWS to verify.

### Auditing

- [ ] **No joining key between Harbor run + Nautilus audit.** Harbor `run_id=019df4be-421c-79a2-b8af-34f5fbd52ae6`. Nautilus `request_id=055c9a7e-66a4-4c6b-a2c8-739db3239e3a`. Two separate UUIDs, no link. Fix: stamp `harbor_run_id` + `step_idx` into the Nautilus context dict on every `_dispatch_intent` call. Surface the joined view in `harbor inspect`. Estimated ~2hr.
- [ ] **No link from CR creation to authorizing JWS.** ServiceNow CHG0040723 was created. Harbor state has `cr_correlation_id` + `servicenow_response`. Nautilus audit has the JWS that authorized the upstream broker call. Nothing connects them. The end-to-end attestation chain is the central pitch and it's broken at this seam. Stamp the broker JWS id into `servicenow_response.__harbor_provenance__.authorizing_jws` and surface in inspect.
- [ ] **Adapter errors silently absorbed at run level.** threat_graph errored with `Couldn't connect to localhost:7687`. Run reported `status=done`. The Nautilus audit captured it (`error_records[0].source_id=threat_graph`) but Harbor's run output and inspect never surfaced it. Add a "warnings" column to the run summary populated from broker `error_records`. Estimated ~1hr.

### Governance

- [ ] **`ToolRegistry.compatible_with` is a Phase-1 stub.** Returns ALL registered tools regardless of graph capabilities. The capability-driven filter (FR-23, AC-3) was deferred to Phase 3 (task 3.13). This means a graph that lacks `tools:servicenow:write` capability can still resolve and call `harbor.tools.servicenow.create_change_request`. Implement the real filter against `ToolSpec.permissions` vs graph-declared capabilities. Estimated ~half day.
- [ ] **No visibility into intent → purpose mapping.** Demo's `_INTENT_PURPOSE_OVERRIDES` dict in `real_nodes.py` controls which Nautilus purpose each broker intent claims. Operator has zero way to inspect or override this without reading the source. Surface as a config (YAML or `nautilus.yaml`-adjacent file) with a CLI dump (`harbor intent-purposes --graph <path>`).
- [ ] **HitlChangeApproval auto-rejects silently in `--non-interactive` mode.** Live SN call returned `ok` (CHG0040723 created). HITL gate then auto-rejected because no responder. State ends at `cr_status=rejected` with no captured reason. Expected: when HITL auto-decides, write the actor + reason to state explicitly, and surface in inspect output.
- [ ] **Capability denied requests aren't surfaced.** When a Nautilus rule denies a source, the audit captures it but Harbor's inspect / run output don't. Operator sees `status=done` and assumes everything ran. Add a "denials" view to inspect that pulls from broker audit `denial_records`.

### Tracing

- [ ] **No per-step latency.** Checkpoints carry timestamps but inspect doesn't compute deltas. One step in the live run took 35075ms (the broker call). Invisible from the timeline. Add `dt_ms = ts[N] - ts[N-1]` to the timeline column. Estimated 30min.
- [ ] **No transition source → target.** Inspect shows `transition=-` for every step. Stamp the transition action (`goto`/`parallel`/`halt` + target) into the checkpoint and render in the timeline.
- [ ] **No tool-call list per step.** A step that triggers a broker dispatch produces a `ToolCallEvent` + `ToolResultEvent` on the bus, written to JSONL. Inspect's `tool_calls=[-]` ignores these. Join JSONL events to checkpoints by step_idx in `build_timeline`.
- [ ] **No rule-firing attribution.** When CLIPS rules fire (`r-advance`, `r-halt`, etc.) they emit on the bus but inspect's `rules=[-]` ignores them. Same join fix as tool calls.
- [ ] **Branch fan-out targets not visible.** When a rule emits `parallel` with N targets, the timeline collapses them into a single transition. Visualizing branches requires reading the IR + parallel actions by hand. Render parallel targets explicitly: `step=12 parallel→[node_a, node_b, node_c]`.

### Debugging

- [ ] **State propagation bug surfaced by live run: `cve_id` resets to empty.** Seed was `CVE-2026-DEMO-LIVE-001`. Final state at step 58 has `cve_id=''`. CR description shows `CVE: ` (blank). Need state-delta inspect to find the responsible step. Open as a separate bug once the inspect view exists.
- [ ] **Replay cassette layer for broker dispatch missing.** `WriteArtifactNode` cassette landed (FR-7 follow-up), but broker calls bypass replay. Replaying a checkpoint that includes broker dispatches is non-deterministic — every replay re-hits the network. Extend the cassette protocol to cover broker requests keyed by `(intent, fact_set_hash)`.
- [ ] **No `harbor doctor`-style pre-flight.** Operator ran `--live-broker` against a `nautilus.yaml` with the wrong schema (fictional fields). Nautilus errored mid-run. A `harbor doctor --config-dir <dir>` would parse the yaml, validate against the installed Nautilus schema, and surface mismatches before the run starts. Estimated ~half day.
- [ ] **No correlation between graph_hash + cassette compatibility.** Cassettes are bound to a structural+runtime hash but inspect doesn't show whether a given checkpoint's hashes match the current graph. Replay rejection is loud but you don't know it'll reject until you try. Add `harbor inspect --replay-feasibility <run-id>` that compares hashes and reports without attempting replay.
