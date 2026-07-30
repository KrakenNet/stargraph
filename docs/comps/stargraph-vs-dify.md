# StarGraph vs Dify

This is a candid, gap-focused comparison: what does Dify do that StarGraph does not? It is intentionally one-sided. The two tools also sit in different categories. Dify is a no-code/low-code **LLM application platform** — a visual product for building, deploying, and operating chatbots, RAG apps, and agentic workflows, used heavily by non-developers and product teams. StarGraph is a **stateful agent-graph framework** for Python engineers, built around deterministic rule-based routing and provenance. They overlap on the substance (agents, RAG, tools, serving), but Dify is not trying to be a typed, replayable engine, and StarGraph is not trying to be a drag-and-drop app builder with an ops console. This document compares them on the axes where they genuinely overlap and is honest about where each simply is not playing the same game.

> **Versions compared:** StarGraph v0.4 (alpha, public API unstable until v1.0) vs Dify v1.14.x (stable, weekly releases, ~146k GitHub stars as of mid-2026).

## TL;DR — different design centers

| | Dify | StarGraph |
|---|---|---|
| **Design center** | No-code/low-code LLM app platform: visual canvas, RAG UI, ops console | Stateful agent-graph framework for Python engineers |
| **Routing / control model** | Visual graph with explicit edges; LLM agent nodes route via function-calling/ReAct | Rule-routed: no static edges, no LLM router; a CLIPS forward-chaining engine matches typed facts |
| **Target user** | Product teams, non-developers, app builders; developers via API | Python engineers building governed, auditable agent systems |
| **Maturity** | Mature, widely deployed (~1M apps reported), large community | Alpha, small community, unstable API |
| **Core bet** | Lower the barrier to building and shipping LLM apps to near-zero | Make the decision layer deterministic, inspectable, and replayable |

## Where Dify is ahead

### 1. Visual workflow builder

- **Dify:** A drag-and-drop canvas where you assemble nodes (LLM, knowledge retrieval, code, HTTP, conditional, iteration, agent) into Workflows and Chatflows, see them laid out graphically, and run/debug them live in the browser. Non-developers can build a working app without writing code.
- **StarGraph:** Authoring is in Python or a compiled YAML subset. There is no visual canvas, no node palette, no live graph editing. You write typed code and rules.

**Gap:** StarGraph has no visual builder at all — its README states a UI is "a future product." Building a graph requires Python/YAML fluency.

### 2. Two app archetypes tuned for chat and automation

- **Dify:** Ships distinct application types — **Chatflow** (multi-turn conversational apps with memory and a chat UI), **Workflow** (single-shot automation pipelines), **Agent**, and **Text Generator** — each with an interaction pattern and UI prebuilt for it.
- **StarGraph:** One runtime model — a graph executed by the engine. There is no built-in conversational app type, no shipped chat surface, no "text generator" preset. You build conversation handling yourself.

**Gap:** StarGraph offers no prebuilt chat application, no conversation-memory UX, and no end-user-facing app shell.

### 3. Prebuilt chat / app UI for end users

- **Dify:** Every app gets a hosted, shareable web UI (a chat window or form) out of the box, plus an embeddable web widget and a WebApp. You can hand a working interface to end users immediately.
- **StarGraph:** `stargraph serve` is **headless** — a FastAPI HTTP + WebSocket API only. There is no chat UI, no web widget, no embeddable front end.

**Gap:** StarGraph ships zero front end. To put anything in front of a human you must build the entire UI layer yourself.

### 4. RAG / knowledge pipeline with a full UI

- **Dify:** End-to-end RAG through a UI — upload PDFs/PPTX/docs or sync from Notion and websites, then chunk, embed, index, and retrieve, all configured in the browser. The newer **Knowledge Pipeline** makes ingestion a visual, node-based, pluggable flow (parse → chunk → embed → store), with data-source plugins like Tavily for live web data. Datasets, retrieval testing, and reranking are all point-and-click.
- **StarGraph:** Has a `RetrievalNode`, a `VectorStore` protocol (LanceDB), and a `GraphStore` (RyuGraph), but no ingestion UI, no dataset manager, no visual chunking/embedding pipeline, no retrieval-testing console. You wire and tune retrieval in code.

**Gap:** StarGraph has retrieval primitives but no knowledge-management product — no document upload, no dataset UI, no visual ingestion pipeline.

### 5. Hundreds of model providers out of the box

- **Dify:** Integrates hundreds of proprietary and open-source models across dozens of inference providers (OpenAI, Anthropic, Mistral, Llama, Azure, Bedrock, local/self-hosted, and any OpenAI-API-compatible endpoint), selectable from a dropdown with per-model config, and extensible via **Model plugins**.
- **StarGraph:** All LLM calls go **through DSPy**. There is no first-class multi-provider catalog and no provider-picker UI; you get whatever DSPy/LiteLLM exposes, configured in code.

**Gap:** StarGraph has no curated multi-provider model catalog and no UI to add/switch providers or load-balance across them.

### 6. Plugin marketplace and ecosystem

