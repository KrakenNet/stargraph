# Stargraph — Architecture Decision Records

**Status:** Draft v0.1
**Format:** Each ADR is short. Context, Decision, Consequences, Alternatives. Date and status at top.

ADRs are immutable once accepted. Supersede by writing a new ADR that references the old.

---

## Index

| # | Title | Status |
|---|---|---|
| 0001 | State sync at node boundaries | Accepted |
| 0002 | Governance as composable rule packs, not middleware | Accepted |
| 0003 | Stargraph owns the tool schema | Accepted |
| 0004 | Pydantic + YAML DSL for state schemas | Accepted |
| 0005 | Structural-hash versioning of graphs | Accepted |
| 0006 | Stores behind Protocols with embeddable defaults | Accepted |
| 0007 | Provenance-typed facts as a foundation | Accepted |
| 0008 | Classical ML as first-class node type | Accepted |
| 0009 | YAML/JSON IR is the canonical graph definition | Accepted |
| 0010 | Defer visual UI; favor inspectability tooling | Accepted |

---

## ADR 0001 — State sync at node boundaries

**Status:** Accepted · 2026-04-25

**Context.** Rules need to reason over Python state. Mirroring on every Python mutation is unpredictable and fights the runtime. Mirroring only at node boundaries is a natural transactional point.

**Decision.** Annotated state fields are mirrored into CLIPS working memory only on node exit. Rules fire after mirroring; routing actions are produced; the next node begins.

**Consequences.**
- Predictable rule firing semantics
- Easy to test rules in isolation
- Nodes are free to mutate state imperatively without surprise
- A misbehaving node that doesn't return state cannot leak partial mutations to rules

**Alternatives.** Continuous sync (rejected: nondeterministic), explicit `flush()` calls (rejected: leaky).

---

## ADR 0002 — Governance as composable rule packs, not middleware

**Status:** Accepted · 2026-04-25

**Context.** Cross-cutting concerns (budgets, audit, retries, safety) need to compose. Python middleware chains hide control flow in decorators — exactly what Stargraph is trying to escape with declarative routing.

**Decision.** Bosun governance ships as versioned rule packs. Packs are mounted declaratively in graph YAML. The runtime composes their facts and rules with the user's.

**Consequences.**
- Governance is inspectable, swappable, and version-pinnable
- Same paradigm as routing — one mental model
- Packs can be authored and audited by non-Stargraph teams
- Pack interactions are visible (rules) instead of invisible (call order)

