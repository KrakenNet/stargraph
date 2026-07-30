# StarGraph vs Google ADK

This is a candid, gap-focused comparison: it exists to enumerate what Google's Agent Development Kit (ADK) does that StarGraph does not. It is intentionally one-sided. Both projects are genuine agent frameworks that overlap heavily — multi-agent orchestration, tool calling, model-backed nodes, deployment as a service — so the comparison is fair on most axes. The honest asymmetry is one of scale and reach: ADK is a Google-backed, multi-language, multimodal, cloud-native platform with a large ecosystem, while StarGraph is a single-team, Python-only, alpha framework with a narrow but distinctive thesis around deterministic rule routing and provenance. Where ADK is simply bigger, this doc says so plainly. Where StarGraph genuinely has something ADK lacks, the "still wins" section near the end covers it.

> **Versions compared:** StarGraph v0.4 (alpha, public API unstable until v1.0) vs Google ADK v2.3.0 (Python; released June 2026, ~20k GitHub stars, Apache-2.0). ADK also ships parallel Java, TypeScript, Go, and Kotlin implementations.

## TL;DR — different design centers

| | Google ADK | StarGraph |
|---|---|---|
| **Design center** | Production multi-agent platform tied to (but not locked to) the Google Cloud / Gemini stack | Stateful agent-graph framework with deterministic governance |
| **Routing / control model** | Code-first orchestration: workflow agents (Sequential/Parallel/Loop), LLM-driven dynamic delegation, and an ADK 2.0 graph workflow runtime | No static edges and no LLM router — a CLIPS forward-chaining rules engine (Fathom) derives transitions from typed facts at runtime |
| **Target user** | Teams building and shipping agents on Google Cloud (or anywhere via LiteLLM), across five languages | Python teams that need inspectable, replayable, audit-grade decision logic — often regulated or air-gapped |
| **Maturity** | Mature and fast-moving: v2.x, bi-weekly releases, large samples/ecosystem, Google-backed | Alpha (v0.4), unstable API, small team, embedded-by-default |
| **Core bet** | A broad, cloud-native, multimodal platform with first-class deployment, evaluation, and a dev UI wins on developer reach and time-to-production | Splitting the LLM (worker) from the router (rules) makes agent control deterministic, versioned, and counterfactually replayable |

## Where Google ADK is ahead

### 1. Multimodal bidirectional streaming (audio + video)

- **ADK:** First-class bidirectional streaming built on the Gemini Live API. Agents can take continuous audio and video input and respond with low latency over a two-way channel — true real-time multimodal interaction, with a development-to-production path (local Gemini API key, then Vertex AI Agent Engine) that doesn't change application code.
- **StarGraph:** Text-centric. ML nodes can host a vision model, but there is no first-class image/audio/video agent surface and no streaming voice/video channel.

**Gap:** StarGraph has no native multimodal or live audio/video streaming story at all.

### 2. Agent-to-agent interoperability (A2A protocol)

- **ADK:** Native support for the Agent2Agent (A2A) protocol — an open, vendor-neutral standard for cross-framework agent communication. ADK agents publish a `.well-known/agent.json` agent card, expose a standard run endpoint, and can discover and delegate to agents written in any framework over A2A.
- **StarGraph:** Has an MCP client adapter but no A2A protocol. Composition is internal (nested sub-graphs); there is no wire protocol for one StarGraph agent to discover and call an external, independently-deployed agent.

**Gap:** StarGraph cannot interoperate with the broader A2A agent ecosystem and has no peer-to-peer agent discovery/delegation protocol.

### 3. Multi-agent teams and hierarchical delegation

- **ADK:** Built ground-up for multi-agent systems. Hierarchical composition, LLM-driven delegation between specialized agents, a Task API for structured agent-to-agent delegation with multi-turn modes, and "agent as a tool" patterns (including wrapping LangGraph/CrewAI agents). This is a true team/peer-delegation paradigm.
- **StarGraph:** Offers agents-as-subgraphs (`SubGraphNode`) — nesting, not peer teams. There is no team abstraction, no dynamic peer delegation, and no autonomous handoff between coequal agents.

**Gap:** StarGraph has no multi-agent team / peer-delegation model; it only nests sub-graphs inside one rule-routed graph.

### 4. Model breadth and a deep tool ecosystem

- **ADK:** Model-agnostic via LiteLLM (Gemini, Claude, Llama, Mistral, AI21, and anything in Vertex Model Garden), plus a rich tool ecosystem: built-in Google Search, Code Execution, Computer Use, Vertex AI Search, and GCP integrations (BigQuery, Spanner, Bigtable, Pub/Sub), MCP tools, OpenAPI tools, and third-party libraries (LangChain, LlamaIndex) used directly.
- **StarGraph:** LLM access goes through DSPy; there is no first-class multi-provider model catalog. It ships reference skills (RAG, ReAct, triage, sql_analyst, extract, digest) but no large prebuilt toolkit, connector library, or built-in web-search/code-exec/computer-use tools.

