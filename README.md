# StarGraph - Core (harbor)

**Stateful agent-graph framework with deterministic governance.**

Harbor composes LLMs, classical ML models, tools, and deterministic logic into
auditable, replayable graphs. Transitions between nodes are decided by
[Fathom](https://github.com/KrakenNet/fathom) (a CLIPS rules engine) over
provenance-typed facts — not by an LLM playing router.

> **Status:** v0.3.0 — Alpha. Public API is unstable until v1.0.
> Built for environments where auditability, determinism, and provenance matter
> more than ecosystem size (DoD, regulated, air-gapped, cleared workloads).

---

## The thesis

> LLMs are knowledge engineers, not inference engines. CLIPS is a better
> inference engine than any LLM. Use the right tool for each job and let
> rules — not LLMs — decide what happens between tools.

In most agent frameworks the LLM is both the worker and the router: it does the
thinking _and_ picks the next step. That is fine for demos and brittle in
production. Harbor splits the job. Nodes do work (LLM calls, ML inference, tool
invocations, retrieval). Rules decide what happens next. The decision layer is
inspectable, versioned, replayable, and free of stochastic drift.

## What you get

- **Rule-routed graphs.** No static edges. Transitions are derived at runtime
  by Fathom rules matching against typed facts in CLIPS working memory.
- **Provenance-typed facts.** Every fact carries
  `(origin, source, run_id, step, confidence, timestamp)`. Origins are typed:
  `llm | tool | user | rule | model | external`. Trust is a first-class type.
- **Classical ML as first-class nodes.** sklearn, XGBoost, PyTorch, ONNX run
  alongside DSPy LLM modules. Route on confidence, fall back to LLMs only when
  the cheap model is unsure.
- **Counterfactual replay.** Checkpoint pinning + structural graph hashing
  makes deterministic replay free. Re-execute from any step with mutated rule,
  node output, or fact, and diff against the original run.
- **Pluggable stores behind Protocols.** `VectorStore` (LanceDB),
  `GraphStore` (RyuGraph), `DocStore`/`MemoryStore`/`FactStore` (SQLite). Embedded
  by default, swappable for hosted providers.
- **Boundary-clean state.** Mutate Pydantic state freely inside a node. On
  exit, annotated fields mirror into CLIPS, rules fire, checkpoint persists.
  Predictable, testable, transactional.
- **Two authoring modes.** Python with full Pydantic typing, or YAML with a
  compiled subset for non-Python contributors. One runtime truth.
- **Headless serving.** `harbor serve` exposes HTTP + WebSocket triggers
  (`manual`, `cron`, `webhook`) over FastAPI with OpenAPI 3.1.

## Architecture

```
harbor serve            triggers, scheduler, HTTP/WS, run history
       │
harbor.Graph            orchestration, streaming, checkpointing
       │
harbor.skills           agents-as-subgraphs, tool registry, plugins
       │
Bosun rule packs        budgets, retries, safety, audit (Fathom rules)
       │
Fathom (CLIPS)          routing and governance inference
       │
Nodes:  DSPy │ ML models │ tools │ retrieval │ memory ops
```

## Install

```bash
uv add harbor                          # core
uv add 'harbor[ml]'                    # + sklearn / xgboost / onnxruntime
uv add 'harbor[stores]'                # + lancedb / ryugraph / pyarrow
uv add 'harbor[skills-rag]'            # + sentence-transformers
```

Requires Python 3.13.

## Quick taste

A graph that runs a cheap intent classifier first and only falls back to an LLM
when confidence is low:

```yaml
nodes:
  classify_intent: ml:intent_clf@v3 # XGBoost
  fallback_llm: dspy:intent_predict
  act: tool:do_thing

rules:
  - when: { classify_intent.confidence: { lt: 0.7 } }
    then: { goto: fallback_llm }
  - when: { last: classify_intent, classify_intent.confidence: { gte: 0.7 } }
    then: { goto: act }

governance: [bosun:budgets, bosun:audit]
```

Run it:

```bash
harbor run path/to/graph.yaml
harbor serve                          # FastAPI on :8000
```

Counterfactual replay from Python:

```python
run = harbor.load_run("r-7af2")
alt = run.counterfactual(step=4, mutate={"facts.intent": "research"})
diff = harbor.compare(run, alt)
```

## Concepts at a glance

| Term           | Meaning                                                                                              |
| -------------- | ---------------------------------------------------------------------------------------------------- |
| **Graph**      | Static blueprint: nodes, state schema, rules, governance.                                            |
| **Run**        | One execution of a graph, addressable by `run_id`, fully resumable.                                  |
| **Node**       | A unit of work: DSPy module, ML model, tool call, retrieval, sub-graph.                              |
| **State**      | The Pydantic-typed bundle of values flowing through a run.                                           |
| **Fact**       | A CLIPS tuple — mirrored from annotated state, emitted by the runtime, or asserted by rules.         |
| **Rule**       | A Fathom production. Matches facts, emits a routing action (`goto`, `parallel`, `halt`).             |
| **Pack**       | A versioned named collection of rules — mounted onto a graph declaratively.                          |
| **Skill**      | A bundle of tools, optional sub-graph, optional prompt fragment. The unit of capability composition. |
| **Plugin**     | A pip-installable package shipping skills, tools, nodes, or stores via entry points.                 |
| **Store**      | The data tier abstraction (vector/graph/doc/memory/fact); concrete impls are Providers.              |
| **Checkpoint** | Per-transition snapshot: state, facts, last node, next action, graph hash.                           |

The full glossary, including disambiguations like _node vs tool_ and
_state vs facts_, lives in
[`design-docs/harbor-concepts.md`](./design-docs/harbor-concepts.md).

## What Harbor is not

- **Not a prompt-optimization framework** — that's DSPy, which Harbor uses.
- **Not an inference engine** — that's Fathom/CLIPS, which Harbor uses.
- **Not a vector or graph DB** — Stores wrap real ones (LanceDB, RyuGraph, …).
- **Not a workflow UI** — `harbor serve` is headless. UI is a future product.
- **Not chasing LangGraph or n8n on mindshare.** It competes on correctness,
  inspectability, and ability to run where those tools can't.

## Project layout

```
src/harbor/
  graph/          Graph, Run, transitions, streaming
  runtime/        Engine, scheduler, concurrency
  nodes/          DSPy, ML, retrieval, memory, tool-call nodes
  fathom/         Adapter to the Fathom CLIPS engine
  bosun/          Reference governance rule packs
  skills/         RAG, autoresearch, wiki, ReAct
  stores/         Vector/Graph/Doc/Memory/Fact protocols + providers
  tools/          Tool registry + Nautilus broker tool
  triggers/       manual / cron / webhook
  serve/          FastAPI HTTP + WebSocket API
  checkpoint/     Per-transition persistence (SQLite default, Postgres adapter)
  replay/         Counterfactual replay engine
  cli/            harbor run / serve / inspect / replay / counterfactual
  ir/             YAML → IR compiler
  ml/             Classical-ML node integrations
demos/            End-to-end reference graphs (PR review, SOC triage, …)
design-docs/      Concepts, design, ADRs, plugin API, roadmap
docs/             User-facing docs site (mkdocs)
tests/            unit · integration · property · replay · migration
```

## Documentation

- [Getting Started](./docs/getting-started.md)
- [Concepts & Glossary](./design-docs/harbor-concepts.md)
- [Design Document](./design-docs/harbor-design.md)
- [Architecture Decision Records](./design-docs/harbor-adrs.md)
- [Plugin API](./design-docs/harbor-plugin-api.md)
- [Roadmap](./design-docs/harbor-roadmap.md)

Full site: <https://harbor.krakn.ai>

## Sister projects

Harbor is part of the **Kraken Networks** stack:

- **[Fathom](https://github.com/KrakenNet/fathom)** — CLIPS rules engine
  Harbor delegates routing and governance to.
- **Bosun** — reference rule packs (budgets, retries, audit, safety) shipped
  in `harbor.bosun.*`.
- **[Nautilus](https://github.com/KrakenNet/nautilus)** — knowledge broker;
  ships an in-tree Harbor tool (`harbor.tools.nautilus.broker_request`).
- **Railyard** — Harbor's Go predecessor. Harbor is the Python cousin and may
  supersede it.

## Development

```bash
make install            # uv sync --group dev --group docs
make lint               # ruff
make typecheck          # pyright (strict)
make test               # unit
make test-all           # everything (needs Fathom + integration deps)
make docs-serve         # local docs site
```

Contributions: see [CONTRIBUTING.md](./CONTRIBUTING.md) and
[CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md). Security disclosures:
[SECURITY.md](./SECURITY.md).

## License

Apache-2.0. See [LICENSE](./LICENSE).
