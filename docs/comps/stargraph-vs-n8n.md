# StarGraph vs n8n

A candid, gap-focused comparison. This page exists to answer one question
honestly: **what does [n8n](https://n8n.io/) do that StarGraph does not?** It is
deliberately one-sided — StarGraph's own advantages (deterministic rule routing,
provenance, replay, air-gap posture) are summarized only briefly at the end.

A fairness note up front, because it matters more here than in most comparisons:
**n8n and StarGraph are different categories of tool.** n8n is a visual,
low-code **workflow-automation platform** — its center of gravity is a
drag-and-drop canvas, 400+ service integrations, and operators who connect SaaS
apps without writing much code. It has grown strong AI-agent features on top of
that base, but it is not trying to be a typed, code-first agent *framework*.
StarGraph is the opposite: a Python library for engineers who want typed state,
rule-routed graphs, and deterministic replay, with **no visual builder at all**.
So treat this as a comparison on the axes where the two genuinely overlap
(building and running AI agents and tool-using workflows), and be clear that on
n8n's home turf — visual building, breadth of connectors, and non-developer
accessibility — StarGraph is simply not competing.

> **Versions compared:** StarGraph v0.4 (alpha, public API unstable until v1.0)
> vs n8n v2.26.x (stable, June 2026). n8n is fair-code, distributed under the
> Sustainable Use License with an additional n8n Enterprise License for `.ee`
> modules; StarGraph is Apache-2.0.

---

## TL;DR — different design centers

| | **StarGraph** | **n8n** |
| --- | --- | --- |
| Design center | Determinism, auditability, provenance, replay | Visual automation, breadth of integrations, time-to-ship |
| Routing / control model | Rules (Fathom/CLIPS) over typed facts; no LLM router | Visual graph drawn by hand; AI Agent node lets an LLM reason and pick tools |
| Primary surface | Python (or YAML subset) + CLI; headless server | Drag-and-drop web canvas; self-host or n8n Cloud |
| Target user | Engineers in regulated / cleared / air-gapped settings | Operators, automation builders, and developers who want low-code speed |
| Maturity | v0.4 alpha, single-org, no UI | v2.26, ~193k★, huge community, mature product |
| Bet | "Rules, not LLMs, decide what happens next" | "Make automation visual, connected, and fast for everyone" |

n8n wins decisively on everything a builder sees in the first hour: a visual
editor, an enormous connector catalog, a template marketplace, and a polished
hosted product. StarGraph wins on a narrow set of axes — provability,
deterministic replay, classical-ML routing, and air-gap deployment — that most
automation teams never need but a few cannot do without. **The rest of this page
is the first-hour list.**

---

## Where n8n is ahead

### 1. Visual no-code/low-code builder

- **n8n:** a mature drag-and-drop **canvas**. You build workflows by placing
  nodes and wiring them together visually, inspect the data flowing on each
  connection, and run a workflow step by step with inline logs at every node.
  Non-developers can build real automations.
- **StarGraph:** **no visual builder.** You author graphs in Python (full
  Pydantic typing) or a compiled YAML subset, and run them from the CLI or a
  headless server. The README is explicit that "UI is a future product."

**Gap:** there is no visual authoring or inspection surface in StarGraph at all.

### 2. Breadth of integrations and connectors

- **n8n:** **400+ built-in integration nodes** (Slack, Postgres, Google
  Workspace, S3, GitHub, Stripe, Notion, HubSpot, and hundreds more), each a
  documented, maintained connector you wire in by selecting it.
- **StarGraph:** a tool registry, an MCP client adapter, and a handful of
  in-tree tools (the Nautilus knowledge broker, etc.). **No large prebuilt
  connector library** — you write or wrap most integrations yourself.

**Gap:** turnkey, documented breadth of service integrations.

### 3. Template library and community workflows

- **n8n:** an official template marketplace with **10,000+ community workflow
  templates** (thousands tagged AI, marketing, sales, DevOps), plus large
  third-party collections. Most builders start by cloning a template.
- **StarGraph:** a handful of in-tree reference skills (RAG, autoresearch,
  ReAct, triage, sql_analyst, extract, digest) and demo graphs. **No template
  marketplace and effectively no community-contributed library.**

**Gap:** a deep, searchable library of ready-to-run starting points.

### 4. Maturity, community, and ecosystem

The single biggest gap, and it is not close.

| | StarGraph | n8n |
| --- | --- | --- |
| GitHub stars | — (early / single-org) | ~193,000 (June 2026) |
| Releases | v0.4 alpha, API unstable until v1.0 | v2.26.x, stable, frequent releases |
| Contributors | Single org (Kraken Networks) | Large open-source + commercial team and community |
| Third-party content | Effectively none | Courses, videos, blogs, agencies, a hiring market |
| Hosted product | None | n8n Cloud (paid tiers) |

**Gap:** community, battle-testing, ecosystem, and the assurance that the API
you build on today will still exist next quarter.

### 5. AI Agent node and LangChain ecosystem

