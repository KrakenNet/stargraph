# Stargraph — Roadmap

**Status:** Draft v0.1
**Owner:** Sean Mauk

Build order is opinionated. Each milestone is gated by a real workload validating the prior one. **Do not advance milestones to chase features.**

---

## Phase 0 — Pre-flight (1 week)

**Goal:** unblock the dependency chain.

- [ ] Fathom v0.1: load YAML rules, assert facts, fire rules, return actions
- [ ] Decide repo layout (monorepo vs split: stargraph / fathom / bosun)
- [ ] Pin DSPy version; smoke test against current release
- [ ] Skeleton repo, CI, license (Apache-2.0 recommended)

**Exit criteria:** Fathom can route a 5-rule example end-to-end from the CLI.

---

## Phase 1 — Core Graph (2 weeks)

**Goal:** prove the thesis on a real Kraken workload.

- [ ] `stargraph.Graph`: nodes, state, transition loop
- [ ] Boundary state sync to CLIPS via Fathom
- [ ] Pydantic state schema; structural hash
- [ ] **Provenance-typed facts (foundational)**
- [ ] DSPy node adapter
- [ ] SQLite checkpointer; `resume(run_id)`
- [ ] YAML loader: nodes, rules, transitions
- [ ] `stargraph run` CLI
- [ ] One existing Kraken workload ported (CVE pipeline or ServiceNow flow)

**Exit criteria:** ported workload runs on Stargraph, produces an audit trail, is at least as fast as the original.

**If exit fails:** stop. Fix Fathom or rethink the API. Do not proceed.

---

## Phase 2 — Differentiators (2 weeks)

**Goal:** the features that make Stargraph *Stargraph*.

- [ ] **Classical ML node adapter** (sklearn, XGBoost, ONNX)
- [ ] ML training as sub-graph pattern + model registry
- [ ] **Counterfactual replay** (`run.counterfactual(step=N, mutate=...)`)
- [ ] Streaming event bus (token, tool_call, transition, checkpoint, result)
- [ ] Tool registry + Stargraph tool schema
- [ ] Tool adapters: DSPy ↔ Stargraph, MCP ↔ Stargraph
- [ ] **Dual-truth state (opt-in)** for fields with symbolic shadows

**Exit criteria:** demo showing ML+LLM hybrid graph with counterfactual replay and streaming.

---

## Phase 3 — Skills, Memory, Knowledge (3 weeks)

**Goal:** make agents and knowledge workflows authorable.

- [ ] Skill base class; entry-point plugin loading
- [ ] Agent-as-subgraph pattern; reference ReAct skill
- [ ] Store Protocols: Vector, Graph, Doc, Memory, Fact
- [ ] Default adapters: LanceDB, Kuzu, SQLite
- [ ] Retrieval node with multi-store fusion + rerank
- [ ] Episodic + semantic memory tiers; consolidation rules
- [ ] KG triple promotion to CLIPS facts
- [ ] Reference skills: `rag`, `autoresearch`, `wiki`

**Exit criteria:** autoresearch skill produces a wiki entry from a topic with full provenance.

---

## Phase 4 — Concurrency & Workflow (2 weeks)

**Goal:** production workflow surface.

- [ ] YAML parallel/join with field-level merge strategies
- [ ] `stargraph serve` daemon
- [ ] Triggers: cron, webhook, file_watch, mcp, manual
- [ ] HTTP + WebSocket endpoints; run history API
- [ ] Inspect/replay tooling (`stargraph inspect <run_id>`)

**Exit criteria:** scheduled workflow running in production at Kraken.

---

## Phase 5 — Governance & Hardening (2 weeks)

**Goal:** ready for cleared deployment.

- [ ] Bosun rule packs as separate repo
- [ ] Reference packs: `budgets`, `audit`, `safety/pii`, `retries`
- [ ] Pack versioning + compatibility checks
- [ ] Air-gap deployment guide
- [ ] Threat model document
- [ ] Security review (internal)

**Exit criteria:** Stargraph deployable in an air-gapped environment with a complete audit trail.

---

## Phase 6 — OSS Launch (4 weeks, optional)

**Goal:** controlled public release after internal validation.

- [ ] Documentation site
- [ ] Tutorial: ReAct agent in 50 lines
- [ ] Tutorial: classical ML + LLM hybrid graph
- [ ] Tutorial: autoresearch wiki
- [ ] Migration guide from LangGraph (the realistic audience)
- [ ] Blog post: the thesis, with benchmarks
- [ ] Public repo, CONTRIBUTING.md, code of conduct
- [ ] Discord or Discussions

**Gate:** at least two non-trivial Kraken workloads in production on Stargraph for 60+ days.

---

## What's deliberately deferred

- Visual graph editor / UI
- Distributed execution (single-process is fine for v1)
- Hosted service offering
- Self-amending rule packs (research, not product)
- Rules-as-tests dual-purpose syntax

---

## Pace expectations

- Phases 0–5: ~12 weeks of focused work
- Phase 6: only after real validation
- Slipping right is fine. Skipping ahead is not.

## Stop conditions

- Fathom cannot reach usable v1
- DSPy roadmap absorbs graph orchestration natively
- Phase 1 exit criteria fail twice