- **Dify:** A live **Marketplace** with multiple plugin classes — **Tool, Model, Extension, Agent Strategy, Datasource, Trigger** — plus **Bundles** for one-click installation of plugin collections, a Creator Center, and a template marketplace for sharing whole apps. 50+ built-in tools ship by default (Google Search, DALL·E, WolframAlpha, etc.).
- **StarGraph:** Has a plugin model (pip-installable packages shipping skills/tools/nodes/stores via entry points) and reference skills (RAG, ReAct, triage, sql_analyst, etc.), but **no marketplace, no discovery UI, no one-click install, and no curated catalog of integrations.**

**Gap:** StarGraph has no marketplace, no integration catalog, and no template-sharing ecosystem. Capability comes from writing or pip-installing packages.

### 7. Large integration / tool library

- **Dify:** 50+ built-in tools plus a continually growing marketplace of third-party tool plugins, so most common SaaS and API integrations already exist.
- **StarGraph:** Tools are defined in code with JSON Schema, namespaces, and permission/side-effect flags — a clean model, but the shipped library is small and there is no prebuilt connector catalog.

**Gap:** StarGraph ships no large connector/integration library; you build integrations from scratch.

### 8. Reasoning / agent strategies as a first-class feature

- **Dify:** **Agent Strategy** plugins let an Agent node use configurable reasoning strategies — Function Calling, ReAct, Chain-of-Thought, Tree-of-Thoughts — for autonomous multi-step tool selection and execution, chosen from a menu.
- **StarGraph:** Deliberately has **no built-in reasoning primitives for routing** — by design the LLM does not reason about what happens next; rules do. You can implement a ReAct-style loop inside a sub-graph, but there is no catalog of swappable agent reasoning strategies.

**Gap:** StarGraph offers no menu of LLM reasoning strategies and, by design, keeps the LLM out of the routing decision entirely.

### 9. Prompt IDE and in-browser iteration

- **Dify:** A **Prompt IDE** for crafting prompts, comparing model outputs side by side, and iterating without redeploying. Combined with the live canvas, the whole build-test loop happens in the browser.
- **StarGraph:** Prompts live in DSPy modules in code. There is no prompt-editing UI, no side-by-side model comparison, and no in-browser iteration loop.

**Gap:** StarGraph has no prompt IDE and no interactive prompt/model comparison tooling.

### 10. Observability, logging, and annotation UI

- **Dify:** Built-in LLMOps — run logs, token usage, latency, and cost over time in a dashboard — plus an annotation system (annotate responses, set reply score thresholds, build datasets from production traffic) and native tracing integrations with Langfuse, Opik, and Arize Phoenix.
- **StarGraph:** Has run history, `stargraph inspect`/`replay` over checkpoints, and `GET /health` + `GET /metrics` (Prometheus text format) on the serve surface, but **no ops dashboard, no live tracing UI, no annotation system, and no built-in eval framework.** Observability is CLI/API/scrape-level, not a console.

**Gap:** StarGraph has no observability dashboard, no annotation/feedback UI, and no built-in evaluation framework.

### 11. Backend-as-a-service with a turnkey app surface

- **Dify:** Every app exposes a documented REST API automatically, but the value is the whole bundle — API **plus** hosted UI, dataset management, conversation storage, and an admin console — so you can ship a complete product, not just an endpoint.
- **StarGraph:** Exposes an OpenAPI 3.1 HTTP + WebSocket API (genuinely strong on the API axis: triggers, scheduler, run history, HITL), but it is **API-only** — no admin console, no app management UI, no conversation/dataset management surface.

**Gap:** StarGraph gives you the API but none of the surrounding product surface (admin console, app/dataset management, hosted apps).

### 12. Maturity, community, and ecosystem

- **Dify:** ~146k GitHub stars, 800+ contributors, weekly releases, a stable 1.x API, reportedly ~1M deployed applications, commercial enterprise edition (SSO, multi-workspace, RBAC), and abundant tutorials, templates, and third-party content.
- **StarGraph:** Alpha (v0.4), public API explicitly unstable until v1.0, a small community, no marketplace, and a steep learning curve (CLIPS, Fathom, provenance facts, the state↔facts boundary, rule packs, the YAML→IR compiler).

**Gap:** StarGraph is early and niche — far smaller community, no stable API, no enterprise edition, and a much steeper on-ramp.

### 13. Enterprise edition and team/workspace management

- **Dify:** A commercial Enterprise edition adds SSO, multi-workspace management, advanced permissions/RBAC, load balancing, and SLAs on top of the self-hostable Community edition.
- **StarGraph:** Open-source (Apache-2.0) with no commercial enterprise tier, no SSO, no workspaces, and no built-in multi-tenant RBAC.

**Gap:** StarGraph has no enterprise edition, no SSO/RBAC, and no workspace/multi-tenant management.

## Feature-gap matrix

