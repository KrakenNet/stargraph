# StarGraph vs Pydantic AI

This is a candid, gap-focused comparison: what does Pydantic AI do that StarGraph does not? It is intentionally one-sided. The two projects are closer in DNA than most of the comparisons in this directory — both are Python-first, both treat a Pydantic-typed object as the unit of state, and both lean hard on static typing as a correctness tool. They diverge on the control layer. Pydantic AI is a broad, production-mature *agent* framework where the LLM is the worker and (often) the router; StarGraph is a narrower *agent-graph* framework that pulls routing out of the LLM and into a deterministic rules engine. Where they overlap — typed state, tools, MCP, structured output, graph/state-machine workflows — Pydantic AI is more complete, better integrated, and far more battle-tested. This document enumerates those gaps.

> StarGraph v0.4 (alpha, public API unstable until v1.0) vs Pydantic AI v1.107 (stable, GA since September 2025; v2.0 in beta as of June 2026).

## TL;DR — different design centers

| | Pydantic AI | StarGraph |
|---|---|---|
| **Design center** | Type-safe agent framework: an `Agent` with typed deps, typed output, and tools, model-agnostic across ~25 providers | Stateful agent-graph framework: nodes do work, a CLIPS rules engine decides what happens next |
| **Routing / control model** | LLM-driven tool/agent control; optional `pydantic-graph` state machine for explicit flows | Rule-routed: no static edges, no LLM router; Fathom forward-chaining rules match typed facts |
| **Target user** | Python engineers building production agents who want validation, observability, and provider portability | Teams that need inspectable, versioned, replayable decision logic and provenance-tracked facts |
| **Maturity** | Stable v1, GA, ~18k stars, broad ecosystem, Logfire-backed observability | Alpha v0.4, unstable API, small community, embedded-by-default stores |
| **Core bet** | Bring Pydantic's "validate everything" rigor to the whole agent lifecycle, with first-class observability and durability | LLMs are knowledge engineers, not routers; the decision layer should be deterministic and auditable |

## Where Pydantic AI is ahead

### 1. Model and provider breadth

- **Pydantic AI:** Ships a provider-neutral `Model` abstraction with first-class support for roughly two dozen providers — OpenAI, Anthropic, Google/Gemini, DeepSeek, Grok, Cohere, Mistral, Perplexity — plus gateways and clouds: Azure AI Foundry, Amazon Bedrock, Google Cloud, Ollama, LiteLLM, Groq, OpenRouter, Together AI, Fireworks, Cerebras, Hugging Face, GitHub Models, Heroku, Vercel, Nebius, OVHcloud, Alibaba Cloud, and SambaNova. Swapping providers is a one-line model string change.
- **StarGraph:** All LLM calls go through DSPy. There is no first-class multi-provider catalog; you get whatever the underlying DSPy LM wrapper supports, configured at a lower level, with no curated provider surface.
- **Gap:** No native, curated multi-provider model layer; provider portability is DSPy's responsibility, not a StarGraph feature.

### 2. Production observability (Logfire / OpenTelemetry)

- **Pydantic AI:** Tightly integrates with Pydantic Logfire, a general-purpose OpenTelemetry observability platform built by the same team. Agent runs, tool calls, model requests, token usage, and graph steps emit OTel spans out of the box, viewable in Logfire or any OTel-compatible backend (Jaeger, Grafana, Datadog).
- **StarGraph:** Has rich per-transition checkpointing and a run-history store, but no built-in tracing dashboards, no OpenTelemetry emission, and no hosted observability product. Inspection is via CLI (`stargraph inspect`) against the run store.
- **Gap:** No OTel instrumentation and no observability UI; you cannot point StarGraph at Logfire/Grafana/Datadog and watch live agent telemetry.

### 3. A built-in evaluation framework (pydantic-evals)

- **Pydantic AI:** Ships `pydantic-evals`, a standalone, Pythonic framework for evaluating non-deterministic functions — datasets, cases, evaluators (including LLM-as-judge), and result analysis. It is designed to work with arbitrary stochastic functions, not just Pydantic AI agents.
- **StarGraph:** Has no eval framework. Its determinism story (counterfactual replay, structural graph hashing) is for *regression diffing of a single run*, not for measuring agent quality across a dataset of cases with scored evaluators.
- **Gap:** No dataset-driven evaluation, no built-in scorers/judges, no eval CLI — a real hole for anyone optimizing agent quality.

### 4. Durable execution backends

- **Pydantic AI:** Offers first-class durable execution across four co-maintained backends — Temporal, DBOS, Prefect, and Restate. Durable agents preserve progress across transient API failures, restarts, and long-running/async/human-in-the-loop workflows, with full streaming and MCP support retained.
- **StarGraph:** Has its own resumability model — per-transition checkpoints in SQLite/Postgres, runs addressable by `run_id` and resumable from any step. This is genuinely strong for replay, but it is a single homegrown mechanism, not an integration with industry-standard durable-workflow engines that operators already run.
- **Gap:** No Temporal/DBOS/Prefect/Restate integration; you cannot drop StarGraph into an existing durable-workflow platform — you adopt its checkpoint store instead.

