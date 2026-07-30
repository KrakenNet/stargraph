# StarGraph vs AutoGen

This is a candid, gap-focused comparison: it enumerates what Microsoft AutoGen does that StarGraph does not. It is intentionally one-sided — StarGraph's own advantages are confined to one short section near the end. Both projects are genuinely agent frameworks, so they overlap on most axes. The honest framing up front: they have **opposite design centers**. AutoGen is a conversational, actor-model **multi-agent** framework where LLM agents talk to each other and a model (or an orchestrator agent) decides who speaks next. StarGraph is a single-graph, rule-routed framework where a deterministic rules engine — not an LLM — decides what runs next. Where AutoGen leans into emergent multi-agent conversation, Studio GUIs, and Microsoft's ecosystem, StarGraph leans into determinism, provenance, and replay. One more thing you should know before choosing: **AutoGen is in maintenance mode.** Microsoft has folded it (with Semantic Kernel) into the new Microsoft Agent Framework, which reached 1.0 GA in April 2026. AutoGen still works, still has a large community, and its concepts live on in MAF — but it will receive only bug fixes and security patches, no new features.

> **Versions compared:** StarGraph v0.4 (alpha, public API unstable until v1.0) vs AutoGen python-v0.7.5 (released September 2025; **in maintenance mode** as of 2026, superseded by Microsoft Agent Framework 1.0).

## TL;DR — different design centers

| | AutoGen | StarGraph |
|---|---|---|
| **Design center** | Conversational multi-agent systems on an async actor runtime | Stateful single-graph framework with a deterministic decision layer |
| **Routing / control model** | LLM-driven: an orchestrator agent or a model-backed `SelectorGroupChat` chooses the next speaker; `Swarm` uses LLM handoffs | Rule-routed: a CLIPS forward-chaining engine (Fathom) matches typed facts; no LLM router, no static edges |
| **Target user** | Python developers building agent teams; non-coders via AutoGen Studio; Microsoft/Azure shops | Python engineers who need auditable, replayable, governed pipelines (regulated / air-gapped) |
| **Maturity** | Mature, large community (~59k stars), but **maintenance-only**; future is Microsoft Agent Framework | Alpha, small community, API unstable, actively developed |
| **Core bet** | Emergent capability from LLM agents conversing + Microsoft ecosystem reach | LLMs are knowledge engineers, not routers — split work (nodes) from control (rules) for inspectability |

## Where AutoGen is ahead

### 1. Multi-agent teams and peer delegation

- **AutoGen:** A first-class team paradigm. `RoundRobinGroupChat`, `SelectorGroupChat` (model picks the next speaker), `Swarm` (agents hand off to each other by name), and `MagenticOneGroupChat` (a planner-orchestrator drives a team). Agents are peers that converse, delegate, and reach consensus or termination via conditions.
- **StarGraph:** No team or peer-delegation model. Composition is hierarchical only — agents-as-subgraphs via `SubGraphNode`. There is no notion of independent agents negotiating among themselves.

**Gap:** StarGraph has no conversational multi-agent / team abstraction at all — no group chat, no peer handoff, no orchestrator-led team.

### 2. Async, event-driven actor runtime

- **AutoGen:** The v0.4 redesign rebuilt Core as an actor model — agents are isolated actors communicating through asynchronous message passing on an event mesh. This is designed for high-concurrency, reactive agent topologies, and it underpins the distributed runtime.
- **StarGraph:** A graph executor that advances per transition, mutating a Pydantic state object and mirroring annotated fields into CLIPS working memory. It is deliberate and sequential-feeling by design (CLIPS in the loop, a checkpoint per transition), not an actor mesh.

**Gap:** StarGraph has no actor model and no asynchronous agent-to-agent message mesh.

### 3. Distributed runtime across processes / machines

