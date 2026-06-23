# AGENTS.md — triggersmith

## Purpose

A Stargraph graph that builds Stargraph **triggers** from a natural-language
brief, and improves at it over time. Another instance of the shared smith core
(`../_smith/`). Same two loops as nodesmith/toolsmith: idea 1 (bounded generate →
gate → repair; only gate-passing triggers land) and idea 2 (file ledger of
reflexion lessons + a spec→trigger trainset + drift; offline compile of few-shot
demos). Targets the **manual** trigger variant only (synchronous, offline;
cron/webhook are future).

## Ownership

Owns trigger generation only. The runtime it generates *for* (the `Trigger`
Protocol, `TriggerEvent`, the scheduler) is owned upstream in
`src/stargraph/triggers/` + `src/stargraph/serve/`. The domain-agnostic machinery
(build loop, gate tiers, ledger, RAG primitives, web, LM, the DSPy generator
shell, `SmithSpec`) lives in `../_smith/` (see its AGENTS.md). This package is the
*trigger* plug-in: signature, `coerce`, contract driver, corpus, seeds, state,
graph wiring. Keep trigger-specific code here.

## Local Contracts

- `gate.py::run_full_gate` — trigger binding of `_smith.gate.run_tiered_gate`:
  static (compile + `ruff --select F`) → **contract** → tests (pytest). The
  contract driver (subprocess) imports the module, finds the single trigger class
  (one defining all of `init`/`start`/`stop`/`routes`/`enqueue`), constructs it
  zero-arg, asserts `init({})` raises `StargraphRuntimeError` (a real guard, not a
  pass-stub), wires a recording stub scheduler DEFINED IN THE DRIVER (the
  candidate cannot override it), calls `enqueue(graph_id, params)`, and asserts
  the trigger DELEGATED (exactly one recorded scheduler call with the right
  `graph_id` + `params`) and RETURNED the scheduler's `run_id`
  (`"run-FIXED-123"`). That delegation + returned-run_id check is the un-cheatable
  floor — a trivially-passing generated test can't land a no-op init or a faked
  `enqueue` that never reaches the scheduler. `verify_sources` gates raw source
  strings in a throwaway temp dir (the entry for `make`, the doctor, and seed
  checks).
- `program.py::TriggerProgram` — `_smith.program.SmithProgram` bound to
  `TriggerSignature` + the trigger `coerce`. Generation emits `class_name`,
  `fixture` (`{graph_id, params}`), `trigger_source` (`trigger.py`), `test_source`
  (`test_trigger.py`). Auto-loads `compiled.json` demos at construction.
- `nodes/build.py` — `TRIGGER_SPEC` (a `_smith.spec.SmithSpec`, the full
  per-domain plug-in: artifact files, gate, recall sources, landing/trainset) +
  `Build`, a thin `_smith.build.SmithBuild` subclass that constructs
  `TriggerProgram()` by name (so tests monkeypatch it). `nodes/{triage,recall,
  record}.py` are no-arg subclasses of the shared `_smith.nodes` lifecycle nodes,
  each binding `TRIGGER_SPEC`. All loop/lifecycle logic lives in `_smith`; only
  the spec is trigger-specific.
- `state.py::State` — subclasses `_smith.state.SmithState` (the generic spine),
  adding only the trigger fields `variant`, `class_name`, `fixture`.
- `_ledger.py` — binds `_smith.ledger.Ledger` to `TRIGGERSMITH_HOME` (default
  `.stargraph/triggersmith/`, absolute).
- `retrieval.py` — RAG grounding: the `Trigger` Protocol (`triggers/__init__.py`,
  always) + the most relevant sibling trigger modules under `stargraph.triggers` +
  gate-accepted ledger pairs. Injected as the signature's `relevant_context`.
- `seeds.py::SEEDS` — hand-authored, gate-verified cold-start manual triggers
  (basic delegation; idempotency-key passthrough), distilled from
  `ManualTrigger`. `test_seeds.py` gates every one. Triggers are pure (no real
  scheduler, no network) so the contract tier runs them offline against an
  in-driver recording stub.
- Graph order is linear (`graph.yaml`, `rules: []`): triage → recall → build →
  record. The repair loop lives INSIDE `build`. `recall` gathers grounding
  (lessons + RAG + model-decided web) into `state.recalled_context`; it calls the
  LM, so it must run inside the scoped `dspy` context.

## TRUST BOUNDARY

The contract + tests tiers EXECUTE LLM-generated code in a subprocess as the
invoking user with full network + filesystem access — process isolation, not a
sandbox. Every gate path runs in a fresh throwaway temp dir; `write_files`
refuses escaping paths. The smith targets the manual (synchronous, offline)
variant and never constructs a real `Scheduler` (which needs an event loop); the
contract tier wires its own recording stub. Don't put secrets in fixture values
(stored verbatim in the trainset row).

## Work Guidance

- No cheats/hardcoding/demo-data — the contract tier exists precisely so a
  trivially-passing generated test can't land a trigger whose `enqueue` doesn't
  actually delegate to the scheduler.
- Subprocesses use `sys.executable`; argv is always list-form (no `shell=True`).

## Verification

```
uv run ruff check src/stargraph/skills/triggersmith/ tests/integration/triggersmith/
uv run pyright src/stargraph/skills/triggersmith/ tests/integration/triggersmith/
uv run pytest tests/integration/triggersmith/
```