**Alternatives.** Middleware chain (rejected: hidden control flow), in-tree governance (rejected: blocks third-party packs), per-node hooks (rejected: doesn't compose globally).

---

## ADR 0003 — Stargraph owns the tool schema

**Status:** Accepted · 2026-04-25

**Context.** DSPy signatures and MCP tool definitions both exist. Neither covers what Stargraph needs: namespacing, side-effect declarations, idempotency keys, permissions, governance hooks.

**Decision.** Stargraph defines its own tool schema (JSON Schema + metadata). Adapters bridge in both directions: DSPy ↔ Stargraph and MCP ↔ Stargraph.

**Consequences.**
- Tools are usable from any node type, not just DSPy
- Governance can reason about tools uniformly
- Migration path to/from MCP is explicit
- Cost: maintaining two adapters

**Alternatives.** Use DSPy signatures (rejected: missing fields), use MCP directly (rejected: too coupled to a transport), generate from Pydantic only (rejected: insufficient metadata).

---

## ADR 0004 — Pydantic + YAML DSL for state schemas

**Status:** Accepted · 2026-04-25

**Context.** Python authors want full Pydantic. YAML authors need declarative schemas without Python.

**Decision.** Pydantic is the runtime truth. A subset YAML schema DSL compiles to Pydantic at load time. The compiled Pydantic is what executes.

**Consequences.**
- Single runtime type system
- YAML authors get a constrained surface (good for non-ML devs)
- JSON Schema can be generated from either side, useful for the planned no-code UI

**Alternatives.** Two parallel systems (rejected: drift), YAML only (rejected: Python authors lose power), Pydantic only (rejected: blocks YAML and future UI).

---

## ADR 0005 — Structural-hash versioning of graphs

**Status:** Accepted · 2026-04-25

**Context.** Resuming a run against a changed graph can corrupt state silently. We need a fast, deterministic way to detect incompatibility.

**Decision.** Compute `graph_hash = sha256(canonical(topology + node signatures + state schema + rule pack versions))`. Checkpoints carry the hash. Resume rejects on mismatch unless a declared `migrate` block applies.

**Consequences.**
- Safe resume by default
- Migration is explicit and reviewable
- Hash is also useful for caching and run grouping

**Alternatives.** Manual versioning (rejected: relies on humans), no versioning (rejected: silent corruption).

---

## ADR 0006 — Stores behind Protocols with embeddable defaults

**Status:** Accepted · 2026-04-25

**Context.** Vector, graph, doc, memory, and fact stores all have many implementations. Locking to one set hurts adoption; building everything ourselves is wasteful.

**Decision.** Define narrow Protocols (`VectorStore`, `GraphStore`, `DocStore`, `MemoryStore`, `FactStore`). Ship default Providers that are embeddable and zero-infra (LanceDB, Kuzu, SQLite). Adapters for production providers are plugins.

**Consequences.**
- Trivial getting-started experience
- Production users pick their own stack
- Plugins drive ecosystem growth
- Cost: must maintain narrow but complete Protocols

**Alternatives.** Bundle a fixed stack (rejected: deployment friction), interface to LangChain stores (rejected: dependency surface).

---

## ADR 0007 — Provenance-typed facts as a foundation

**Status:** Accepted · 2026-04-25

**Context.** Cleared deployments require auditability. Hallucination management requires distinguishing LLM-claimed values from tool-returned ones. Counterfactual replay requires source attribution.

**Decision.** Every value-bearing fact carries a provenance bundle: `(origin, source, run_id, step, confidence, timestamp)`. Origins are typed. Rules can pattern-match on provenance.

**Consequences.**
- Trust becomes a first-class type
- Audit trail is automatic, not bolted on
- Rules can express "trust tools more than LLMs" declaratively
- Cost: every fact is heavier; storage size grows

**Alternatives.** Provenance as an optional sidecar (rejected: retrofit pain), provenance only on selected facts (rejected: fragmented).

---

## ADR 0008 — Classical ML as first-class node type

**Status:** Accepted · 2026-04-25

**Context.** "LLMs as the right tool for some jobs" generalizes. A logistic regression is faster, cheaper, more accurate, and more auditable for many classification tasks. Cleared environments often cannot run frontier LLMs.

**Decision.** Stargraph ships an `MLNode` adapter (sklearn, XGBoost, ONNX, PyTorch) and a training-as-subgraph pattern. Model outputs flow into facts on the same primitives as LLM outputs.

**Consequences.**
- "Use the right model" becomes the framework's stance
- Deployable in air-gapped environments
- Model registry and training pipelines are graphs themselves
- Cost: another adapter surface to maintain

**Alternatives.** Wrap models in tools (rejected: loses lifecycle and metadata), defer to v2 (rejected: changes positioning materially).

---

## ADR 0009 — YAML/JSON IR is the canonical graph definition

**Status:** Accepted · 2026-04-25

**Context.** Future plans require: bidirectional conversion with LangGraph/Agno/CrewAI/n8n; AI-harness plugins that author and edit graphs; a no-code UI; chat agents that build graphs. All of these require a machine-readable, language-neutral representation.

**Decision.** The YAML/JSON IR is the source of truth for graph definition. Python builders are convenience constructors that emit IR. Runtime consumes IR, not Python objects directly.

**Consequences.**
- Conversions, UIs, and agent-authored graphs become tractable
- Diffing, versioning, and migration operate on IR
- Python builders cannot introduce features the IR can't express
- Cost: must keep IR expressive enough; resist Python-only escape hatches

**Alternatives.** Python-first with serialization adapters (rejected: features will leak into Python and never reach IR), per-target conversion at boundaries (rejected: O(n²) adapters).

---

## ADR 0010 — Defer visual UI; favor inspectability tooling

**Status:** Accepted · 2026-04-25

**Context.** A visual builder is a v3+ product. v1 ships to engineers.

**Decision.** No visual UI in v1. Invest instead in CLI inspect/replay/diff/counterfactual tools. The IR (ADR 0009) keeps a future UI viable.

**Consequences.**
- Smaller v1 surface
- Engineers get the tools they actually want
- UI is unblocked when it becomes a priority

**Alternatives.** Build a minimal UI in v1 (rejected: distracts from core), build a chat UI first (rejected: requires the framework to be solid first).