### 5. Multimodal input

- **Pydantic AI:** First-class multimodal input — image, audio, video, and document content flow through typed wrappers (`ImageUrl`, `AudioUrl`, `VideoUrl`, `BinaryContent`) and into multimodal models. The AG-UI integration maps protocol media types straight onto these.
- **StarGraph:** Text-centric by construction. ML nodes can host vision models, but there is no first-class image/audio/video/document agent surface — no typed media content, no multimodal model routing.
- **Gap:** No native multimodal agent surface; vision/audio/video are an ML-node escape hatch, not a supported input type.

### 6. Agent-to-Agent (A2A) and multi-agent composition

- **Pydantic AI:** Supports the Agent-to-Agent (A2A) protocol for inter-agent communication, plus multi-agent composition where agents call other agents as tools/capabilities. This gives you peer delegation across process and even framework boundaries.
- **StarGraph:** Has no A2A protocol and no peer-delegation paradigm. Composition is strictly hierarchical via `SubGraphNode` — agents-as-subgraphs, nested under one graph runtime. There is no notion of independent agents negotiating over a wire protocol.
- **Gap:** No A2A and no multi-agent *team* model; only nested sub-graphs inside a single runtime.

### 7. Frontend / chat UI integration (AG-UI, Vercel AI SDK)

- **Pydantic AI:** Implements the AG-UI protocol and integrates with the Vercel AI SDK, converting agent events into a standard UI event stream. You get a documented path from agent to a streaming chat frontend, including streamed media content.
- **StarGraph:** `stargraph serve` is explicitly headless — FastAPI HTTP + WebSocket with OpenAPI 3.1, but no AG-UI, no Vercel AI SDK adapter, and no prebuilt chat UI. The README states the UI is "a future product."
- **Gap:** No AG-UI, no Vercel AI SDK bridge, no chat-UI story — you build the frontend integration yourself.

### 8. Streaming with live validation

- **Pydantic AI:** Streams structured output with real-time validation — partial responses are validated against the output Pydantic model as they arrive, so you can render typed, partially-complete results mid-stream.
- **StarGraph:** Streams events over WebSocket at the serving layer, but does not offer streamed-and-validated structured output at the node level the way Pydantic AI does; structured output validation happens when a node completes, not incrementally as tokens arrive.
- **Gap:** No incremental validated streaming of structured output.

### 9. Dependency injection ergonomics

- **Pydantic AI:** Dependency injection via `RunContext[Deps]` is a headline ergonomic — typed dependencies (DB connections, HTTP clients, config) are injected into tools, system prompts, and output validators, fully type-checked, and trivially swappable in tests.
- **StarGraph:** State flows as a mutated Pydantic object inside a node, which is clean, but there is no equivalent typed DI container threading external dependencies through tools, prompts, and validators with the same static-typing guarantees.
- **Gap:** No first-class typed DI for external dependencies across the tool/prompt/validator surface.

### 10. Built-in tools and an integration surface

- **Pydantic AI:** Ships built-in tools (web search, code execution, "thinking"), automatic JSON-schema generation from typed functions, and toolsets as a reusable grouping primitive — plus MCP for pulling in external tool servers.
- **StarGraph:** Has MCP (client) and a tool/skill model, but no comparable library of built-in first-party tools and no large prebuilt connector/integration catalog. Reference skills (RAG, ReAct, triage, sql_analyst, etc.) exist, but the out-of-the-box tool count is small.
- **Gap:** No meaningful library of built-in tools/connectors; you wire most integrations yourself.

### 11. Maturity, stability, and ecosystem

- **Pydantic AI:** Stable v1 (GA since September 2025, currently ~v1.107 with v2 in beta), ~18k GitHub stars, hundreds of contributors, MIT license, and the gravitational pull of the broader Pydantic ecosystem that nearly every Python AI project already depends on.
- **StarGraph:** Alpha v0.4 with an explicitly unstable public API, a small community, and a steep learning curve (CLIPS, Fathom, provenance facts, the state↔facts boundary, rule packs, the YAML→IR compiler).
- **Gap:** Pre-1.0 alpha vs a GA project with a large community and a deep, ubiquitous parent ecosystem.

### 12. Reasoning and "thinking" primitives

- **Pydantic AI:** Exposes thinking/reasoning capabilities and web-search as built-in features, letting agents use reasoning models and reasoning traces directly.
- **StarGraph:** Deliberately keeps reasoning out of the routing layer — by design the LLM does not reason about transitions. It has no first-class chain-of-thought or reasoning-model primitive at the agent surface.
- **Gap:** No built-in reasoning primitives (this is a design choice, but it is still a feature Pydantic AI ships that StarGraph does not).