- **n8n:** a first-class **AI Agent node** built on LangChain. The agent reasons,
  maintains conversation memory, streams responses, and can call **any other n8n
  node as a tool** — instantly turning the 400+ connector catalog into an agent
  toolbelt. 70+ AI-specific nodes cover models, memory, vector stores, and
  chains.
- **StarGraph:** agents are sub-graphs of typed nodes; LLM calls go through DSPy.
  Powerful and inspectable, but there is no "agent reasons and picks from
  hundreds of pre-wired tools" experience, and no LangChain node ecosystem.

**Gap:** a turnkey reasoning agent whose toolbelt is the entire integration
catalog.

### 6. Broad model-provider support, by selection

- **n8n:** model-agnostic at the node level — OpenAI, Anthropic, Google Vertex
  AI, Mistral, Ollama for local models, and any OpenAI-compatible endpoint, each
  selectable from a dropdown with documented credentials.
- **StarGraph:** LLM calls go through **DSPy**. You get whatever DSPy/LiteLLM
  reaches, but there is no first-class provider catalog, no per-provider node,
  and the integration story is "configure DSPy."

**Gap:** point-and-click provider selection with documented credential handling.

### 7. RAG and vector-store integrations out of the box

- **n8n:** **Vector Store nodes** for Pinecone, Qdrant, Supabase, Azure AI
  Search, and others, with loaders, embedders, and retrieval wired into the
  Agent node — a documented Agentic-RAG pattern you assemble visually.
- **StarGraph:** clean `VectorStore` / `GraphStore` / `DocStore` Protocols, but
  the **shipped concrete providers are narrow** — LanceDB (vector), RyuGraph
  (graph), SQLite (doc/memory/fact). Embedded-by-default is great for air-gap; it
  means few hosted/managed backends ship today.

**Gap:** number of ready hosted vector backends and a turnkey visual RAG
pipeline.

### 8. Code nodes inside a visual flow

- **n8n:** when the visual nodes run out, drop a **Code node** and write
  JavaScript or Python inline (npm packages installable when self-hosted) — an
  escape hatch that sits right in the canvas.
- **StarGraph:** everything is code already, so there is no equivalent "escape
  hatch," but there is also no canvas to escape *from*. The two simply meet the
  developer at opposite ends.

**Gap:** the low-code-plus-inline-code blend that lets a non-engineer build most
of a flow and an engineer fill the last 10%.

### 9. Hosted product, multi-user, RBAC, and SSO

- **n8n:** **n8n Cloud** (paid tiers from ~€24/mo) plus self-host. Enterprise
  adds **SAML SSO, granular RBAC, audit logs, and log streaming to external
  SIEMs**. Multi-user collaboration is built in.
- **StarGraph (`stargraph serve`):** a FastAPI HTTP + WebSocket server with
  triggers, profiles, and run history — but explicitly **headless**, with no
  hosted offering and a weaker multi-tenant / RBAC story.

**Gap:** a managed cloud product and an enforced multi-user / RBAC / SSO runtime.

### 10. Operations UI: runs, logs, and human-in-the-loop

- **n8n:** a full **executions UI** — see every run, inspect per-node input and
  output, replay, and pause workflows at **manual approval gates** for
  high-stakes steps, all in the browser.
- **StarGraph:** strong forensic tooling — audit logs, counterfactual replay, an
  `inspect` CLI, structural graph hashing, and an `InterruptNode` for HITL — but
  it is **all CLI/API**, with no dashboard to watch or approve runs.

**Gap:** a browser-based operations console for monitoring, inspecting, and
approving runs.

### 11. Triggers and event sources

- **n8n:** a large catalog of **trigger nodes** — webhooks, schedules/cron, and
  hundreds of app-specific triggers (a new Slack message, a Stripe event, a row
  added to a sheet) — selectable visually.
- **StarGraph:** ships **manual / cron / webhook** triggers with a scheduler.
  Solid for a framework, but there is no library of app-specific event sources;
  you wire those yourself.

**Gap:** breadth of ready-made, app-specific event triggers.

### 12. Built-in evaluations and observability

- **n8n:** an evaluations feature for AI workflows, step-by-step execution logs,
  and (on Enterprise) audit-log and log-streaming integrations for external
  observability tooling.
- **StarGraph:** replay and audit are powerful but **forensic**-leaning. Live
  surface: `stargraph serve` exposes `GET /health` (per-component readiness) and
  `GET /metrics` (Prometheus text format — runs by status, run-duration summary,
  audit-chain height, rule-transition count), scrapeable by any Prometheus
  stack. Still no built-in agent-eval harness, no tracing dashboards, and no
  OTel span export.

**Gap:** a built-in eval flow and dashboard-style tracing (metrics endpoint
exists; dashboards and OTel do not).

### 13. Onboarding and accessibility

- **n8n:** a non-developer can build a working automation from a template in an
  afternoon; the canvas teaches the model as you go.
- **StarGraph:** to be productive you must learn **CLIPS rules, Fathom,
  provenance-typed facts, the state↔facts boundary, packs, skills, and the IR**.
  Real power for the right problem, but a steep ramp and a Python prerequisite.

**Gap:** accessibility to non-developers and time-to-first-working-flow.

