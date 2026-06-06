# Stargraph — Design Document

**Status:** Draft v0.1
**Owner:** Sean Mauk
**Related:** Fathom (CLIPS runtime), Bosun (governance packs), Nautilus (knowledge engine), Railyard (Go predecessor)

---

## 1. Thesis

> **LLMs are knowledge engineers, not inference engines. CLIPS is a better inference engine than any LLM. Use the right tool for each job and let rules — not LLMs — decide what happens between tools.**

Stargraph is a Python framework for orchestrating LLMs, classical ML models, tools, and deterministic logic into auditable, testable, replayable graphs governed by declarative rules.

## 2. Positioning

Stargraph is **Railyard's Python cousin** and may supersede it. It targets:

- **Primary (now):** Kraken Networks internal use — DoD/cleared workloads where auditability, determinism, and provenance matter more than ecosystem size.
- **Secondary (later):** Engineers who have been burned by LLM-as-router in production and want correctness.

Stargraph is **not** competing with LangGraph or n8n on mindshare. It competes on correctness, inspectability, and the ability to run in environments those tools can't.

## 3. Goals

- Declarative, rule-based routing inspectable without execution
- First-class support for LLMs, classical ML, tools, and rules in the same graph
- Boundary-clean state with full provenance on every fact
- Replay and counterfactual debugging from any checkpoint
- YAML authorability for non-Python contributors
- Pluggable stores (vector, graph, doc, memory, fact)
- Suitable for air-gapped and cleared deployments

## 4. Non-Goals

- Visual UI in v1 (post-v1 if at all)
- Replacing DSPy's prompt optimization
- Owning embedding models, vector DBs, or graph DBs
- Generic workflow automation parity with n8n

## 5. Architecture

```
stargraph serve            triggers, scheduler, HTTP/WS, run history
       │
stargraph.Graph            orchestration, streaming, checkpointing
       │
stargraph.skills           agents-as-subgraphs, tool registry, plugins
       │
Bosun rule packs        budgets, retries, safety, audit (Fathom rules)
       │
Fathom (CLIPS)          routing and governance inference
       │
Nodes:  DSPy │ ML models │ tools │ retrieval │ memory ops
```

## 6. Core Concepts

| Concept | Definition |
|---|---|
| **Graph** | Directed structure of nodes connected by rule-derived transitions over typed state. |
| **Node** | Unit of work: a DSPy module, ML model, tool call, retrieval op, or sub-graph. |
| **State** | Pydantic schema; subset annotated for mirroring into CLIPS at node boundaries. |
| **Rule** | Fathom/CLIPS production matching state facts; emits `goto`, `parallel`, `halt`. |
| **Skill** | Bundle of tools, optional sub-graph, optional prompt fragment. Plugin-installable. |
| **Tool** | Typed callable with JSON schema, namespace, and side-effect declarations. |
| **Store** | Provider behind a Protocol: vector, graph, doc, memory, fact. |
| **Pack** | Versioned collection of Bosun rules mounted onto a graph. |
| **Run** | A single execution, addressable by `run_id`, fully resumable from checkpoints. |

## 7. Key Design Decisions

### 7.1 State sync: boundary-only
Mutate freely in Python during a node. On exit, mirror annotated fields to CLIPS, fire rules, persist checkpoint. Predictable, testable, transactional.

### 7.2 Rules as composable packs, not middleware
Governance mounts declaratively (`governance: [bosun:budgets, bosun:audit]`) rather than as Python middleware chains. Inspectable, versioned, environment-swappable.

### 7.3 Stargraph owns the tool schema
Tools are defined against Stargraph's spec (JSON Schema + namespace + permissions + side-effect flags). Adapters bridge to DSPy and MCP. Tools become portable across non-DSPy nodes.

### 7.4 State schema: Pydantic + thin YAML DSL
Python authors get full Pydantic. YAML authors get a compiled subset. One source of truth at runtime.

### 7.5 Stores behind Protocols
Vector, graph, doc, memory, and fact stores are interfaces with default embeddable implementations (LanceDB, Kuzu, SQLite). All retrieval, RAG, KG, and memory features compose from these.

### 7.6 Versioning by structural hash
Graph hash = topology + node signatures + state schema. Checkpoints carry the hash. Resume refuses on mismatch unless a declared `migrate` block applies.

## 8. Differentiating Features

These are the four features that distinguish Stargraph from competitors. Each reinforces the thesis.

### 8.1 Provenance-typed facts (foundational)
Every fact in working memory carries `(origin, source, run_id, step, confidence, timestamp)`. Origins are typed: `llm | tool | user | rule | model | external`. Rules can pattern-match on provenance:

```yaml
- when: { evidence.origin: tool, evidence.confidence: { gte: 0.8 } }
  then: { goto: act }
```