**Gap:** StarGraph lacks a broad model catalog and a large library of prebuilt tools, connectors, and managed integrations.

### 5. Built-in evaluation framework

- **ADK:** Ships an evaluation framework — `AgentEvaluator.evaluate()` and an `adk eval` CLI — to validate both final responses and the step-by-step execution path against test cases, integrated into the dev UI's evaluations tab.
- **StarGraph:** No built-in eval framework. It has deterministic replay and simulation (which help testing), but no first-class harness for scoring agent quality against datasets.

**Gap:** StarGraph has no built-in evaluation/scoring framework for agent outputs and trajectories.

### 6. Developer web UI, visual builder, and live tracing

- **ADK:** A built-in `adk web` developer UI: interactive chat, an events/trace explorer with step-by-step execution traces, artifacts, evaluations, and an AI-assisted agent builder / visual builder. Plus Cloud Trace integration for end-to-end observability in production.
- **StarGraph:** Headless only. The README states a UI is "a future product." There is no web console, no visual builder, and no live tracing dashboard — inspection happens via the CLI (`inspect`, `replay`).

**Gap:** StarGraph has no web/ops UI, no visual builder, and no live tracing dashboard.

### 7. One-command enterprise deployment

- **ADK:** First-class deployment to Vertex AI Agent Engine (managed runtime), Cloud Run (containerized, autoscaled, public HTTPS endpoint), and GKE — with built-in authentication and enterprise security, driven by a single CLI command. An agent-starter-pack adds CI/CD, evaluation, and observability templates.
- **StarGraph:** Ships `stargraph serve` (a headless FastAPI HTTP + WebSocket daemon with OpenAPI 3.1, triggers, scheduler, run history, and HITL) with a container path — official multi-stage `Dockerfile`, `compose.yaml`, and a single-replica Helm chart (`deploy/helm/stargraph/`, StatefulSet + PVC) — but no managed cloud runtime, no one-command cloud deploy, and no autoscaling (single-replica is the supported topology by design).

**Gap:** StarGraph has no managed runtime or one-command cloud deployment; you operate the container/process yourself.

### 8. Multi-language reach

- **ADK:** Available in Python, TypeScript, Go, Java, and Kotlin from a single project, letting JVM, Node, and Go shops adopt it without leaving their stack.
- **StarGraph:** Python 3.13 only. (There is a Go predecessor, Railyard, but it is a separate project, not a supported SDK.)

**Gap:** StarGraph is Python-only; ADK meets teams in five languages.

### 9. Callbacks, guardrails, and observability integrations

- **ADK:** A callback system intercepts agent/tool/model lifecycle points for guardrails, safety policies, and plugging in third-party observability tools, with first-party Cloud Trace tracing.
- **StarGraph:** Governance is real but different in shape — Bosun reference rule packs (budgets, retries, safety, audit) are Fathom rules mounted declaratively onto a graph. That is powerful for policy, but there is no broad ecosystem of observability/tracing integrations and no large catalog of prebuilt safety connectors.

**Gap:** StarGraph lacks the breadth of observability/guardrail integrations and the lifecycle-callback surface ADK exposes.

### 10. Community, ecosystem, and stability

- **ADK:** Google-backed, ~20k stars, bi-weekly releases, large sample-agent collections, a starter pack, codelabs, and "awesome" community lists. Stable, versioned public API (v2.x).
- **StarGraph:** Small single-team project, alpha API explicitly unstable until v1.0, limited samples, and a steep learning curve (CLIPS, Fathom, provenance facts, the state↔facts boundary, rule packs, the YAML→IR compiler).

**Gap:** StarGraph has a small community, an unstable alpha API, and a steeper learning curve than a Google-backed, widely-adopted framework.

### 11. Context management

- **ADK:** ADK 2.0 treats context "like source code," automatically filtering irrelevant data and managing token efficiency across a graph workflow — a managed-context feature aimed at long, complex runs.
- **StarGraph:** Provides explicit, typed state and a working-memory fact store, but no automatic context-window curation/compaction layer; prompt context is the node author's responsibility (via DSPy).

**Gap:** StarGraph has no automatic context-window management/compaction.

## Feature-gap matrix

