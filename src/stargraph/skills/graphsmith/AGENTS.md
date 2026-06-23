# AGENTS.md — graphsmith

## Purpose

A Stargraph graph that builds whole Stargraph **graphs** from a natural-language
brief, and improves at it over time. The first *composite* smith on the shared
core (`../_smith/`): where the leaf smiths (node/tool/store/trigger/adapter) each
emit one artifact, graphsmith emits a runnable multi-file **bundle** — a `State`
model, one or more `NodeBase` classes, and the `graph.yaml` wiring. Same two loops
as the leaf smiths: idea 1 (bounded generate → gate → repair; only gate-passing
bundles land) and idea 2 (file ledger of reflexion lessons + a brief→bundle
trainset + drift; offline compile of few-shot demos).

## Ownership

Owns graph-bundle generation only. The runtime it generates *for* (the `NodeBase`
contract, `Graph`/`GraphRun`, the IR, the checkpointer, the event bus) is owned
upstream in `src/stargraph/{nodes,graph,ir,checkpoint,runtime}/`. The
domain-agnostic machinery (build loop, gate tiers, ledger, RAG primitives, web,
LM, the DSPy generator shell, `SmithSpec`) lives in `../_smith/` (see its
AGENTS.md). This package is the *graph* plug-in: signature, `coerce`, the
bundle-assembly + the contract verdict (it composes the shared `RUN_GRAPH_PRELUDE`
from `_smith.gate`), corpus, seeds, state, graph wiring, and the spec's bundle
landing config. No node overrides — the lifecycle is wholly shared.

## Local Contracts

- `gate.py` — graph binding of `_smith.gate.run_tiered_gate`: static (compile +
  `ruff --select F`) → **contract** → tests (pytest `test_nodes.py`).
  `assemble_graph_yaml(graph_id, node_classes)` wires the ordered classes into a
  linear IR string (`state_class: "state:State"`, per-node `kind: "nodes:<Cls>"`,
  unique snake node ids, `rules: []`) — the wiring is **auto-assembled here, not
  LLM-emitted**, so `state_class`/`kind` paths are correct by construction. The
  contract driver is the shared `RUN_GRAPH_PRELUDE` (from `_smith.gate`) plus a
  one-line graph verdict: the prelude (subprocess) loads the assembled bundle into a
  real `stargraph.graph.Graph`, builds the node registry (resolving every `kind` to a
  `NodeBase`), and RUNS the graph to a terminal `ResultEvent` (draining the run
  bus concurrently with `run.start()` under `anyio.fail_after(30)`), asserting
  `status=="done"` and that each `fixture["expects"]` field appears in the final
  state (null ⇒ must be populated, else ⇒ exact match). Because the assert is on a
  real run's observable output, a trivially-passing generated unit test cannot
  land a bundle whose nodes do not actually connect. `verify_sources(...)` (all
  keyword-only) gates raw source strings in a throwaway temp dir — the entry for
  `make`, the doctor, and seed checks.
- `program.py::GraphProgram` — `_smith.program.SmithProgram` bound to
  `GraphSignature` + the graph `coerce`. Generation emits `graph_id`,
  `node_classes` (ordered, ≥2), `state_source` (`state.py`), `nodes_source`
  (`nodes.py`), `fixture` (`{inputs, expects}`), `test_source` (`test_nodes.py`).
  It does NOT emit `graph.yaml` (assembled by the smith). Auto-loads
  `compiled.json` demos at construction.
- `nodes/build.py` — `GRAPH_SPEC` (a `_smith.spec.SmithSpec`: the four-file
  `artifact_files`, the gate, recall sources, landing/trainset) + `Build`, a thin
  `_smith.build.SmithBuild` subclass constructing `GraphProgram()` by name (so
  tests monkeypatch it). The trainset row stores `graph_id` + `node_classes` (which
  regenerate `graph.yaml`), not the assembled yaml.
- `nodes/{triage,recall,record}.py` — no-arg subclasses of the shared `_smith.nodes`
  lifecycle nodes binding `GRAPH_SPEC`; no method overrides. The four-file bundle
  landing under `output_dir/<stem>/` (entry = `graph.yaml`) is driven by
  `GRAPH_SPEC.bundle_files` + `entry_file` through the shared `SmithRecord._land`.
  All loop/lifecycle logic stays in `_smith`.
- `state.py::State` — subclasses `_smith.state.SmithState` (the generic spine),
  adding only `graph_id`, `node_classes`, `fixture`.
- `_ledger.py` — binds `_smith.ledger.Ledger` to `GRAPHSMITH_HOME` (default
  `.stargraph/graphsmith/`, absolute).
- `retrieval.py` — RAG grounding: the `NodeBase` contract (`nodes/base.py`, always)
  + the most relevant sibling node modules under `stargraph.nodes` + gate-accepted
  ledger bundles. Injected as the signature's `relevant_context`.
- `seeds.py::SEEDS` — hand-authored, gate-verified cold-start bundles (normalize →
  classify; tokenize → count). Both are two-node pipelines where the second node
  reads a channel the first wrote, so the contract `expects` only holds if the
  nodes wired end-to-end. `test_seeds.py` gates every one.
- Graph order is linear (`graph.yaml`, `rules: []`): triage → recall → build →
  record. The repair loop lives INSIDE `build`. `recall` calls the LM, so it must
  run inside the scoped `dspy` context.

## TRUST BOUNDARY

The contract + tests tiers EXECUTE LLM-generated code in a subprocess as the
invoking user with full network + filesystem access — process isolation, not a
sandbox. The contract tier additionally LOADS and RUNS the generated graph (its
nodes' `execute` bodies run in that subprocess). Every gate path runs in a fresh
throwaway temp dir; `_smith.gate.write_files` refuses escaping paths. Don't run a
smith as a privileged user, don't put secrets in fixture/input values (stored
verbatim in the trainset row), and treat a generated bundle as untrusted code.

## Work Guidance

- No cheats/hardcoding/demo-data — the contract tier exists precisely so a
  trivially-passing generated test can't land a bundle whose nodes don't connect.
- `graph.yaml` is auto-assembled; never accept an LLM-emitted `graph.yaml`.
- Subprocesses use `sys.executable`; argv is always list-form (no `shell=True`).

## Verification

```
uv run ruff check src/stargraph/skills/graphsmith/ tests/integration/graphsmith/
uv run pyright src/stargraph/skills/graphsmith/ tests/integration/graphsmith/
uv run pytest tests/integration/graphsmith/
```
