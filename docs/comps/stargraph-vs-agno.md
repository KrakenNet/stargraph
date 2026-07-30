# StarGraph vs Agno

A candid, gap-focused comparison. This page exists to answer one question
honestly: **what does [Agno](https://www.agno.com/) do that StarGraph does
not?** It is deliberately one-sided — StarGraph's own advantages (deterministic
rule routing, provenance, replay, air-gap posture) are summarized only briefly
at the end. If you are choosing a framework and Agno is on your shortlist, read
this as the list of things you would be giving up.

> **Versions compared:** StarGraph v0.4 (alpha, public API unstable) vs Agno
> v2.5+ (June 2026). Agno was formerly Phidata; it relicensed from MPL-2.0 to
> Apache-2.0. Both projects are Apache-2.0 today.

---

## TL;DR — different design centers

| | **StarGraph** | **Agno** |
| --- | --- | --- |
| Design center | Determinism, auditability, provenance, replay | Speed, breadth, developer ergonomics, production scale |
| Routing | Rules (Fathom/CLIPS) over typed facts | The agent (LLM) reasons and decides |
| Target user | Regulated / cleared / air-gapped (DoD, finance, healthcare) | General Python teams shipping agents fast |
| Maturity | v0.4 alpha, single-org | v2.5+, ~40k★, 420+ contributors |
| Bet | "Rules, not LLMs, decide what happens next" | "Make the agent fast, capable, and easy to run" |

Agno wins on almost every axis a typical team scores on day one: breadth,
speed, polish, ecosystem, and time-to-first-agent. StarGraph wins on a narrow
set of axes (provability, replay, classical-ML routing, air-gap) that most teams
do not need — but the few that do cannot get from Agno. **The rest of this page
is the day-one list.**

---

## Where Agno is ahead

### 1. Maturity and ecosystem

The single biggest gap. It is not close.

| | StarGraph | Agno |
| --- | --- | --- |
| GitHub stars | — (private/early) | ~40,000 (May 2026) |
| Contributors | Single org (Kraken Networks) | 420+ |
| Releases | v0.4 alpha, API unstable until v1.0 | v2.5+, stable, semver |
| Examples / cookbook | Handful of in-tree demos | Large official cookbook + community |
| Third-party content | Effectively none | Tutorials, courses, videos, blog posts |
| Hiring / Stack Overflow surface | None | Substantial |

**What StarGraph lacks:** community, battle-testing, a deep example library, and
the assurance that the API you build on today will exist next quarter. For most
teams this alone decides it.

### 2. Model-provider breadth

- **Agno:** model-agnostic with **30+ first-class provider integrations**
  (OpenAI, Anthropic, Google, AWS Bedrock, Azure, Groq, Mistral, Ollama, local,
  etc.), each a documented, supported adapter.
- **StarGraph:** LLM calls go through **DSPy**. You get whatever DSPy/LiteLLM
  reaches, but there is **no first-class provider catalog**, no per-provider
  docs, and the integration story is "configure DSPy." Multimodal and
  provider-specific features are not surfaced.

**Gap:** turnkey, documented, broad provider support.

### 3. Prebuilt tools and toolkits

- **Agno:** **100+ prebuilt toolkits** (search, web, finance, databases, files,
  Slack, GitHub, …) plus MCP. Wire a tool in one line.
- **StarGraph:** a tool registry, an MCP adapter, and a few in-tree tools
  (Nautilus broker, etc.). **No large prebuilt toolkit library.** You write or
  wrap most tools yourself.

**Gap:** breadth of ready-made integrations.

### 4. Knowledge / vector-store breadth

- **Agno:** **15–20+ vector-store integrations** (Pinecone, Weaviate, Qdrant,
  pgvector, …) with **hybrid search, reranking, and chunking out of the box**,
  plus an Agentic RAG pattern and loaders/chunkers/embedders/rerankers as
  first-class pipeline parts.
- **StarGraph:** clean `VectorStore` / `GraphStore` / `DocStore` Protocols, but
  the **shipped concrete providers are narrow** — LanceDB (vector), RyuGraph
  (graph), SQLite (doc/memory/fact). Embedded-by-default is great for air-gap;
  it means **few hosted/managed backends are provided**.

**Gap:** number of ready store backends and turnkey hybrid-search/rerank
pipelines.

### 5. Multimodal

- **Agno:** **native text, image, audio, video, and file** input *and* output,
  with **structured (Pydantic) output for each modality**.