- **AutoGen:** Core ships a distributed agent runtime so agents can run across process and machine boundaries, communicating over the same message contracts as the in-process runtime. The actor model was chosen explicitly to make this scale-out possible.
- **StarGraph:** Single-process graph execution with per-transition checkpointing (SQLite default, Postgres adapter). Runs are resumable and addressable by `run_id`, but there is no distributed multi-host agent fabric.

**Gap:** StarGraph offers no distributed/multi-host runtime; horizontal scale-out of agents is not a shipped capability.

### 4. AutoGen Studio — a no-code / low-code GUI

- **AutoGen:** AutoGen Studio is a real, shipped web application: drag-and-drop authoring of agents and teams, a component gallery, interactive run/debug, and a playground for stakeholder demos — usable without writing Python.
- **StarGraph:** Authoring is Python or a compiled YAML subset. The README explicitly states there is no management/web UI — "UI is a future product." There is no visual builder.

**Gap:** StarGraph has no GUI, no visual builder, and no no-code authoring path.

### 5. Native multimodal agents

- **AutoGen:** Multimodal is built in. Magentic-One ships a `MultimodalWebSurfer` that perceives and acts on rendered web pages, and model clients support image inputs. Agents handle vision-grounded web and file tasks out of the box.
- **StarGraph:** Text-centric. `MLNode` can host a vision model as a classical-ML node, but there is no first-class image/audio/video agent surface and no multimodal agent like WebSurfer.

**Gap:** StarGraph has no native multimodal agent surface — no built-in vision-grounded web/file agent.

### 6. Magentic-One — a ready-made generalist agent team

- **AutoGen:** Magentic-One is a packaged, general-purpose multi-agent system (Orchestrator + `WebSurfer` + `FileSurfer` + `Coder` + terminal) for open-ended web and file tasks. Its agents are now broadly available as standard AgentChat agents to drop into any team.
- **StarGraph:** Ships reference *skills* (RAG, autoresearch, ReAct, triage, sql_analyst, extract, digest) — useful building blocks, but no comparable turnkey generalist "do open-ended web+file work" agent team.

**Gap:** StarGraph has no flagship generalist agent like Magentic-One — no out-of-the-box web/file/code-solving team.

### 7. Code execution as a first-class, sandboxed capability

- **AutoGen:** Code execution is a built-in primitive. `DockerCommandLineCodeExecutor` runs model-generated code in an isolated container (`python:3-slim` by default, customizable); `LocalCommandLineCodeExecutor` and `PythonCodeExecutionTool` cover host/tool execution. `CodeExecutorAgent` closes the generate-execute-reflect loop automatically.
- **StarGraph:** No built-in sandboxed code-execution node or executor. Tool calls run through typed tool definitions with permission/side-effect flags; running arbitrary LLM-generated code is left to the author.

**Gap:** StarGraph ships no code-execution sandbox or generate-execute-reflect agent loop.

### 8. Built-in observability via OpenTelemetry

- **AutoGen:** Instrumented for OpenTelemetry, so agent runs, messages, and tool calls emit traces consumable by standard observability backends (Jaeger, Azure Monitor, etc.). Improved observability was a headline goal of the v0.4 redesign.
- **StarGraph:** Provides deep run introspection through checkpoints, fact streams, and `stargraph inspect`/`replay`, emits structured run history, and serves `GET /health` + `GET /metrics` (Prometheus text format) from `stargraph serve` — but there is no OpenTelemetry integration and no live tracing dashboard.

**Gap:** StarGraph has no OpenTelemetry export and no live tracing dashboard / APM integration (Prometheus scraping only).

### 9. A built-in benchmarking / eval harness

- **AutoGen:** AutoGen Bench is a shipped tool for measuring and comparing agent performance across tasks and environments, giving teams a repeatable way to evaluate changes.
- **StarGraph:** No published benchmark suite for agent performance. It does ship an eval harness for *policies/models* — `stargraph.rl.gauntlet` (3-way splits, Pareto-vs-baseline, CSCV-PBO) with a reference rule-routed eval graph — but nothing AutoGen-Bench-shaped for comparing agents across tasks, and it publishes no performance numbers (heavier by construction — CLIPS in the loop plus a checkpoint per transition).

