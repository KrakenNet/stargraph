# StarGraph vs CrewAI

A candid, gap-focused comparison. This page exists to answer one question
honestly: **what does [CrewAI](https://www.crewai.com/) do that StarGraph does
not?** It is deliberately one-sided — StarGraph's own advantages (deterministic
rule routing, provenance, replay, classical-ML routing, air-gap posture) are
summarized only briefly at the end. Both projects are genuinely trying to be
agent frameworks, so they overlap on most axes. The headline difference is the
control model: CrewAI is built around a **role-based multi-agent team** that the
LLM drives, while StarGraph splits the worker from the router and lets
**rules**, not the LLM, decide what happens next. If CrewAI is on your
shortlist, read this as the list of things you would be giving up.

> **Versions compared:** StarGraph v0.4 (alpha, public API unstable) vs CrewAI
> v1.14.x (stable, MIT, June 2026). CrewAI is built from the ground up with no
> dependency on LangChain.

---

## TL;DR — different design centers

| | **StarGraph** | **CrewAI** |
| --- | --- | --- |
| Design center | Determinism, auditability, provenance, replay | Role-based multi-agent collaboration, breadth, enterprise platform |
| Routing / control | Rules (Fathom/CLIPS) over typed facts | Agents delegate to each other (Crews) or you wire event-driven Flows |
| Target user | Regulated / cleared / air-gapped (DoD, finance, healthcare) | General Python teams and enterprises shipping multi-agent automations |
| Maturity | v0.4 alpha, single-org | v1.14.x stable, ~54k★, large community, Enterprise/AMP platform |
| Core bet | "Rules, not LLMs, decide what happens next" | "Give agents roles and let them collaborate; ship it on a managed platform" |

CrewAI wins on almost every axis a typical team scores in the first week:
the multi-agent paradigm itself, a large prebuilt toolkit, three-tier memory,
knowledge sources, a no-code visual builder, a managed enterprise control plane,
multimodal, and an enormous community. StarGraph wins on a narrow set of axes
(provability, replay, classical-ML routing, air-gap) that most teams do not need
— but the few that do cannot get from CrewAI. **The rest of this page is the
first-week list.**

---

## Where CrewAI is ahead

### 1. Maturity and community

The single biggest gap, and it is not close.

