# How to Author a `stargraph.yaml` Graph

## Goal

Write a Stargraph IR document (`stargraph.yaml`) that the engine can validate,
hash, and execute end-to-end via `stargraph run`.

## Prerequisites

- Stargraph installed (`pip install stargraph>=0.2`).
- Familiarity with [IR concepts](../concepts/ir.md) and the
  [Node Reference](../reference/nodes/index.md).
- A working directory writable to `./.stargraph/`.

## Steps

### 1. Start with the required top-level fields

Every IR document is a [`IRDocument`][ir-document]: `ir_version`, `id`,
`nodes` are required; everything else has a sensible default.

```yaml
# stargraph.yaml
ir_version: "1.0.0"
id: "graph:demo.hello"

state_schema:
  message: str

nodes:
  - id: greet
    kind: echo
```

`state_schema` here is the flat primitive map (string-typed). For
non-trivial state, use the escape hatch `state_class:
"my_pkg.state:MyState"` to point at a Pydantic `BaseModel` subclass —
the two are mutually exclusive.

**Verify:** `python -c "import yaml; from stargraph.ir import IRDocument;
IRDocument.model_validate(yaml.safe_load(open('stargraph.yaml')))"` exits
clean.

### 2. Add nodes

Every entry under `nodes:` is a [`NodeSpec`][node-spec] with an `id`
(unique) and `kind` (registry key or `"module.path:ClassName"`):

```yaml
nodes:
  - id: retrieve
    kind: retrieval                       # bundled RetrievalNode
  - id: classify
    kind: my_pkg.nodes:ClassifierNode     # importable NodeBase subclass
  - id: pause
    kind: interrupt                       # HITL primitive
  - id: ship
    kind: write_artifact
```

Bundled kinds: `echo`, `dspy`, `ml`, `memory`, `retrieval`, `subgraph`,
`write_artifact`, `interrupt`, `broker`. See
[Node Reference](../reference/nodes/index.md) for the full catalog and
each node's IO contract.

### 3. Wire stores, governance, and rules

```yaml
stores:
  - name: kb_vec
    provider: lancedb                    # registered StoreSpec
  - name: facts
    provider: sqlite

governance:
  - id: stargraph.bosun.budgets
    version: "1.0"
  - id: stargraph.bosun.audit
    version: "1.0"

rules:
  - id: halt-on-budget-exhausted
    when: "(bosun.violation (severity halt))"
    then:
      - kind: halt
        reason: "budget exhausted"
```

`rules.then` actions are the FR-11 verb set: `goto`, `halt`, `parallel`,
`retry`, `assert`, `retract`, `interrupt`. Nesting is forbidden — each
action lives at the top level of `then:`.

### 4. Reference subgraphs

```yaml
nodes:
  - id: sandbox
    kind: subgraph
    spec: ./subgraphs/sandbox_dispatch.yaml
```

The Sentinel Dark Watch demo
([`demos/sentinel_dark_watch/graph/stargraph.yaml`][sdw-graph]) is the
canonical worked example: ML detection, HITL review, governance packs.

For one node from that graph:

```yaml
- id: sandbox_run
  kind: subgraph
  spec: subgraphs/sandbox_dispatch.yaml
```

### 5. Validate the IR

```bash
stargraph run ./stargraph.yaml --inspect
```

`--inspect` skips node execution: it constructs the [`Graph`][graph],
prints the graph hash, and renders the rule-firing trace against
synthetic zero-value fixtures. Use this in CI to catch IR drift before
it reaches the runtime.

**Verify:** the command exits `0` and prints `graph_hash=<sha256-hex>`
plus one line per rule firing.

### 6. Run it

```bash
stargraph run ./stargraph.yaml \
    --inputs message="hello world" \
    --checkpoint .stargraph/run.sqlite \
    --log-file .stargraph/run.jsonl
```

`--inputs key=value` seeds initial state (validated against
`state_schema`). The CLI defaults the checkpointer to
`./.stargraph/run.sqlite` if `--checkpoint` is omitted.

## Wire it up

`stargraph.yaml` is consumed by:

- `stargraph run <graph.yaml>` — drives a single in-process run.
- `stargraph inspect <run_id> --db <ckpt.sqlite>` — replays the timeline.
- `stargraph replay <run_id> --db <ckpt.sqlite> --diff` — counterfactual
  fork.
- `stargraph serve` — the FastAPI app loads graphs out of a configured
  directory; see [serve overview](../serve/overview.md).

## Verify

After a successful `stargraph run`, the last line of stdout is:

```
run_id=<uuid> status=done
```

Non-`done` statuses raise exit code 1 — useful for CI gating.

## Troubleshooting

!!! warning "Common failure modes"
    - **`pydantic.ValidationError: extra fields not permitted`** — IR
      models are `extra="forbid"` (FR-6). Fix the typo or add the field
      to the schema.
    - **`unknown node kind ...`** — supply a registered key (`echo`,
      `retrieval`, ...) or a `module.path:ClassName` reference; the
      class must subclass `stargraph.nodes.base.NodeBase`.
    - **`SimulationError: missing fixture for node ...`** — `--inspect`
      synthesises empty dicts per node automatically; this error means
      a node is unreachable from the entry — check your node IDs.
    - **`PackCompatError`** — a `governance:` pack's `requires.stargraph_facts_version`
      doesn't match the running engine. Bump the pack or pin Stargraph.

## See also

- [IR Schema reference](../reference/ir-schema.md) — every field, every
  type.
- [Tutorial: Your first graph](../tutorials/first-graph.md) — narrated
  walkthrough.
- [Demos catalog](https://github.com/KrakenNet/stargraph/blob/main/demos/CATALOG.md)
- [CLI reference](../reference/cli.md) — `stargraph run`, `stargraph inspect`,
  `stargraph replay`.

[ir-document]: https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/ir/_models.py
[node-spec]: https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/ir/_models.py
[graph]: https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/graph/__init__.py
[sdw-graph]: https://github.com/KrakenNet/stargraph/blob/main/demos/sentinel_dark_watch/graph/stargraph.yaml
