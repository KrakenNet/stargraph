# Tutorials

Hands-on lessons that take you from a single-node graph to a running
`stargraph serve` with replay, retrieval, classical ML, and human-in-the-loop
gates wired up. Every tutorial is self-contained: paste the code, run the
command, verify the output.

## Learning path

### Beginner — graph mechanics

- [**First graph**](first-graph.md) — a two-node graph with a Pydantic
  state model, routed by a single rule, executed via `stargraph run` and
  inspected via `stargraph inspect`.
- [**Add a Fathom rule pack**](fathom-rules.md) — wire deterministic
  governance onto the first graph, see the routing rule fire in the
  audit log, validate the pack with the Fathom plugin tooling.

### Intermediate — real nodes

- [**Agent with retrieval**](agent-with-retrieval.md) — `RetrievalNode`
  fans out across a LanceDB vector store, fuses hits, and feeds a
  `DSPyNode` summariser through the force-loud DSPy adapter.
- [**Classical ML in a graph**](ml-node-graph.md) — `MLNode` running an
  ONNX classifier inside an otherwise-LLM graph, with the sklearn
  safe-load gate explained.

### Advanced — production wiring

- [**HITL graph**](hitl-graph.md) — `InterruptNode` pauses a run,
  `stargraph respond` resumes it, and the whole thing survives a
  cold-restart of the engine.
- [**Serve and replay**](serve-and-replay.md) — boot the FastAPI app
  with `stargraph serve`, enqueue a run over HTTP, and replay it
  byte-identically with `stargraph replay`.

## Prerequisites for every tutorial

- Python 3.13+
- `uv add stargraph` (or `pip install 'stargraph[stores,ml]'` for the
  retrieval and ML lessons)
- A scratch directory to hold per-run state under `./.stargraph/`

!!! tip "Reference docs nearby"
    Each tutorial links into [Concepts](../concepts/index.md),
    [Reference / nodes](../reference/nodes/index.md), and the
    [Engine](../engine/index.md) and [Serve](../serve/overview.md)
    sections. Read them once you understand the mechanics; the
    tutorials cover only what each lesson needs.