| Capability | Dify | StarGraph |
|---|---|---|
| Visual workflow builder | ✅ | ❌ |
| Prebuilt chat / app UI for end users | ✅ | ❌ |
| Chatflow + Workflow app archetypes | ✅ | ⚠️ (one runtime model, code) |
| RAG knowledge pipeline with UI | ✅ | ⚠️ (retrieval primitives, no UI) |
| Hundreds of model providers (catalog) | ✅ | ⚠️ (via DSPy, no catalog/UI) |
| Plugin marketplace | ✅ | ❌ (entry-point plugins, no market) |
| Large built-in tool/integration library | ✅ | ⚠️ (small, code-defined) |
| Agent reasoning strategies (CoT/ToT/ReAct menu) | ✅ | ⚠️ (sub-graph, by-design no LLM routing) |
| Prompt IDE / in-browser iteration | ✅ | ❌ |
| Observability / logging dashboard | ✅ | ⚠️ (CLI inspect/replay, no console) |
| Annotation / feedback UI | ✅ | ❌ |
| Built-in eval framework | ✅ | ❌ |
| Backend-as-a-service API | ✅ | ✅ (OpenAPI, headless) |
| Enterprise edition (SSO/RBAC/workspaces) | ✅ | ❌ |
| Self-hostable | ✅ | ✅ |
| Deterministic rule-based routing | ❌ | ✅ |
| Provenance-typed facts | ❌ | ✅ |
| Counterfactual replay | ❌ | ✅ |
| Classical ML (sklearn/XGBoost/ONNX) as nodes | ❌ | ✅ |
| Air-gap / regulated deployment posture | ⚠️ (self-host, enterprise license) | ✅ |

Legend: ✅ shipped / first-class · ⚠️ partial or lower-level · ❌ absent.

## Where StarGraph still wins (for honest framing)

- **Deterministic rule routing.** Transitions come from a CLIPS forward-chaining engine matching typed facts — inspectable, versioned, and free of stochastic drift. Dify's control flow mixes explicit edges with LLM agent nodes that route via function-calling; the routing decision can be non-deterministic.
- **Provenance-typed facts.** Every fact carries `(origin, source, run_id, step, confidence, timestamp)` with a typed origin (`llm | tool | user | rule | model | external`). Trust is a first-class type. Dify has no equivalent typed-trust model in working memory.
- **Counterfactual replay.** Checkpoint pinning plus structural graph hashing make deterministic replay essentially free — re-run from any step with a mutated rule, node output, or fact and diff against the original. Dify offers logs and annotation, not branch-and-diff replay.
- **Classical ML as first-class nodes.** sklearn / XGBoost / PyTorch / ONNX run alongside LLM modules, so you can route on a cheap model's confidence and only escalate to an LLM when unsure. Dify is LLM-centric and has no classical-ML node.
- **Air-gap posture.** Embedded-by-default stores, an operator playbook, and model staging target cleared / air-gapped / regulated (DoD, finance, healthcare) deployments. Dify self-hosts, but its richer feature set assumes far more moving infrastructure and gates SSO/compliance behind a commercial license.

## Bottom line

- **Choose Dify** when you want to build and ship LLM apps fast — chatbots, RAG assistants, agentic workflows — with a visual builder, a hosted UI, broad model and tool coverage, an ops console, and a large community, especially when non-developers are part of the build and the routing being LLM-driven is acceptable.
- **Choose StarGraph** when you are a Python engineer who needs the decision layer to be deterministic, auditable, and replayable — typed provenance on every fact, rule-based routing with no LLM in the loop, counterfactual replay, classical ML alongside LLMs, and an air-gap-friendly posture for regulated environments — and you are willing to write code and accept an alpha API to get it.

## Sources

- [Dify GitHub repository (langgenius/dify)](https://github.com/langgenius/dify)
- [Dify v1.14.2 release notes](https://github.com/langgenius/dify/releases/tag/1.14.2)
- [Dify releases](https://github.com/langgenius/dify/releases)
- [Dify 1.9.0 — Orchestrating Knowledge, Powering Workflows (discussion)](https://github.com/langgenius/dify/discussions/26138)
- [Introducing Knowledge Pipeline — Dify Blog](https://dify.ai/blog/introducing-knowledge-pipeline)
- [Knowledge Pipeline Plugin Ecosystem — Dify Blog](https://dify.ai/blog/knowledge-pipeline-plugin-ecosystem-co-build-enterprise-grade-rag-with-global-partners)
- [Dify x Tavily: Knowledge Pipelines from Live Web Data — Dify Blog](https://dify.ai/blog/dify-x-tavily-build-knowledge-pipelines-from-live-web-data)
- [Introducing Dify Plugins — Dify Blog](https://dify.ai/blog/introducing-dify-plugins)
- [Dify Marketplace](https://marketplace.dify.ai/)
- [Agent Strategy Plugin — Dify Docs](https://docs.dify.ai/plugins/quick-start/develop-plugins/agent-strategy-plugin)
- [Model Plugin — Dify Docs](https://docs.dify.ai/plugins/quick-start/develop-plugins/model-plugin)
- [Self-hosted plan differences (Community / Premium / Enterprise) — Dify discussion](https://github.com/langgenius/dify/discussions/32254)
- [Langfuse observability integration for Dify](https://langfuse.com/integrations/no-code/dify)
