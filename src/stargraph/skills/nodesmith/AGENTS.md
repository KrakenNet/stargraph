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

The **trainset is the substrate both loops + RAG/fine-tune feed on**, so it is
curatable: the `nodesmith` CLI + a Textual TUI let a human review each generated
pair and attach an accept/reject verdict (both feed the set), or edit-to-gold a
node (fix it, re-gate it, store the fix as a positive). Hand-verified seeds give
it a cold start. The TUI is the full console — *use* (Generate: run the
generate→gate→repair loop from a brief) **and** *tweak* (Curate / Doctor /
Stats) — not just the labeler.

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
  `.stargraph/nodesmith/`, absolute). `recall_lessons` feeds idea 1;
  `append_trainset` / `drift_rate` / `load_compiled_demos` feed idea 2. Trainset
  rows carry `id` (uuid7), `source` (`seed`/`generated`/`edited`), and `verdict`
  (`accept`/`reject`/`None`). CRUD (`find`/`update`/`delete`, prefix-matched) and
  `seed_trainset` rewrite the file atomically (temp + `replace`). `drift_rate`
  counts only `generated` rows, so seeds/golds don't flatter the signal.
- `gate.py::verify_sources` — gate a pair of raw source strings in a throwaway
  temp dir; the shared entry for edit-to-gold, `make`, the doctor, and seed checks.
- `seeds.py::SEEDS` — hand-authored, gate-verified cold-start pairs with fixed
  literal ids (so re-seeding is idempotent). `test_seeds.py` gates every one.
- `_curate.py` — the single edit-to-gold implementation (`MARKER`,
  `build_edit_buffer`, `apply_edit`, `short_id`) shared by CLI + TUI; front-ends
  differ only in how they open `$EDITOR` and report results.
- `cli.py` (`nodesmith` console script) — `doctor`, `seed`, `make`, and
  `trainset {list,show,stats,label,edit,rm}`. `tui.py` (`NodesmithTUI`, Textual,
  optional `nodesmith` extra) is the interactive console: a **Generate** tab
  drives the same `make` loop (LLM behind `Build._program`, run in a Textual
  worker), plus **Curate** / **Doctor** / **Stats** tabs. `run_tui()` launches it.
- `_doctor.py::run_doctor` — preflight that proves the toolchain (python, pytest,
  ruff, write, dspy) and runs a probe node through the gate end-to-end.
- Graph order is linear (`graph.yaml`, `rules: []`): triage → recall → build →
  record. The repair loop lives INSIDE `build` because `stargraph run` walks
  nodes linearly (no rule engine) — do not move it into graph routing.
- `Build._program` is the stub seam for tests; keep generation behind it.

## TRUST BOUNDARY

Tiers 2–3 EXECUTE LLM- or human-edited code in a subprocess as the invoking user
with full network + filesystem access — process isolation, not a sandbox. Every
path through the gate (build loop, `verify_sources`, edit-to-gold, doctor) runs
in a fresh throwaway temp dir (`tempfile.mkdtemp`/`TemporaryDirectory`, cleaned);
`write_files` refuses paths that escape it. Edited source is only persisted (to
the `NODESMITH_HOME` JSONL) after it passes the gate. Don't run nodesmith
privileged; don't put secrets in `fixture` values. `--lm-key` is passed only to
the LM client, never stored in a trainset row.

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

Curate the trainset (the `nodesmith` console script; `tui` needs the `nodesmith`
extra: `uv sync --extra nodesmith`):

```
uv run nodesmith doctor                 # prove run/test/verify/write toolchain
uv run nodesmith seed                   # load the gate-verified cold-start pairs
uv run nodesmith make "<brief>" --lm-url "$LLM_OLLAMA_URL" --lm-model laguna-xs
uv run nodesmith trainset list|stats    # review
uv run nodesmith trainset label <id> --accept|--reject [--reason ...]
uv run nodesmith trainset edit <id>     # edit-to-gold ($EDITOR, re-gated)
uv run nodesmith tui                    # console: Generate | Curate | Doctor | Stats
```

Idea-2 ops (no LLM needed for drift):

```
uv run python scripts/nodesmith_optimize.py drift
uv run python scripts/nodesmith_optimize.py bench   --lm-url "$LLM_OLLAMA_URL" --models laguna-xs,gemma3,gpt-oss:20b
uv run python scripts/nodesmith_optimize.py compile --lm-url "$LLM_OLLAMA_URL" --lm-model laguna-xs
```
