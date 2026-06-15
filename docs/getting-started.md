# Getting Started

Install Stargraph and run your first agent graph in under a minute.

## Install

Stargraph needs Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv add stargraph        # into an existing project
# or, to try it from a clone:
git clone https://github.com/KrakenNet/stargraph && cd stargraph
uv sync --group dev
```

Verify the CLI:

```bash
uv run stargraph --help
```

## Run the smoke graph

[`examples/hello.yaml`](https://github.com/KrakenNet/stargraph/blob/main/examples/hello.yaml)
is the smallest runnable graph — two nodes, two routing rules, one state
field, no LLM. Run it end to end:

```bash
uv run stargraph run examples/hello.yaml --inputs message=hello
```

You'll see the run reach `status=done`. To trace which Fathom rules fired
without executing nodes:

```bash
uv run stargraph run examples/hello.yaml --inspect
```

## Next

- [`examples/`](https://github.com/KrakenNet/stargraph/tree/main/examples) —
  runnable starting points (`hello.yaml`, `pipeline.yaml`). Every example is
  covered by a golden test, so they never rot.
- [Your First Graph](tutorials/first-graph.md) — the same graph, built up
  step by step, with `stargraph inspect` and a smoke test.
- [Architecture map](architecture-map.md) — where everything lives.
- `demos/` (in the repo) — full graphs with rule-routing on state, LLM, ML,
  and retrieval nodes.
