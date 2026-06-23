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

The domain-agnostic machinery (build loop, gate tiers, ledger, RAG primitives,
web, LM, the DSPy generator shell, `SmithSpec`) lives in `../_smith/` (see its
AGENTS.md). This package is the *node* plug-in: signature, `coerce`, contract
driver, corpus, seeds, state, graph wiring. Keep node-specific code here and
generic code in `_smith`.

## Local Contracts

- `gate.py::run_full_gate` — node binding of `_smith.gate.run_tiered_gate`:
  static (compile + `ruff --select F`) → contract (subprocess import + zero-arg
  construct + run `execute()` on a fixture, assert dict output ⊆ declared writes)
  → tests (pytest). Short-circuits on first fail. `all_passed` requires all three
  tiers present and green. The node contract driver (`_CONTRACT_DRIVER`) is the
  only domain-specific tier; static + tests come from `_smith`.
- `program.py::NodeProgram` — `_smith.program.SmithProgram` bound to
  `NodeSignature` + the node `coerce`. `forward` returns the raw prediction
  (optimizer needs it); `generate` returns the coerced dict (build node needs it).
  Auto-loads `compiled.json` demos at construction.
- `nodes/build.py` — `NODE_SPEC` (a `_smith.spec.SmithSpec`, the full per-domain
  plug-in: artifact files, gate, recall sources, landing/trainset) + `Build`, a
  thin `_smith.build.SmithBuild` subclass that constructs `NodeProgram()` by name
  (so tests monkeypatch it). `nodes/{triage,recall,record}.py` are no-arg
  subclasses of the shared `_smith.nodes` lifecycle nodes, each binding `NODE_SPEC`.
  All loop/lifecycle logic lives in `_smith`; only the spec is node-specific.
- `state.py::State` — subclasses `_smith.state.SmithState` (the generic spine),
  adding only the node fields `node_name`, `class_name`, `reads`, `writes`, `fixture`.
- `_ledger.py` — binds `_smith.ledger.Ledger` to `NODESMITH_HOME` (default
  `.stargraph/nodesmith/`, absolute). `recall_lessons` / `recall_examples` feed idea 1;
  `append_trainset` / `drift_rate` / `load_compiled_demos` feed idea 2. Trainset
  rows carry `id` (uuid7), `source` (`seed`/`generated`/`edited`), and `verdict`
  (`accept`/`reject`/`None`). CRUD (`find`/`update`/`delete`, prefix-matched) and
  `seed_trainset` rewrite the file atomically (temp + `replace`). `drift_rate`
  counts only `generated` rows, so seeds/golds don't flatter the signal.
