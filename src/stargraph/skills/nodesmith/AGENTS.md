# AGENTS.md — nodesmith

## Purpose

A Stargraph graph that builds Stargraph nodes from a natural-language brief, and
improves at it over time. Two coupled loops:

- **Idea 1 — always works:** a bounded generate → verify → repair loop. The LLM
  writes a node + its pytest; a 3-tier gate must pass or the loop retries with
  the failure fed back, up to `max_attempts`. Only gate-passing nodes are landed.
- **Idea 2 — self-improvement:** every run appends to a file ledger (reflexion
  lessons + a spec→node trainset + a drift signal). An offline script benches
  candidate models and compiles few-shot demos that the build node auto-loads.

## Ownership

Owns node generation only. The runtime it generates *for* (NodeBase, State,
graph loop) is owned upstream in `src/stargraph/`. The gate is the single source
of truth for "a node works" and is shared verbatim by the build node and the
optimizer — keep it that way so the optimization metric == the ship criterion.

## Local Contracts

- `gate.py::run_full_gate` — static (compile + `ruff --select F`) → contract
  (subprocess import + zero-arg construct + run `execute()` on a fixture, assert
  dict output ⊆ declared writes) → tests (pytest). Short-circuits on first fail.
  `all_passed` requires all three tiers present and green.
- `program.py::NodeProgram` — the one `dspy.Module`. `forward` returns the raw
  prediction (optimizer needs it); `generate` returns the coerced dict (build
  node needs it). Auto-loads `compiled.json` demos at construction.
- `_ledger.py` — append-only JSONL under `NODESMITH_HOME` (default
  `.stargraph/nodesmith/`). `recall_lessons` feeds idea 1; `append_trainset` /
  `drift_rate` / `load_compiled_demos` feed idea 2.
- Graph order is linear (`graph.yaml`, `rules: []`): triage → recall → build →
  record. The repair loop lives INSIDE `build` because `stargraph run` walks
  nodes linearly (no rule engine) — do not move it into graph routing.
- `Build._program` is the stub seam for tests; keep generation behind it.

## TRUST BOUNDARY

Tiers 2–3 EXECUTE LLM-generated code in a subprocess as the invoking user with
full network + filesystem access — process isolation, not a sandbox. Generation
runs in a fresh per-run temp dir (`build.py`, `tempfile.mkdtemp`, cleaned in
`finally`); `write_files` refuses paths that escape it. Don't run nodesmith
privileged; don't put secrets in `fixture` values.

## Work Guidance

- No cheats/hardcoding/demo-data — the contract tier exists precisely so a
  trivially-passing generated test can't get a non-running node landed.
- Subprocesses use `sys.executable` (not `uv run`) so `import stargraph`
  resolves regardless of cwd; argv is always list-form (no `shell=True`).

## Verification

```
uv run ruff check src/stargraph/skills/nodesmith/ scripts/nodesmith_optimize.py tests/integration/nodesmith/
uv run pyright src/stargraph/skills/nodesmith/ tests/integration/nodesmith/
uv run pytest tests/integration/nodesmith/
```

Idea-2 ops (no LLM needed for drift):

```
uv run python scripts/nodesmith_optimize.py drift
uv run python scripts/nodesmith_optimize.py bench   --lm-url "$LLM_OLLAMA_URL" --models laguna-xs,gemma3,gpt-oss:20b
uv run python scripts/nodesmith_optimize.py compile --lm-url "$LLM_OLLAMA_URL" --lm-model laguna-xs
```
