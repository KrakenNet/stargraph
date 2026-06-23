# AGENTS.md — storesmith

## Purpose

A Stargraph graph that builds Stargraph **stores** from a natural-language brief,
and improves at it over time. Another instance of the shared smith core
(`../_smith/`) — it proves the core generalizes to stateful artifacts. Same two
loops as nodesmith/toolsmith: idea 1 (bounded generate → gate → repair; only
gate-passing stores land) and idea 2 (file ledger of reflexion lessons + a
spec→store trainset + drift; offline compile of few-shot demos).

## Ownership

Owns store generation only. The runtime it generates *for* (the `DocStore`
protocol, `Document`, `StoreHealth`, `MigrationPlan`, `MigrationNotSupported`) is
owned upstream in `src/stargraph/stores/` + `src/stargraph/errors/`. The
domain-agnostic machinery (build loop, gate tiers, ledger, RAG primitives, web,
LM, the DSPy generator shell, `SmithSpec`) lives in `../_smith/` (see its
AGENTS.md). This package is the *store* plug-in: signature, `coerce`, contract
driver, corpus, seeds, state, graph wiring. Keep store-specific code here.

## Local Contracts

- `gate.py::run_full_gate` — store binding of `_smith.gate.run_tiered_gate`:
  static (compile + `ruff --select F`) → **contract** → tests (pytest). The
  contract driver (subprocess) imports the module, finds the single class whose
  `__module__` is the module and that defines all of
  `{bootstrap,health,migrate,put,get,query}`, constructs it on a tmpfile sqlite
  DB, and drives one `asyncio.run` exercising the whole round trip: bootstrap;
  `health()` is a `StoreHealth` with `ok is True` and an int version; `put` then
  `get` returns the same content + metadata (real persistence); `get` of an
  absent id is `None`; `query` surfaces the doc; a second `put` replaces
  (INSERT OR REPLACE); and `migrate` of a non-`add_column` plan raises
  `MigrationNotSupported`. Those behavioral asserts are the un-cheatable floor —
  a trivially-passing generated test can't land a store that doesn't persist,
  doesn't replace, or skips the migrate guard. `verify_sources` gates raw source
  strings in a throwaway temp dir (the entry for `make`, the doctor, seed checks).
- `program.py::StoreProgram` — `_smith.program.SmithProgram` bound to
  `StoreSignature` + the store `coerce`. Generation emits `class_name`, `fixture`
  (doc_id/content/content2/metadata), `store_source` (`store.py`), `test_source`
  (`test_store.py`). Auto-loads `compiled.json` demos at construction.
- `nodes/build.py` — `STORE_SPEC` (a `_smith.spec.SmithSpec`, the full per-domain
  plug-in: artifact files, gate, recall sources, landing/trainset) + `Build`, a
  thin `_smith.build.SmithBuild` subclass that constructs `StoreProgram()` by name
  (so tests monkeypatch it). `nodes/{triage,recall,record}.py` are no-arg
  subclasses of the shared `_smith.nodes` lifecycle nodes, each binding
  `STORE_SPEC`. All loop/lifecycle logic lives in `_smith`; only the spec is
  store-specific.
- `state.py::State` — subclasses `_smith.state.SmithState` (the generic spine),
  adding only the store fields `class_name`, `fixture` (the targeted protocol is
  fixed to `"doc"`).
- `_ledger.py` — binds `_smith.ledger.Ledger` to `STORESMITH_HOME` (default
  `.stargraph/storesmith/`, absolute).
- `retrieval.py` — RAG grounding: the `DocStore` contract (`doc.py` + `_common.py`,
  always) + the most relevant sibling store modules under `stargraph.stores` +
  gate-accepted ledger pairs. Injected as the signature's `relevant_context`.
- `seeds.py::SEEDS` — hand-authored, gate-verified cold-start stores distilled
  from `SQLiteDocStore`: a plain text round-trip and a rich-metadata + query-filter
  pair. `test_seeds.py` gates every one. Both are pure aiosqlite so the contract
  tier exercises them fully offline.
- Graph order is linear (`graph.yaml`, `rules: []`): triage → recall → build →
  record. The repair loop lives INSIDE `build`. `recall` gathers grounding
  (lessons + RAG + model-decided web) into `state.recalled_context`; it calls the
  LM, so it must run inside the scoped `dspy` context.

## TRUST BOUNDARY

The contract + tests tiers EXECUTE LLM-generated code in a subprocess as the
invoking user with full network + filesystem access — process isolation, not a
sandbox. Every gate path runs in a fresh throwaway temp dir; `write_files`
refuses escaping paths. The smith targets pure-sqlite stores rooted at a tmpfile
path — don't point its contract fixture at a store with real external side
effects, and don't put secrets in fixture values (stored verbatim in the trainset
row).

## Work Guidance

- No cheats/hardcoding/demo-data — the contract tier exists precisely so a
  trivially-passing generated test can't land a store that doesn't persist or
  replace, or that skips the migrate guard.
- Subprocesses use `sys.executable`; argv is always list-form (no `shell=True`).

## Verification

```
uv run ruff check src/stargraph/skills/storesmith/ tests/integration/storesmith/
uv run pyright src/stargraph/skills/storesmith/ tests/integration/storesmith/
uv run pytest tests/integration/storesmith/
```