- `gate.py::verify_sources` — gate a pair of raw source strings in a throwaway
  temp dir; the shared entry for edit-to-gold, `make`, the doctor, and seed checks.
  `gate.py::run_node` — execute the node once on caller-supplied inputs in the
  same subprocess isolation and return the actual output values + how they line
  up with the declared writes (the Test tab's "Run node").
- `seeds.py::SEEDS` — hand-authored, gate-verified cold-start pairs with fixed
  literal ids (so re-seeding is idempotent). `test_seeds.py` gates every one.
- `_curate.py` — the single edit-to-gold implementation (`MARKER`,
  `build_edit_buffer`, `apply_edit`, `short_id`) shared by CLI + TUI; front-ends
  differ only in how they open `$EDITOR` and report results.
- `cli.py` (`nodesmith` console script) — `doctor`, `seed`, `make`, and
  `trainset {list,show,stats,label,edit,rm}`. `tui.py` (`NodesmithTUI`, Textual,
  optional `nodesmith` extra) is the interactive console with five tabs —
  **Generate** / **Curate** / **Test** / **Doctor** / **Stats** (`run_tui()`
  launches it):
  - **Generate** — settings on the left, output on the right (so the generated
    pair gets the vertical space). The model is a dropdown filled from Ollama's
    `/api/tags` (refresh button; free-text field as the fallback when the server
    is down); editable knobs are temperature + context length (`num_ctx`) + max
    tokens; the endpoint is not a field (defaults to local Ollama). The
    generate→gate→repair loop (LLM behind `Build._program`) runs in a worker
    **thread** so the loading spinner animates and `Build`'s `on_progress`
    callback can stream per-attempt phase lines to the log. When the generator
    is unsure it asks a clarifying question (`program.clarify`) — pre-flight on
    an ambiguous brief and again if the repair loop gets stuck — surfaced as a
    `ClarifyModal` (`push_screen_wait`): the model's concrete answers as buttons
    plus a free-text fallback. The answer is folded into the brief and (on the
    stuck path) generation retries once.
  - **Test** — verify the current node (last generated, else the highlighted
    Curate row): one input box per declared read (prefilled from the fixture) →
    **Run node** (`gate.run_node`, shows output vs declared writes) and **Full
    gate** (`verify_sources`, static → contract → tests). Both run off-thread.
- `program.py::make_lm` / `configure_lm` — build the DSPy LM via litellm's native
  `ollama_chat` provider (default `http://localhost:41001`), not the `/v1`
  OpenAI-compat shim, because only the native `/api/chat` path honors `num_ctx`.
  `make_lm` returns an LM (TUI worker scopes it with `dspy.context`); `configure_lm`
  sets the process-global LM (`make` CLI + optimizer, both main-task callers).
  Each knob is sent only when set, so unset ones fall back to the model default.
- `retrieval.py` — RAG grounding: `retrieve_context(brief)` ranks (lexically;
  embedding-pluggable) the real `NodeBase` contract + sibling node source under
  `stargraph.nodes` AND gate-accepted ledger pairs (`_ledger.recall_examples`),
  `format_context` renders the prompt block. Injected as the signature's
  `relevant_context` input. Best-effort — unreadable files just yield fewer snippets.
- `web.py` — optional, model-decided web research. `_decide(brief)` (a
  `dspy.Predict`) gates *whether* to search; `web_search`/`web_fetch` are keyless
  best-effort httpx (DuckDuckGo HTML), behind one `_http_get` seam tests stub;
  `research(brief)` returns `Snippet`s folded into the same `relevant_context`.
  Any failure / `needs=False` → `[]`, so the network never blocks a build. The
  Recall node runs it, so Recall must execute inside the scoped `dspy` context.
- `program.py::clarify` — best-effort `dspy.Predict` returning
  `{needs, question, options}`; any model/transport error → `needs=False`, so a
  clarify outage never blocks generation (the gate still guards correctness).
  Caller scopes the LM with `dspy.context`. Tests stub it (autouse fixture
  defaults to `needs=False`; the clarify journeys force `needs=True`).
- `_doctor.py::run_doctor` — preflight that proves the toolchain (python, pytest,
  ruff, write, dspy) and runs a probe node through the gate end-to-end.
- Graph order is linear (`graph.yaml`, `rules: []`): triage → recall → build →
  record. The repair loop lives INSIDE `build` because `stargraph run` walks
  nodes linearly (no rule engine) — do not move it into graph routing. `recall`
  gathers all grounding (reflexion lessons + RAG context + model-decided web
  research) into `state.recalled_context`; `build` passes it to `generate`.
- `Build._program` is the stub seam for tests; keep generation behind it.

## TRUST BOUNDARY

Tiers 2–3 EXECUTE LLM- or human-edited code in a subprocess as the invoking user
with full network + filesystem access — process isolation, not a sandbox. Every
path through the gate (build loop, `verify_sources`, edit-to-gold, doctor) runs
in a fresh throwaway temp dir (`tempfile.mkdtemp`/`TemporaryDirectory`, cleaned);
`write_files` refuses paths that escape it. Edited source is only persisted (to
the `NODESMITH_HOME` JSONL) after it passes the gate. Don't run nodesmith
privileged; don't put secrets in `fixture` values (they are stored verbatim in
the trainset row).

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
uv run nodesmith make "<brief>" --lm-model laguna-xs   # Ollama @ localhost:41001 by default
uv run nodesmith trainset list|stats    # review
uv run nodesmith trainset label <id> --accept|--reject [--reason ...]
uv run nodesmith trainset edit <id>     # edit-to-gold ($EDITOR, re-gated)
uv run nodesmith tui                    # console: Generate | Curate | Test | Doctor | Stats
```

Idea-2 ops (no LLM needed for drift):

```
uv run python scripts/nodesmith_optimize.py drift
uv run python scripts/nodesmith_optimize.py bench   --models laguna-xs,gemma3,gpt-oss:20b
uv run python scripts/nodesmith_optimize.py compile --lm-model laguna-xs
```
