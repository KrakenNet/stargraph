# AGENTS.md — toolsmith

## Purpose

A Stargraph graph that builds Stargraph **tools** from a natural-language brief,
and improves at it over time. The second instance of the shared smith core
(`../_smith/`) — it proves the core generalizes past nodes. Same two loops as
nodesmith: idea 1 (bounded generate → gate → repair; only gate-passing tools
land) and idea 2 (file ledger of reflexion lessons + a spec→tool trainset +
drift; offline compile of few-shot demos).

## Ownership

Owns tool generation only. The runtime it generates *for* (`@tool` decorator,
`ToolSpec`, `SideEffects`/`ReplayPolicy`) is owned upstream in `src/stargraph/tools/`.
The domain-agnostic machinery (build loop, gate tiers, ledger, RAG primitives,
web, LM, the DSPy generator shell, `SmithSpec`) lives in `../_smith/` (see its
AGENTS.md). This package is the *tool* plug-in: signature, `coerce`, contract
driver, corpus, seeds, state, graph wiring. Keep tool-specific code here.

## Local Contracts

- `gate.py::run_full_gate` — tool binding of `_smith.gate.run_tiered_gate`:
  static (compile + `ruff --select F`) → **contract** → tests (pytest). The
  contract driver (subprocess) imports the module, finds the single
  `@tool`-decorated callable (one whose `.spec` is a `ToolSpec` and is defined in
  the module), asserts `input_schema`/`output_schema` are valid JSON Schema,
  validates the fixture against `input_schema`, RUNS the tool on the fixture
  (awaiting if it's a coroutine), and asserts the return validates against
  `output_schema`. That run-and-validate step is the un-cheatable floor — a
  trivially-passing generated test can't land a tool that crashes or returns
  off-schema. `verify_sources` gates raw source strings in a throwaway temp dir
  (the entry for `make`, the doctor, and seed checks).
- `program.py::ToolProgram` — `_smith.program.SmithProgram` bound to
  `ToolSignature` + the tool `coerce`. Generation emits `tool_name`, `namespace`,
  `fixture` (sample kwargs), `tool_source` (`tool.py`), `test_source`
  (`test_tool.py`). Auto-loads `compiled.json` demos at construction.
- `nodes/build.py` — `TOOL_SPEC` (a `_smith.spec.SmithSpec`, the full per-domain
  plug-in: artifact files, gate, recall sources, landing/trainset) + `Build`, a
  thin `_smith.build.SmithBuild` subclass that constructs `ToolProgram()` by name
  (so tests monkeypatch it). `nodes/{triage,recall,record}.py` are no-arg
  subclasses of the shared `_smith.nodes` lifecycle nodes, each binding `TOOL_SPEC`.
  All loop/lifecycle logic lives in `_smith`; only the spec is tool-specific.
- `state.py::State` — subclasses `_smith.state.SmithState` (the generic spine),
  adding only the tool fields `tool_name`, `namespace`, `fixture`.
- `_ledger.py` — binds `_smith.ledger.Ledger` to `TOOLSMITH_HOME` (default
  `.stargraph/toolsmith/`, absolute).
- `retrieval.py` — RAG grounding: the `@tool` contract (`decorator.py` + `spec.py`,
  always) + the most relevant sibling tool modules under `stargraph.tools` +
  gate-accepted ledger pairs. Injected as the signature's `relevant_context`.
- `seeds.py::SEEDS` — hand-authored, gate-verified cold-start tools spanning
  object/string/number/integer/array outputs (so the derived output schema is
  exercised). `test_seeds.py` gates every one. Tools are pure (`side_effects=none`,
  stdlib-only) so the contract tier runs them offline.
- Graph order is linear (`graph.yaml`, `rules: []`): triage → recall → build →
  record. The repair loop lives INSIDE `build`. `recall` gathers grounding
  (lessons + RAG + model-decided web) into `state.recalled_context`; it calls the
  LM, so it must run inside the scoped `dspy` context.

## TRUST BOUNDARY

The contract + tests tiers EXECUTE LLM-generated code in a subprocess as the
invoking user with full network + filesystem access — process isolation, not a
sandbox. Every gate path runs in a fresh throwaway temp dir; `write_files`
refuses escaping paths. The smith targets pure tools — don't point its contract
fixture at a tool with real external side effects, and don't put secrets in
fixture values (stored verbatim in the trainset row).

## Work Guidance

- No cheats/hardcoding/demo-data — the contract tier exists precisely so a
  trivially-passing generated test can't land a tool that doesn't actually run.
- Subprocesses use `sys.executable`; argv is always list-form (no `shell=True`).

## Verification

```
uv run ruff check src/stargraph/skills/toolsmith/ tests/integration/toolsmith/
uv run pyright src/stargraph/skills/toolsmith/ tests/integration/toolsmith/
uv run pytest tests/integration/toolsmith/
```
