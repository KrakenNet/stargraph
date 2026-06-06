# Stargraph — Concepts & Glossary

**Status:** Draft v0.1
**Purpose:** One source of truth for Stargraph vocabulary. Disambiguates terms that get conflated.

---

## Core terms

| Term | Definition |
|---|---|
| **Graph** | Definition: nodes, state schema, rules, governance. A blueprint, not a running thing. |
| **Run** | A single execution of a graph, addressable by `run_id`. |
| **Node** | Unit of work inside a graph. Wraps a callable: DSPy module, ML model, tool invocation, retrieval op, or sub-graph. |
| **Edge** | *Implicit.* Stargraph has no static edges. Transitions are derived at runtime by rules over state. |
| **State** | The Pydantic-typed bundle of values flowing through a run. |
| **Annotated state** | Subset of state fields marked for mirroring into CLIPS working memory at node boundaries. |
| **Fact** | A CLIPS working-memory tuple. Mirrored from annotated state, emitted by the runtime, or asserted by rules. |
| **Rule** | A single Fathom/CLIPS production. Matches facts; emits a routing action (`goto`, `parallel`, `halt`). |
| **Pack** | A versioned, named collection of rules. Mounted onto a graph declaratively. |
| **Tool** | A typed callable with JSON Schema, namespace, permissions, and side-effect declarations. Invokable from nodes. |
| **Skill** | A bundle of tools, optional sub-graph, and optional prompt fragment. The unit of capability composition. |
| **Plugin** | A pip-installable Python package exposing skills, tools, nodes, or stores via entry points. |
| **Store** | The data tier abstraction: `VectorStore`, `GraphStore`, `DocStore`, `MemoryStore`, `FactStore`. |
| **Provider** | A concrete implementation of a Store Protocol (e.g., LanceDB is a vector provider). |
| **Checkpoint** | A persisted snapshot at a transition: state, facts, last node, next action, graph hash. |
| **Graph hash** | Structural fingerprint: topology + node signatures + state schema. Used for resume safety. |
| **Trigger** | An external event source that initiates a run: `cron`, `webhook`, `file_watch`, `mcp`, `manual`. |
| **Run history** | The ordered list of checkpoints + events for a run. The basis for replay and counterfactuals. |

---

## Disambiguations

These pairs are routinely confused. The distinction matters.

### Node vs Tool
- A **node** is a step in a graph. Has state in/out semantics.
- A **tool** is a callable a node may invoke. May not appear in the graph at all.
- A node *can be* a single tool call (`stargraph.nodes.tool_call`), but most nodes do more.

### Skill vs Plugin
- A **skill** is a logical bundle of capability (e.g., "research").
- A **plugin** is a *distribution* mechanism. One plugin can ship many skills.

### Rule vs Pack
- A **rule** is one production.
- A **pack** is a versioned set of rules with a documented fact vocabulary.
- Routing rules and governance rules are both rules; packs are usually one or the other by convention.

### Store vs Provider
- **Store** = the Protocol (interface).
- **Provider** = the implementation.
- Code talks to Stores; configuration selects Providers.

### State vs Facts
- **State** is Pydantic, lives in Python, mutates freely inside a node.
- **Facts** are CLIPS tuples, mirrored from annotated state at node exit.
- Source of truth is State; Facts are the projection rules can reason over.

### Subgraph vs Skill
- A **subgraph** is a graph used as a node.
- A **skill** is a packaged, named bundle that may include a subgraph.
- All skills with logic contain a subgraph; not all subgraphs are skills.

### Graph vs Run
- A **graph** is static; deterministic given inputs and seeds.
- A **run** is dynamic; has an ID, history, and resumable state.

### Routing rule vs Governance rule vs Assertion
- **Routing rule** decides where execution goes next.
- **Governance rule** (Bosun) constrains, observes, or modifies execution (budgets, audits, retries).
- **Assertion** declares an invariant that must always hold over facts.
- All three are Fathom rules. Convention separates them; nothing in the engine does.

### Skill vs Agent
- **Skill** is the package.
- **Agent** is a runtime use of a skill (typically a `think → act → observe` subgraph) executing against goals.
- "Agent" describes behavior; "skill" describes structure.

---

## Naming conventions

- **Tool names:** `namespace.name` (e.g., `web.search`, `kraken.servicenow.create_incident`)
- **Skill names:** lowercase, hyphenated, plugin-namespaced (`research-agent`, `kraken/triage`)
- **Pack names:** `vendor:pack@version` (e.g., `bosun:budgets@1.2`)
- **Fact templates:** `stargraph.*` reserved for runtime; `bosun.*` for governance; `user.*` for application-defined
- **Run IDs:** `r-` prefix + 6-char base32

---

## What Stargraph is *not*

- Not a prompt-optimization framework — that's DSPy
- Not an inference engine — that's Fathom/CLIPS
- Not a database — stores are pluggable, defaults are embedded
- Not a vector DB or knowledge graph — Providers wrap real ones
- Not a workflow UI — `stargraph serve` is headless; UI is a future product