## Feature-gap matrix

| Capability | Pydantic AI | StarGraph |
|---|:---:|:---:|
| Model/provider breadth (~25 providers) | ✅ | ⚠️ (via DSPy) |
| Structured / validated output | ✅ | ✅ |
| Streaming with live-validated output | ✅ | ⚠️ |
| Typed dependency injection | ✅ | ⚠️ |
| Tools / toolsets + auto JSON schema | ✅ | ✅ |
| Built-in tools (web search, code exec, thinking) | ✅ | ❌ |
| MCP support | ✅ | ✅ (client) |
| A2A protocol | ✅ | ❌ |
| AG-UI / Vercel AI SDK frontend | ✅ | ❌ |
| Multi-agent / peer delegation | ✅ | ⚠️ (sub-graphs only) |
| Graph / state-machine workflows | ✅ (pydantic-graph) | ✅ (rule-routed) |
| Built-in eval framework | ✅ (pydantic-evals) | ❌ |
| Durable execution (Temporal/DBOS/Prefect/Restate) | ✅ | ⚠️ (own checkpoints) |
| Observability (Logfire / OpenTelemetry) | ✅ | ❌ |
| Multimodal input (image/audio/video/docs) | ✅ | ❌ |
| Human-in-the-loop | ✅ | ✅ |
| Reasoning / thinking primitives | ✅ | ❌ |
| Deterministic rule routing | ❌ | ✅ |
| Provenance-typed facts | ❌ | ✅ |
| Counterfactual replay + graph hashing | ❌ | ✅ |
| Classical ML as first-class nodes | ❌ | ✅ |
| Air-gap / regulated deployment posture | ⚠️ | ✅ |
| Maturity / stability / community | ✅ | ❌ |

Legend: ✅ shipped / first-class · ⚠️ partial, lower-level, or via a workaround · ❌ absent.

## Where StarGraph still wins (for honest framing)

- **Deterministic rule routing.** Transitions come from a CLIPS forward-chaining engine matching typed facts, not from an LLM or static edges. The decision layer is inspectable, versioned, and free of stochastic drift. Pydantic AI's control flow ultimately leans on the LLM (or hand-authored graph code).
- **Provenance-typed facts.** Every fact carries `(origin, source, run_id, step, confidence, timestamp)`, with `origin` typed as `llm | tool | user | rule | model | external`. Trust is a first-class type, not metadata you bolt on.
- **Counterfactual replay.** Checkpoint pinning plus structural graph hashing make deterministic re-execution from any step essentially free — mutate a rule, node output, or fact and diff against the original run. Pydantic AI has durability and evals, but no built-in counterfactual diffing of a single run.
- **Classical ML as first-class nodes.** sklearn / XGBoost / PyTorch / ONNX run as nodes alongside LLM modules. Route on a cheap model's confidence score and fall back to an LLM only when unsure — a native pattern with no Pydantic AI equivalent.
- **Air-gap and regulated posture.** Embedded-by-default stores, an operator playbook, and model staging target cleared / air-gapped / regulated (DoD, finance, healthcare) deployments out of the box.

## Bottom line

- **Choose Pydantic AI** when you want a stable, production-mature agent framework with broad provider portability, first-class observability (Logfire/OTel), a real eval framework, durable execution on industry-standard engines, multimodal input, A2A and chat-UI integration, and a large, well-supported ecosystem.
- **Choose StarGraph** when the decision layer itself must be deterministic, inspectable, versioned, and replayable — when you need provenance-typed facts, counterfactual replay, classical ML as routing-grade nodes, and an air-gap-friendly deployment — and you can accept an alpha API and a steep learning curve to get them.

## Sources

- [Pydantic AI — official docs (overview)](https://pydantic.dev/docs/ai/overview/)
- [Pydantic AI — GitHub repository](https://github.com/pydantic/pydantic-ai)
- [Pydantic AI — version policy](https://ai.pydantic.dev/version-policy/)
- [Pydantic AI — durable execution overview (Temporal/DBOS/Prefect/Restate)](https://pydantic.dev/docs/ai/integrations/durable_execution/overview/)
- [Pydantic AI — AG-UI and Vercel AI SDK integration (DeepWiki)](https://deepwiki.com/pydantic/pydantic-ai/8.3-ag-ui-and-vercel-ai-sdk-integration)
- [pydantic-evals — source](https://github.com/pydantic/pydantic-ai/tree/main/pydantic_evals)
- [pydantic-ai — PyPI](https://pypi.org/project/pydantic-ai/)
- [Pydantic AI releases](https://github.com/pydantic/pydantic-ai/releases)