**Gap:** StarGraph has no agent benchmarking suite and no published performance benchmarks (its eval harness targets policy/model admission).

### 10. Broad model-provider and tool ecosystem

- **AutoGen:** Extensions provide a catalog of model clients (OpenAI, Azure OpenAI, Anthropic, Ollama, Azure AI, and more), plus MCP tool integration and a wide set of community tools and adapters. Provider choice is a first-class, swappable concern.
- **StarGraph:** All LLM calls go through DSPy; there is no first-class multi-provider model catalog, and the shipped tool/integration library is small. It has an MCP client adapter, but no large connector ecosystem.

**Gap:** StarGraph has no first-class multi-provider model catalog and a far smaller built-in tool/integration set.

### 11. Mature documentation, samples, and community

- **AutoGen:** ~59k GitHub stars, an extensive docs site spanning Core / AgentChat / Extensions / Studio, a large body of tutorials and notebooks, and an active (now community-managed) ecosystem — plus the AG2 community fork carrying the lineage forward.
- **StarGraph:** Alpha, small community, unstable API, and a steep learning curve (CLIPS, Fathom, provenance facts, the state↔facts boundary, packs, the YAML→IR compiler).

**Gap:** StarGraph has a fraction of the community, examples, and documentation surface, and a markedly steeper on-ramp.

### 12. Microsoft / Azure backing and an enterprise upgrade path

- **AutoGen:** Backed by Microsoft Research, with Azure-native code executors and model clients, and a sanctioned migration path into the supported, GA Microsoft Agent Framework (with long-term support and enterprise features like middleware, telemetry, and session state).
- **StarGraph:** Independent, single-vendor (Kraken Networks), alpha. No large-platform backing and no enterprise support contract.

**Gap:** StarGraph has no major-platform backing, no Azure-native integrations, and no enterprise support/migration story.

## Feature-gap matrix

| Capability | AutoGen | StarGraph |
|---|---|---|
| Conversational multi-agent teams / group chat | ✅ | ❌ |
| Peer delegation / handoffs (Swarm) | ✅ | ❌ |
| Async actor runtime | ✅ | ⚠️ (sequential graph executor) |
| Distributed multi-host runtime | ✅ | ❌ |
| No-code / visual builder (Studio) | ✅ | ❌ |
| Native multimodal agents | ✅ | ⚠️ (vision via ML node only) |
| Turnkey generalist team (Magentic-One) | ✅ | ❌ |
| Sandboxed code execution | ✅ | ❌ |
| OpenTelemetry observability | ✅ | ⚠️ (inspect/replay, no OTel) |
| Built-in eval / benchmark harness | ✅ | ❌ |
| Multi-provider model catalog | ✅ | ⚠️ (via DSPy, no first-class catalog) |
| Large tool / integration ecosystem | ✅ | ⚠️ (small; MCP client only) |
| MCP support | ✅ | ⚠️ (client adapter) |
| Community size / docs maturity | ✅ | ⚠️ (alpha, small) |
| Major-platform backing | ✅ | ❌ |
| HITL / pause-resume | ✅ | ✅ |
| Headless HTTP/WebSocket serving | ⚠️ (Studio + app; no headless server product) | ✅ (`stargraph serve`) |
| Deterministic rule-based routing | ❌ | ✅ |
| Provenance-typed facts | ❌ | ✅ |
| Counterfactual replay (mutate + diff) | ❌ | ✅ |
| Classical ML (sklearn/XGBoost/ONNX) as nodes | ❌ | ✅ |
| Per-transition checkpointing | ⚠️ (save/restore task state) | ✅ |
| Air-gap / regulated deployment posture | ⚠️ (Azure-leaning) | ✅ |