Trust becomes a first-class type. Required for cleared work and impossible to retrofit.

### 8.2 Classical ML as first-class nodes
sklearn, XGBoost, PyTorch, ONNX models run as nodes alongside DSPy. Training loops are sub-graphs. Model outputs flow into CLIPS facts and are routable on the same primitives as LLM outputs.

```yaml
nodes:
  classify_intent: ml:intent_clf@v3        # XGBoost; faster, cheaper, auditable
  fallback_llm:    dspy:intent_predict
rules:
  - when: { classify_intent.confidence: { lt: 0.7 } }
    then: { goto: fallback_llm }
```

This is the thesis applied one layer down: use the right model for the job. Also expands deployable surface to environments that disallow frontier LLMs.

### 8.3 Counterfactual replay
Checkpoint pinning + graph hashing makes deterministic replay free. Counterfactuals add: re-execute from any step with mutated rule, node output, or fact.

```python
run = stargraph.load_run("r-7af2")
alt = run.counterfactual(step=4, mutate={"facts.intent": "research"})
diff = stargraph.compare(run, alt)
```

Debugging agent failures becomes a science. Regression analysis becomes tractable.

### 8.4 Dual-truth state (opt-in)
Annotated state fields can carry both an LLM-derived value and a CLIPS-inferred shadow. Disagreement is itself a fact:

```
(disagreement field=intent llm=research rules=chitchat)
```

Hallucination detection becomes a native primitive. Rules route on disagreement. Opt-in per field — only valuable where a meaningful symbolic shadow exists.

## 9. Stack Choices

| Layer | Choice | Reason |
|---|---|---|
| Inference engine | Fathom (CLIPS) | Real production system; thesis-aligned |
| LLM nodes | DSPy | Best-in-class prompt optimization |
| Tool schema | Stargraph-native + adapters | DSPy/MCP interop without lock-in |
| State | Pydantic + YAML DSL | One runtime truth, two authoring modes |
| Persistence | SQLite default; Postgres/Redis adapters | Zero-infra start; production-ready upgrade |
| Vector store | LanceDB default | Embeddable, no service to run |
| Graph store | Kuzu default | Embeddable Cypher; pairs cleanly with CLIPS |
| Doc store | SQLite default | Coherent with checkpointer |
| Streaming | Async iterator + tagged events | Single bus, all event types |
| Concurrency | YAML-declared parallel/join | Auditable; non-Python authors can express |

## 10. Memory & Knowledge Architecture

Three tiers with distinct lifetimes:

- **Working** — graph state + CLIPS facts; ephemeral, this run only
- **Episodic** — `MemoryStore` scoped by `(user, session, agent)`; rule-scheduled consolidation
- **Semantic** — promoted to `FactStore`/`GraphStore` when stable

Knowledge graphs serve two roles:
- *Retrieval-time:* `GraphStore` queried by retrieval nodes (entity expansion, multi-hop)
- *Inference-time:* selected triples promoted to CLIPS facts so Fathom can reason over them

Reference skills bundle these patterns: `stargraph.skills.rag`, `stargraph.skills.autoresearch`, `stargraph.skills.wiki`.

## 11. Concurrency

Parallel execution declared in YAML; field-level state merge with per-field strategies (`last-write`, `append`, `max`, custom).

```yaml
- when: { last: classify, intent: research }
  then:
    parallel: [search_web, search_arxiv, search_internal]
    join: synthesize
    strategy: all          # all | any | race | quorum:N
    timeout: 30s
```

## 12. Streaming

Single async iterator per run. Tagged events: `token`, `tool_call`, `tool_result`, `transition`, `checkpoint`, `error`, `result`. Nodes opt into token streaming by being async generators.

## 13. Persistence

`Checkpointer` interface; SQLite default. Checkpoint per transition records `(run_id, step, graph_hash, state, clips_facts, last_node, next)`. Supports `resume`, `resume(from_step=N)`, and counterfactual replay.

## 14. Trigger / Workflow Layer

`stargraph serve` exposes triggers: `cron`, `webhook`, `file_watch`, `mcp`, `manual`. Daemon supervises runs, persists to checkpointer, exposes HTTP + WebSocket. UI deferred.

## 15. Open Questions

- Memory salience scoring: rule-based (default) vs learned vs hybrid
- Per-store embedding strategy (default + override) vs Stargraph-managed
- Bosun pack distribution: in-tree, separate repo, or registry
- Test pyramid: how rule packs ship with property-based tests

## 16. Risks

- Fathom maturity is the gating dependency
- Audience confusion if "internal Kraken" and "OSS for non-ML devs" pull in different directions
- Over-building pre-validation: Memory/KG/retrieval before a real workload demands them
- Coupling between Stargraph, Bosun, and Fathom slowing all three