---

## Feature-gap matrix

| Capability | n8n | StarGraph |
| --- | :---: | :---: |
| Visual drag-and-drop builder | ✅ | ❌ |
| 400+ prebuilt integration nodes | ✅ | ❌ small registry |
| 10,000+ community templates | ✅ | ❌ few reference skills |
| AI Agent node (LLM reasons + picks tools) | ✅ | ⚠️ sub-graphs, no auto-toolbelt |
| Broad point-and-click model providers | ✅ | ⚠️ via DSPy, no catalog |
| Hosted vector stores + visual RAG | ✅ | ⚠️ few embedded backends |
| Inline Code nodes (JS / Python) | ✅ | ✅ code-first by nature |
| Managed cloud product | ✅ | ❌ self-host only |
| Multi-user, RBAC, SSO | ✅ Enterprise | ⚠️ partial |
| Browser ops UI (runs / logs / approvals) | ✅ | ❌ CLI / API only |
| App-specific event triggers | ✅ | ⚠️ manual/cron/webhook only |
| Built-in evaluations | ✅ | ❌ |
| Large community / ecosystem | ✅ | ❌ |
| Stable public API | ✅ | ❌ alpha |
| Deterministic rule routing | ❌ | ✅ |
| Provenance-typed facts | ❌ | ✅ |
| Counterfactual replay | ⚠️ re-run only | ✅ |
| Classical ML as first-class nodes | ❌ | ✅ |
| Air-gap posture | ⚠️ self-host | ✅ embedded-by-default |

Legend: ✅ shipped · ⚠️ partial / lower-level · ❌ absent.

---

## Where StarGraph still wins (for honest framing)

This page is about n8n's advantages, but the trade is not free. StarGraph keeps
the things n8n's visual-automation model structurally does not offer:

- **Deterministic routing.** Transitions are decided by Fathom/CLIPS rules over
  typed facts, not drawn by hand and not chosen by an LLM. The decision layer is
  inspectable, versioned, and free of stochastic drift.
- **Provenance as a type.** Every fact carries
  `(origin, source, run_id, step, confidence, timestamp)`, and `origin` is typed
  (`llm | tool | user | rule | model | external`). Trust is first-class — n8n
  passes JSON between nodes with no built-in notion of where a value came from or
  how much to trust it.
- **Counterfactual replay.** Checkpoint pinning plus structural graph hashing
  make deterministic re-execution from any step — with a mutated fact, rule, or
  node output — and a diff against the original run essentially free. n8n can
  re-run a workflow, but it cannot fork a past run from a checkpoint with mutated
  state and diff the outcomes.
- **Classical ML as first-class nodes.** Route on a cheap sklearn / XGBoost /
  ONNX model's confidence and fall back to an LLM only when it is unsure — these
  models are nodes in the graph, not external API calls.
- **Air-gap posture.** Embedded-by-default stores (LanceDB / RyuGraph / SQLite)
  and an operator playbook for cleared / air-gapped / regulated deployments,
  where most of n8n's value (the hosted connectors and cloud product) cannot
  reach.

n8n is the better default for connecting apps and shipping AI workflows fast,
visually, with a huge catalog behind you. StarGraph is the better — sometimes
only — choice when you must *prove* what the system did and why, replay it
deterministically, and run it where n8n's ecosystem cannot go.

---

## Bottom line

- **Choose n8n** when you want a visual builder, hundreds of ready connectors, a
  template marketplace, a managed cloud option, and a tool that non-developers
  and developers alike can ship with today. For most automation work, most of
  the time.
- **Choose StarGraph** when deterministic rule routing, provenance-typed facts,
  counterfactual replay, classical-ML routing, or air-gapped / regulated
  constraints are hard requirements — and you accept an alpha API, a code-first
  workflow, no visual builder, and more assembly to get there.

---

## Sources

- [n8n — homepage](https://n8n.io/) ·
  [n8n GitHub repository](https://github.com/n8n-io/n8n) ·
  [License (Sustainable Use License)](https://github.com/n8n-io/n8n/blob/master/LICENSE.md) ·
  [Release notes](https://docs.n8n.io/release-notes/)
- [Schedule Trigger node](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger/) ·
  [Docker / self-hosting](https://docs.n8n.io/hosting/installation/docker/) ·
  [Workflow templates](https://n8n.io/workflows/)
- [n8n Guide 2026 (Hatchworks)](https://hatchworks.com/blog/ai-agents/n8n-guide/) ·
  [Build AI Agents with n8n (Strapi)](https://strapi.io/blog/build-ai-agents-n8n) ·
  [n8n — the workflow automation tool for the AI age (WorkOS)](https://workos.com/blog/n8n-the-workflow-automation-tool-for-the-ai-age)
- [n8n Pricing 2026 (No Code MBA)](https://www.nocode.mba/articles/n8n-pricing) ·
  [n8n Review 2026 (StartupOwl)](https://startupowl.com/reviews/n8n)
- StarGraph: in-repo `README.md`, `docs/`, `design-docs/`, and `src/stargraph/`
  (v0.4, this repository).