| Capability | Google ADK | StarGraph |
|---|---|---|
| Multimodal audio/video bidirectional streaming | ✅ | ❌ |
| A2A cross-framework agent protocol | ✅ | ❌ |
| Multi-agent teams / peer delegation | ✅ | ⚠️ (nested sub-graphs only) |
| Model-agnostic provider catalog | ✅ | ⚠️ (via DSPy, no first-class catalog) |
| Large prebuilt tool / connector library | ✅ | ⚠️ (reference skills only) |
| Built-in evaluation framework | ✅ | ❌ |
| Web dev UI / visual builder | ✅ | ❌ (headless; UI is "future") |
| Live tracing dashboard | ✅ | ⚠️ (CLI inspect/replay only) |
| One-command managed cloud deployment | ✅ | ⚠️ (self-hosted `serve`; Docker/compose/Helm provided) |
| Multi-language SDKs | ✅ | ❌ (Python only) |
| Callbacks / guardrails / observability integrations | ✅ | ⚠️ (Bosun rule packs, no broad integrations) |
| Automatic context management/compaction | ✅ | ❌ |
| Self-hostable HTTP/WebSocket serving | ✅ | ✅ |
| Deterministic rule-based routing (no LLM router) | ❌ | ✅ |
| Provenance-typed facts (origin/source/confidence) | ❌ | ✅ |
| Counterfactual replay + per-transition checkpoints | ⚠️ (traces/state, no native counterfactual fork) | ✅ |
| Classical ML (sklearn/XGBoost/ONNX) as first-class nodes | ⚠️ (as tools, not native nodes) | ✅ |
| Embedded-by-default, air-gap posture | ❌ | ✅ |

Legend: ✅ shipped / first-class · ⚠️ partial or lower-level · ❌ absent

## Where StarGraph still wins (for honest framing)

- **Deterministic rule routing.** ADK 2.0 adds graph workflows and deterministic Sequential/Parallel/Loop agents, but its dynamic delegation still leans on the LLM as router. StarGraph removes the LLM from the routing decision entirely: transitions are derived by a CLIPS rules engine over typed facts, so the decision layer is inspectable, versioned, and free of stochastic drift.
- **Provenance-typed facts.** Every fact carries `(origin, source, run_id, step, confidence, timestamp)`, with `origin` typed as `llm | tool | user | rule | model | external`. Trust is a first-class type you can route on. ADK has traces and state, but not provenance as a typed, rule-addressable primitive.
- **Counterfactual replay.** Checkpoint pinning plus structural graph hashing make deterministic replay essentially free: re-run from any step with a mutated rule, node output, or fact and diff against the original run. ADK offers traces and evaluation, but no native counterfactual fork-and-diff.
- **Classical ML as first-class nodes.** sklearn / XGBoost / PyTorch / ONNX run as native `MLNode`s alongside DSPy LLM modules — route on a cheap model's confidence and only escalate to an LLM when unsure. In ADK, non-LLM models are wrapped as tools, not nodes in the control graph.
- **Air-gap posture.** Embedded-by-default stores (LanceDB / RyuGraph / SQLite), an operator playbook, and model staging target cleared, air-gapped, and regulated (DoD, finance, healthcare) deployments. ADK's center of gravity is Google Cloud and the Gemini stack.

## Bottom line

- **Choose Google ADK when** you want a mature, well-supported, multi-language platform with multimodal streaming, A2A interoperability, real multi-agent teams, a deep tool/model ecosystem, a dev UI with built-in evaluation, and one-command deployment to Google Cloud — and you're comfortable with LLM-influenced routing.
- **Choose StarGraph when** your priority is a deterministic, inspectable, replayable decision layer — rules instead of an LLM router, provenance-typed facts, counterfactual replay, classical ML as first-class nodes, and an air-gappable embedded-by-default footprint — and you can accept an alpha, Python-only, headless framework with a small ecosystem.

## Sources

- [google/adk-python (GitHub)](https://github.com/google/adk-python)
- [ADK official documentation](https://adk.dev/)
- [google/adk-web (developer UI)](https://github.com/google/adk-web)
- [google/adk-samples](https://github.com/google/adk-samples)
- [google/adk-docs](https://github.com/google/adk-docs)
- [ADK streaming / Gemini Live (audio, images, video)](https://google.github.io/adk-docs/streaming/)
- [Bidirectional streaming with Vertex AI Agent Engine Runtime](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/bidirectional-streaming)
- [Create multi-agent systems with ADK and the A2A protocol (Google Codelabs)](https://codelabs.developers.google.com/codelabs/create-multi-agents-adk-a2a)
- [Mastering ADK workflows: Sequential, Parallel, Loop, Custom agents](https://medium.com/@shins777/adk-workflow-the-core-logic-of-ai-agent-8ce4be5c1c40)
- [Built-in tools (DeepWiki: google/adk-python)](https://deepwiki.com/google/adk-python/7.6-built-in-tools)
- [Vertex AI Search tool for ADK](https://google.github.io/adk-docs/integrations/vertex-ai-search/)
- [Deploy ADK agents to Cloud Run (Google Codelabs)](https://codelabs.developers.google.com/deploy-manage-observe-adk-cloud-run)
- [Cloud Trace observability for ADK](https://adk.dev/integrations/cloud-trace/)
- [Agent Development Kit: easy multi-agent applications (Google Developers Blog)](https://developers.googleblog.com/en/agent-development-kit-easy-to-build-multi-agent-applications/)
