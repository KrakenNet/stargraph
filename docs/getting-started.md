# Getting Started

Install Stargraph and run your first agent graph end-to-end in a couple of
minutes. By the end you will have executed a real graph, persisted a checkpoint
database, and inspected the run timeline.

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

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
uv add stargraph                          # core
```

The core install is enough for everything on this page. Optional extras pull in
heavier subsystems only when you need them:

```bash
uv add 'stargraph[ml]'                     # + sklearn / xgboost / onnxruntime
uv add 'stargraph[stores]'                 # + lancedb / ryugraph / pyarrow
uv add 'stargraph[skills-rag]'             # + sentence-transformers
```

If you prefer pip, `pip install stargraph` (and `pip install 'stargraph[ml]'`,
etc.) works the same way.

## Run your first graph

Stargraph ships a minimal two-node fixture — `node_a` (echo) transitions to
`node_b` (halt). It uses no LLM, no store, and no external services, so it runs
anywhere the package is installed. From a checkout of the repository:

```bash
uv run stargraph run tests/fixtures/sample-graph.yaml
```

```text
[01] node_b                         ✓     0ms

✓ done in 3ms  (1 steps, 0 llm calls)

  inspect:
    stargraph inspect ./.stargraph/run.sqlite --run-id <run_id>

run_id=019edc5d-dd2a-7770-99f7-f52c6847d92e status=done
```

The run exits `0` on `status=done` and writes a SQLite checkpoint to
`./.stargraph/run.sqlite` by default (override with `--checkpoint <path>`). Use
`-i key=value` to seed initial state fields and `--log-file <path>` to append a
JSONL event log.

## Inspect the run

Every run is checkpointed, so you can replay its timeline read-only. Pass the
`run_id` printed above and the checkpoint database:

```bash
uv run stargraph inspect <run_id> --db ./.stargraph/run.sqlite
```

```text
step=0 transition=- node=node_a tool_calls=[-] rules=[-]
step=1 transition=- node=node_b tool_calls=[-] rules=[-]
```

Add `--step N` to print the canonical state dict at a given step, or
`--diff N M` to see the CLIPS facts added/removed between two steps.

## Simulate without executing

`simulate` dry-runs a graph against synthetic node outputs and prints the
rule-firing trace — no nodes are executed and no checkpoint is touched. It takes
a fixtures file mapping each `node_id` to a synthetic output:

[`examples/hello.yaml`](https://github.com/KrakenNet/stargraph/blob/main/examples/hello.yaml)
is the smallest runnable graph — two nodes, two routing rules, one state
field, no LLM. Run it end to end:

```bash
# fixtures.yaml: one entry per node
#   node_a: {}
#   node_b: {}
uv run stargraph simulate tests/fixtures/sample-graph.yaml --fixtures fixtures.yaml
```

```text
graph_hash=21aaab638b478e555bd1559b15c8fcff12460c72f78945cca0577ff27141a993
rule_firings=2
  rule=r-advance fired=False matched=[-] actions=[goto]
  rule=r-halt fired=True matched=[node_b] actions=[halt]
```

## Other CLI commands

`stargraph --help` lists the full command set: `run`, `inspect`, `simulate`,
`counterfactual`, `replay`, `respond`, `serve`, and `verify-audit`. Run
`stargraph <command> --help` for the options of any one.

## Next steps

- [Tutorial: Your First Graph](tutorials/first-graph.md) — build this graph
  from scratch, line by line, and learn how the IR YAML maps onto the runtime.
- Browse the other [tutorials](tutorials/index.md) for retrieval, Fathom rule
  packs, human-in-the-loop pauses, ML nodes, and serve/replay.