Legend: ✅ shipped / first-class · ⚠️ partial, lower-level, or via a workaround · ❌ absent

## Where StarGraph still wins (for honest framing)

- **Deterministic rule routing.** Control flow is decided by a CLIPS forward-chaining engine over typed facts, not by an LLM. AutoGen's routing is the opposite bet — `SelectorGroupChat` and `Swarm` hand the "who's next" decision to a model. StarGraph's decision layer is inspectable, versioned, and free of stochastic drift.
- **Provenance-typed facts.** Every fact carries `(origin, source, run_id, step, confidence, timestamp)` with a typed origin (`llm | tool | user | rule | model | external`). Trust is a first-class type. AutoGen has no equivalent typed-trust model.
- **Counterfactual replay.** Checkpoint pinning plus structural graph hashing make deterministic replay essentially free: re-run from any step with a mutated rule, node output, or fact and diff against the original. AutoGen can save/restore task state, but has no mutate-and-diff counterfactual primitive.
- **Classical ML as first-class nodes.** sklearn / XGBoost / PyTorch / ONNX models run as nodes alongside DSPy LLM modules — route on a cheap model's confidence and fall back to an LLM only when unsure. AutoGen is LLM-agent-centric and has no classical-ML node primitive.
- **Air-gap posture.** Embedded-by-default stores (LanceDB / RyuGraph / SQLite), an operator playbook, and model staging target cleared / air-gapped / regulated (DoD, finance, healthcare) deployments — terrain where AutoGen's Azure-leaning, cloud-model defaults are a poorer fit.

## Bottom line

- **Choose AutoGen when** you want a mature, well-documented, community-backed framework for building conversational multi-agent teams; you value an actor runtime, a no-code Studio, multimodal web/file agents (Magentic-One), sandboxed code execution, and OpenTelemetry out of the box; and you're comfortable being on a maintenance-mode library whose forward path is Microsoft Agent Framework.
- **Choose StarGraph when** routing must be deterministic, inspectable, and replayable rather than model-driven; you need provenance-typed facts and counterfactual replay for audit; you want classical ML and LLMs as peer nodes; or you're deploying into air-gapped / regulated environments — and you can accept an alpha API, a small community, and no GUI, teams, or multimodal surface.

## Sources

- [AutoGen GitHub repository (microsoft/autogen)](https://github.com/microsoft/autogen)
- [AutoGen documentation (stable)](https://microsoft.github.io/autogen/stable/index.html)
- [AutoGen v0.4: Reimagining the foundation of agentic AI — Microsoft Research](https://www.microsoft.com/en-us/research/blog/autogen-v0-4-reimagining-the-foundation-of-agentic-ai-for-scale-extensibility-and-robustness/)
- [AutoGen reimagined: Launching AutoGen 0.4 — AutoGen Blog](https://devblogs.microsoft.com/autogen/autogen-reimagined-launching-autogen-0-4/)
- [Magentic-One — AutoGen docs](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/magentic-one.html)
- [AutoGen Studio — AutoGen docs](https://microsoft.github.io/autogen/dev/user-guide/autogenstudio-user-guide/index.html)
- [Code Execution — AutoGen docs](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/components/command-line-code-executors.html)
- [AutoGen to Microsoft Agent Framework Migration Guide — Microsoft Learn](https://learn.microsoft.com/en-us/agent-framework/migration-guide/from-autogen/)
- [Introducing Microsoft Agent Framework — Microsoft Foundry Blog](https://devblogs.microsoft.com/foundry/introducing-microsoft-agent-framework-the-open-source-engine-for-agentic-ai-apps/)
- [Microsoft ships production-ready Agent Framework 1.0 — Visual Studio Magazine](https://visualstudiomagazine.com/articles/2026/04/06/microsoft-ships-production-ready-agent-framework-1-0-for-net-and-python.aspx)
- [AG2 (formerly AutoGen) community fork — ag2ai/ag2](https://github.com/ag2ai/ag2)