- **StarGraph:** **text-centric.** Classical-ML nodes can host vision models, but
  there is **no first-class multimodal agent surface** — no built-in image/audio/
  video in-or-out, no per-modality structured output.

**Gap:** multimodal is a non-feature in StarGraph today.

### 6. Built-in reasoning primitives

- **Agno:** first-class **reasoning** — chain-of-thought, reasoning models, and
  reasoning tools the agent uses to deliberate before answering.
- **StarGraph:** by *design* the LLM does not reason about routing (rules do).
  That is the thesis, but it also means there are **no built-in reasoning
  primitives** if you want an agent that thinks step-by-step in the Agno sense.
  You assemble that yourself from DSPy modules.

**Gap:** turnkey reasoning patterns (this is partly philosophical, but it is a
capability Agno ships and StarGraph does not).

### 7. Memory ergonomics

- **Agno:** built-in **cross-session user memory** with optimization strategies,
  agentic memory, and session management that "just works" across many
  concurrent users.
- **StarGraph:** has `MemoryStore` + `MemoryWriteNode` (SQLite), but memory is a
  **lower-level primitive**, not a turnkey "remember this user across sessions"
  feature. More assembly required.

**Gap:** out-of-the-box user-memory UX.

### 8. Multi-agent teams and agent-to-agent collaboration

- **Agno:** first-class **Teams** with route / coordinate / collaborate modes,
  agents that delegate to one another, HITL for teams, and support for the
  **A2A (Agent-to-Agent)** protocol.
- **StarGraph:** composition is **sub-graphs** (`SubGraphNode`, skills-as-
  subgraphs). There is **no peer-agent team abstraction, no delegation model,
  and no A2A**. Multi-agent means "nest a graph," not "a team of agents talks."

**Gap:** the entire team/collaboration paradigm and A2A interop.

### 9. Raw performance and footprint

- **Agno:** **~2–3 µs** agent instantiation and **~2.5–6.5 KB** memory per agent
  — published, reproducible benchmarks (~10,000× faster instantiation and ~50×
  less memory than LangGraph on the same test).
- **StarGraph:** **no published performance numbers.** The runtime is heavier by
  construction — a CLIPS engine in the loop and a checkpoint written **per
  transition**. StarGraph trades wall-clock and memory for determinism and
  auditability.

**Gap:** speed, footprint, and the benchmarks to prove them. If you spin up many
agents dynamically or care about cold-start at scale, Agno is in a different
class.

### 10. Production runtime + Control Plane UI

- **Agno (AgentOS):** a **pre-built stateless FastAPI runtime** plus a
  **web Control Plane UI** (`os.agno.com`) for testing, monitoring, and managing
  deployments — sessions, traces, agents, teams, workflows — with **JWT-based
  RBAC, per-session isolation, multi-user, scheduling, and audit logs** built in.
  The browser talks directly to your runtime (data sovereignty); deploy as a
  Docker container in your own cloud.
- **StarGraph (`stargraph serve`):** FastAPI HTTP + WebSocket with triggers
  (manual/cron/webhook), profiles, and run history — but **explicitly headless.
  "UI is a future product."** No management console, weaker multi-tenant /
  RBAC story.

**Gap:** a shipped operations UI and a turnkey multi-user, RBAC-enforced runtime.

### 11. Observability, tracing, and evals

- **Agno:** **built-in tracing** of every step/decision, surfaced in the Control
  Plane, plus an **evaluation framework** (accuracy / performance / reliability
  evals).
- **StarGraph:** strong on **forensic** tooling — audit logs, counterfactual
  replay, `inspect` CLI, structural graph hashing — plus a serve-level ops
  surface: `GET /health` (per-component readiness) and `GET /metrics`
  (Prometheus text format). Still **no OTel-style tracing, no dashboards, and
  no built-in agent-eval harness.**

**Gap:** live tracing dashboards and an eval framework (a Prometheus metrics
endpoint is not a Control Plane).

### 12. Interop protocols and a prebuilt chat UI

- **Agno:** **MCP + A2A + AG-UI**. AG-UI gives you a ready agent-to-user event
  stream and a **prebuilt chat / Agent UI**.
- **StarGraph:** **MCP adapter only.** No A2A, no AG-UI, **no prebuilt chat UI**.

**Gap:** A2A/AG-UI interop and a ready frontend.

### 13. Developer experience and onboarding

- **Agno:** "single agent in minutes," minimal concepts, async-first, streaming
  first-class, huge docs.
- **StarGraph:** steeper. To be productive you must learn **CLIPS rules, Fathom,
  provenance-typed facts, the state↔facts boundary, packs, skills, and the IR**.
  Power for the right problem, but a real ramp.