| | StarGraph | CrewAI |
| --- | --- | --- |
| GitHub stars | — (early/single-org) | ~54,000 (June 2026) |
| License | Apache-2.0 | MIT |
| Releases | v0.4 alpha, API unstable until v1.0 | v1.14.x, stable, semver, post-1.0 |
| Certified developers | None | 100,000+ (CrewAI's own figure) |
| Examples / courses | Handful of in-tree demos | Large cookbook, courses, third-party tutorials |
| Enterprise adoption | None published | Claims use across a majority of the Fortune 500 |

**Gap:** community, battle-testing, a deep example library, and the assurance
that today's API survives to next quarter. For many teams this alone decides it.

### 2. The role-based multi-agent team paradigm (Crews)

- **CrewAI:** the core abstraction is a **Crew** — a team of agents, each with a
  **role, goal, and backstory**, working **Tasks** under either a **sequential**
  or **hierarchical** process. In the hierarchical process a manager agent plans,
  delegates to workers, and reviews their output. Agents delegate to one another
  natively.
- **StarGraph:** composition is **sub-graphs** (`SubGraphNode`, skills-as-
  subgraphs). There is **no peer-agent team abstraction and no delegation model**.
  Multi-agent in StarGraph means "nest a graph," not "a manager agent assigns
  work to a researcher and a writer."

**Gap:** the entire role/goal/backstory team-of-agents paradigm and
agent-to-agent delegation.

### 3. Event-driven Flows with conditional routing

- **CrewAI:** **Flows** are a separate, deterministic orchestration layer —
  event-driven, with explicit state, `@start`/`@listen`/`@router` decorators,
  conditional branching, and the ability to embed Crews as steps. This gives you
  a precise, auditable control surface *alongside* the autonomous Crew mode.
- **StarGraph:** has deterministic routing too, but it is the *only* mode, and it
  is expressed as **CLIPS forward-chaining rules over typed facts** rather than
  Python decorators on an event graph. The concept overlaps; the authoring model
  is heavier and the learning curve steeper.

**Gap:** a lightweight, Pythonic event-driven control layer that most developers
can pick up in an afternoon (CLIPS rules are not that).

### 4. Prebuilt tools and integrations

- **CrewAI:** the `crewai-tools` package ships **40+ prebuilt tools** (web search,
  scraping, file and document readers, PDF/CSV/JSON/XML RAG tools, database tools,
  code tools, and more), plus first-class **MCP** support reaching thousands of
  community MCP-server tools, and a hosted **tool repository** in the enterprise
  platform.
- **StarGraph:** a tool registry, an MCP adapter (client), and a handful of
  in-tree tools (the Nautilus broker, reference skills). **No large prebuilt
  toolkit.** You write or wrap most tools yourself.

**Gap:** breadth of ready-made, one-line integrations.

### 5. Three-tier memory out of the box

- **CrewAI:** setting `memory=True` gives an agent **short-term** (ChromaDB +
  RAG for the current run), **long-term** (SQLite, persisted across runs so
  agents improve over time), and **entity** memory (tracking people, places, and
  concepts). A newer unified Memory API consolidates these, and external
  providers like Mem0 plug in.
- **StarGraph:** ships `MemoryStore` + `MemoryWriteNode` (SQLite), but memory is
  a **lower-level primitive**. There is no turnkey "remember this user and these
  entities across sessions, and learn from past runs" behavior — you assemble it.

**Gap:** turnkey, multi-tier agent memory with a one-flag on switch.

### 6. Knowledge sources

- **CrewAI:** a first-class **Knowledge** abstraction — pre-load text, PDF, CSV,
  JSON, and custom sources that agents query during execution, with chunking and
  embedding handled for you, scoped at the agent or crew level.
- **StarGraph:** has a `RetrievalNode` and a RAG reference skill backed by
  embedded LanceDB, but there is **no equally turnkey "attach these documents as
  agent knowledge" surface** with the same set of out-of-the-box loaders.

**Gap:** a one-call knowledge-source loader covering common file types.

### 7. No-code visual builder (Crew Studio)

- **CrewAI:** **Crew Studio** is a **no-code / low-code** interface in the
  enterprise platform for designing, configuring, and launching crews without
  writing Python, then deploying them with a few clicks.
- **StarGraph:** **no visual builder of any kind.** Authoring is Python (full
  Pydantic typing) or a compiled YAML subset. Both require an engineer; the
  README states the UI is a future product.

**Gap:** a no-code path for non-engineers to build and ship agents.

### 8. Managed enterprise platform (CrewAI AMP)

- **CrewAI:** **AMP** (the Agent Management Platform / enterprise suite) provides
  a **unified control plane**, one-click **managed deployment** (from GitHub, the
  CLI, or Crew Studio), a hosted **tool repository**, REST API and webhook
  integration, real-time observability, guardrails, and 24/7 support, with both
  cloud and on-premise options.
- **StarGraph:** `stargraph serve` is a **headless** FastAPI HTTP + WebSocket
  daemon with triggers (manual/cron/webhook), a scheduler, profiles, and run
  history — but **no management console, no hosted deployment product, and no
  control plane.** You operate it yourself.

**Gap:** a turnkey managed platform with a control plane and one-click deploy.

### 9. Built-in observability and tracing

- **CrewAI:** **built-in tracing and telemetry** — every LLM call, tool call, and
  memory read is traceable with cost accounting, surfaced in the AMP control
  plane, and it integrates cleanly with OpenTelemetry-based observability stacks
  (SigNoz, W&B Weave, Langfuse, and others).
- **StarGraph:** strong on **forensic** tooling — audit logs, counterfactual
  replay, the `inspect` CLI, structural graph hashing — plus `GET /health` and
  a Prometheus-format `GET /metrics` on `stargraph serve`, but **no live
  tracing dashboard, no OTel-native span export, and no cost-accounting UI.**

**Gap:** live tracing dashboards and out-of-the-box OTel/cost telemetry
(Prometheus scraping only).

### 10. Reasoning and planning primitives

- **CrewAI:** agents support a **`reasoning=True`** mode for strategic planning
  before acting, and crews support **planning** (a planner generates a step-by-step
  plan that the crew follows). These are first-class, one-flag features.
- **StarGraph:** by *design* the LLM does not reason about routing — rules do.
  That is the thesis, but it also means there are **no built-in reasoning or
  planning primitives** in CrewAI's sense; you assemble that behavior yourself
  from DSPy modules.

**Gap:** turnkey reasoning and planning toggles (partly philosophical, but a
shipped capability StarGraph lacks).

### 11. Multimodal agents

- **CrewAI:** setting `multimodal=True` auto-equips an agent with an image tool so
  it can process images alongside text (with some rough edges historically).
- **StarGraph:** **text-centric.** Classical-ML nodes can host a vision model, but
  there is **no first-class multimodal agent surface** — no built-in image/audio/
  video in or out.

**Gap:** a one-flag multimodal agent.

### 12. Training and iterative improvement

- **CrewAI:** a built-in **`train`** workflow lets you run a crew through
  iterations with human feedback so it refines prompts and behavior over time,
  plus a **`test`** command for evaluating crew performance.
- **StarGraph:** **no training loop and no built-in eval harness.** Improvement is
  manual — edit nodes, rules, and prompts, then replay. (StarGraph's replay is a
  real strength, but it is a debugging tool, not a feedback-driven training loop.)

**Gap:** a feedback-driven training loop and a crew-evaluation command.

### 13. Developer experience and time-to-first-agent

- **CrewAI:** a `crewai create` scaffold, YAML config for agents and tasks, a
  large cookbook, and a gentle "give an agent a role and a goal" mental model. A
  first crew runs in minutes.
- **StarGraph:** steeper. To be productive you must learn **CLIPS rules, Fathom,
  provenance-typed facts, the state↔facts boundary, packs, skills, and the IR.**
  Power for the right problem, but a real ramp.

**Gap:** time-to-first-agent and overall learning curve.

---

## Feature-gap matrix

| Capability | CrewAI | StarGraph |
| --- | :---: | :---: |
| Role-based multi-agent teams + delegation | ✅ | ❌ sub-graphs only |
| Hierarchical (manager) process | ✅ | ❌ |
| Event-driven Flows with conditional routing | ✅ | ⚠️ rules-only, CLIPS |
| 40+ prebuilt tools | ✅ | ❌ small registry |
| MCP support | ✅ | ✅ adapter (client) |
| Three-tier memory (short/long/entity) | ✅ | ⚠️ low-level only |
| Knowledge sources (PDF/CSV/JSON/custom) | ✅ | ⚠️ retrieval node only |
| No-code visual builder (Crew Studio) | ✅ | ❌ |
| Managed enterprise platform + control plane | ✅ | ❌ headless serve only |
| One-click managed deployment | ✅ | ❌ |
| Built-in tracing / telemetry / cost accounting | ✅ | ❌ replay/audit only |
| Reasoning + planning primitives | ✅ | ❌ (by design) |
| Multimodal agents | ✅ | ❌ |
| Training loop + crew test/eval | ✅ | ❌ |
| Large community / ecosystem | ✅ | ❌ |
| Stable public API | ✅ | ❌ alpha |
| Deterministic rule routing | ⚠️ Flows are deterministic but code-defined | ✅ |
| Provenance-typed facts | ❌ | ✅ |
| Counterfactual replay | ❌ | ✅ |
| Classical ML as first-class nodes | ❌ | ✅ |
| Air-gap / embedded-by-default posture | ⚠️ on-prem option | ✅ |

Legend: ✅ shipped · ⚠️ partial / lower-level · ❌ absent.

---

## Where StarGraph still wins (for honest framing)

This page is about CrewAI's advantages, but the trade is not free. StarGraph
keeps the things CrewAI does not offer:

- **Deterministic routing as the default.** Transitions are decided by
  Fathom/CLIPS rules over typed facts, not by an LLM and not by hand-wired code
  paths. The decision layer is inspectable, versioned, and free of stochastic
  drift. CrewAI's Flows are deterministic too, but routing in a Crew is the LLM's
  job; StarGraph makes the rule layer the single source of truth.
- **Provenance as a type.** Every fact carries
  `(origin, source, run_id, step, confidence, timestamp)`; `origin` is typed
  (`llm | tool | user | rule | model | external`). Trust is first-class — CrewAI
  has no equivalent.
- **Counterfactual replay.** Checkpoint pinning per transition plus structural
  graph hashing make deterministic re-execution from any step — with a mutated
  fact, rule, or node output — and a diff against the original run essentially
  free. CrewAI offers tracing, not replay.
- **Classical ML as first-class nodes.** Route on a cheap sklearn/XGBoost/ONNX
  model's confidence and fall back to an LLM only when it is unsure. CrewAI is
  LLM-centric and has no `MLNode` equivalent.
- **Air-gap posture.** Embedded-by-default stores (LanceDB / RyuGraph / SQLite)
  and an operator playbook for cleared/air-gapped/regulated deployments. CrewAI
  offers an on-prem enterprise option, but its default posture and ecosystem
  assume connectivity.

CrewAI is the better default for shipping collaborative multi-agent automations
fast, and for organizations that want a managed platform with a control plane.
StarGraph is the better — sometimes only — choice when you must *prove* what the
system did and why, replay it deterministically, route on classical ML, and run
it where CrewAI's ecosystem cannot go.

---

## Bottom line

- **Choose CrewAI** for the role-based multi-agent team paradigm, a large
  prebuilt toolkit, turnkey memory and knowledge, a no-code Studio, multimodal
  and reasoning toggles, a managed enterprise platform with built-in
  observability, and a very large community. For most teams, most of the time.
- **Choose StarGraph** when deterministic rule routing, provenance, counterfactual
  replay, classical-ML routing, or air-gap/regulated constraints are hard
  requirements — and you accept an alpha API, a smaller surface, and more
  assembly to get them.

---

## Sources

- [CrewAI — homepage](https://www.crewai.com/) ·
  [GitHub: crewAIInc/crewAI](https://github.com/crewAIInc/crewAI) ·
  [crewai on PyPI](https://pypi.org/project/crewai/)
- [CrewAI docs — Memory](https://docs.crewai.com/en/concepts/memory) ·
  [Agents](https://docs.crewai.com/en/concepts/agents) ·
  [Tools Overview](https://docs.crewai.com/en/tools/overview) ·
  [Multimodal Agents](https://docs.crewai.com/how-to/multimodal-agents)
- [CrewAI AMP / Enterprise introduction](https://docs.crewai.com/en/enterprise/introduction) ·
  [GitHub: crewAIInc/crewAI-tools](https://github.com/crewAIInc/crewAI-tools)
- [Built-in Tracing and Telemetry (DeepWiki)](https://deepwiki.com/crewAIInc/crewAI/6.2-built-in-tracing-and-telemetry) ·
  [CrewAI Observability with OpenTelemetry (SigNoz)](https://signoz.io/docs/crewai-observability/)
- [What Is CrewAI? A Practical 2026 Guide (Nerova)](https://nerova.ai/guides/what-is-crewai-practical-guide-2026-2) ·
  [CrewAI Platform Statistics 2026 (Panto)](https://www.getpanto.ai/blog/crewai-platform-statistics) ·
  [Wikipedia: CrewAI](https://en.wikipedia.org/wiki/CrewAI)
- StarGraph: in-repo `README.md`, `docs/`, `design-docs/`, and `src/stargraph/`
  (v0.4, this repository).