**Gap:** time-to-first-agent and learning curve.

---

## Feature-gap matrix

| Capability | Agno | StarGraph |
| --- | :---: | :---: |
| 30+ model providers, first-class | ✅ | ⚠️ via DSPy, no catalog |
| 100+ prebuilt toolkits | ✅ | ❌ small registry |
| 15–20+ vector stores + hybrid/rerank | ✅ | ⚠️ few embedded backends |
| Native multimodal (image/audio/video) | ✅ | ❌ |
| Built-in reasoning primitives | ✅ | ❌ (by design) |
| Turnkey cross-session user memory | ✅ | ⚠️ low-level only |
| Multi-agent teams + delegation | ✅ | ❌ sub-graphs only |
| A2A protocol | ✅ | ❌ |
| AG-UI + prebuilt chat UI | ✅ | ❌ |
| Published perf benchmarks (µs / KB) | ✅ | ❌ |
| Prebuilt FastAPI runtime | ✅ | ✅ headless |
| Web Control Plane / ops UI | ✅ | ❌ future |
| JWT RBAC, multi-user, scheduling | ✅ | ⚠️ partial |
| Live tracing dashboards | ✅ | ❌ replay/audit + /metrics only |
| Built-in eval framework | ✅ | ❌ |
| Large community / ecosystem | ✅ | ❌ |
| Stable public API | ✅ | ❌ alpha |

Legend: ✅ shipped · ⚠️ partial / lower-level · ❌ absent.

---

## Where StarGraph still wins (for honest framing)

This page is about Agno's advantages, but the trade is not free. StarGraph keeps
the things Agno structurally cannot offer:

- **Deterministic routing.** Transitions are decided by Fathom/CLIPS rules over
  typed facts, not by an LLM. Inspectable, versioned, free of stochastic drift.
- **Provenance as a type.** Every fact carries
  `(origin, source, run_id, step, confidence, timestamp)`; `origin` is typed
  (`llm | tool | user | rule | model | external`). Trust is first-class.
- **Counterfactual replay.** Checkpoint pinning + structural graph hashing make
  deterministic re-execution from any step (with a mutated fact/rule/output) and
  a diff against the original run essentially free.
- **Classical ML as first-class nodes.** Route on a cheap sklearn/XGBoost/ONNX
  model's confidence; fall back to an LLM only when it is unsure.
- **Air-gap posture.** Embedded-by-default stores and an operator playbook for
  cleared/air-gapped/regulated deployments.

Agno is the better default for shipping capable agents fast. StarGraph is the
better — sometimes only — choice when you must *prove* what the system did and
why, replay it deterministically, and run it where the Agno ecosystem cannot go.

---

## Bottom line

- **Choose Agno** for breadth, speed, multimodal, teams, a polished production
  runtime + UI, and a large ecosystem. For most teams, most of the time.
- **Choose StarGraph** when determinism, provenance, replay, classical-ML
  routing, or air-gap/regulated constraints are hard requirements — and you
  accept an alpha API, a smaller surface, and more assembly to get them.

---

## Sources

- [Agno — homepage](https://www.agno.com/) ·
  [Agent framework](https://www.agno.com/agent-framework) ·
  [AgentOS](https://www.agno.com/agentos) ·
  [Docs](https://docs.agno.com/) ·
  [Performance](https://docs.agno.com/performance)
- [AgentOS Control Plane](https://docs.agno.com/agent-os/control-plane) ·
  [Control Plane UI (DeepWiki)](https://deepwiki.com/agno-agi/agno/7.12-control-plane-ui)
- [Multimodal Agents](https://docs.agno.com/multimodal/agent/overview)
- [Agno: Production-Ready AI Agent Framework (39k+ Stars)](https://www.decisioncrafters.com/agno-ai-agent-framework-39k-stars/) ·
  [Community Roundup, Feb 2026](https://www.agno.com/blog/community-roundup-february-2026)
- [Agno vs LangGraph (ZenML)](https://www.zenml.io/blog/agno-vs-langgraph) ·
  [Understanding Agno (DigitalOcean)](https://www.digitalocean.com/community/conceptual-articles/agno-fast-scalable-multi-agent-framework) ·
  [Agno for Python teams (WorkOS)](https://workos.com/blog/agno-the-agent-framework-for-python-teams)
- StarGraph: in-repo `README.md`, `docs/`, `design-docs/`, and `src/stargraph/`
  (v0.4, this repository).
