# AGENTS.md — _smith (shared smith core)

## Purpose

The domain-agnostic machinery every "smith" reuses to build one kind of Stargraph
artifact from a brief and improve at it over time. A smith = this core + a thin
per-domain plug-in. `nodesmith` is the reference instance; `tool-smith`, store,
trigger, adapter, `mlsmith` (leaves) and the composites `graphsmith` (runnable graph
bundle), `skillsmith` (graph bundle + Skill manifest), and `pluginsmith` (a
registerable plugin: a `@tool` callable + pluggy hooks) ride the same core. The full
smith family is in place.

Two coupled loops (same as nodesmith's): **idea 1** — bounded generate → gate →
repair (only gate-passing artifacts land); **idea 2** — every run feeds a file
ledger (reflexion lessons + a spec→artifact trainset + a drift signal) that an
offline optimizer compiles few-shot demos from.

## Ownership

Owns the reusable loop, all four lifecycle nodes (triage → recall → build →
record), the state spine, gate tiers, ledger, RAG primitives, web research, LM
construction, the DSPy generator shell, and the `SmithSpec` seam. Owns nothing
domain-specific: a smith supplies its DSPy signature, `coerce`, contract driver,
corpus, seeds, and graph wiring, then subclasses `SmithState` (domain fields) and
binds one `SmithSpec` into thin no-arg node subclasses. The generic triage /
recall / record nodes were driven out by the second instance (tool-smith), where
the seam became visible: all three now read only the `SmithState` spine and route
every domain decision through the spec.

## Local Contracts

- `spec.py::SmithSpec` — the per-domain plug-in every lifecycle node runs against;
  frozen dataclass, one per smith. Build: `name`, `artifact_filenames=(source,
  test)`, `artifact_files(gen) -> {filename: source}`, `gate(work, files, gen) ->
  results`, `summary_fields(gen) -> state fields`. Recall: `recall_lessons(brief,
  *, limit)`, `retrieve_context(brief, *, k)`. Record: `landed_stem(state)`,
  `trainset_fields(state) -> row`, `append_lesson(**kw)`, `append_trainset(row)`.
  Landing shape (optional): `bundle_files` (when set, land these filenames verbatim
  into `output_dir/<stem>/` — the composite/multi-file case; empty ⇒ the flat
  two-file landing) + `entry_file` (which bundle file's path `_land` returns).
- `state.py::SmithState` — the generic run-state spine (`brief`/`model_id`/
  `output_dir`, recalled grounding, the build outputs common to all smiths,
  `landed_path`). A smith subclasses it and adds only its domain output fields.
- `nodes.py` — the three shared lifecycle nodes around `SmithBuild`: `SmithTriage`
  (reject empty briefs), `SmithRecall` (lessons + RAG + model-decided web →
  `recalled_*`), `SmithRecord` (terminal: on pass log the trainset pair + land the
  files via `snake(landed_stem)`, on fail log a summary lesson). `_land` lands a
  multi-file bundle into `output_dir/<stem>/` when the spec sets `bundle_files`, else
  the flat `<stem>.py` + `test_<stem>.py` — so composites need no `_land` override.
  Each is `__init__(*, spec)`; a smith binds its spec in a no-arg subclass the graph
  loader names.
- `build.py::SmithBuild` — the bounded generate → gate → repair loop as one
  `NodeBase`. Constructed with a built `program` instance + a `SmithSpec`. The
  loop is identical for every domain; only the spec differs. `_program` is the
  stub seam for tests — a smith subclass constructs its program by reading its
  own module global so tests can monkeypatch it. Runs in one node so the loop
  closes under plain `stargraph run` (linear routing, no rule engine).
- `program.py::SmithProgram` — the one `dspy.Module` shell. `forward` returns the
  raw prediction (optimizer needs it); `generate` returns the coerced dict (build
  loop needs it). Constructed with `signature` + `coerce` + `load_compiled_demos`;
  auto-loads compiled demos at construction. `as_list`/`as_dict` are the shared
  coerce helpers; `INPUT_FIELDS` is the stored-demo input set.
- `gate.py` — the three-tier gate machinery. `run_tiered_gate(work, files,
  contract_tier=, test_file=)` runs static (compile + `ruff --select F`) →
  contract (smith-supplied driver) → tests (pytest), short-circuiting on first
  fail. `all_passed` requires all three tiers present and green.
  `make_contract_tier(driver_src, payload)` wraps a smith's self-contained driver
  + JSON payload into the `contract_tier` callable (subprocess run + timeout →
  failed result + verdict mapping), so a smith's `run_full_gate` supplies only its
  driver string + the payload its fixture builds. `run_driver` runs a driver +
  payload in a subprocess and parses its verdict line; `contract_from_verdict`
  maps `(rc, verdict, out)` to a result. `write_files` refuses escaping paths.
  `RUN_GRAPH_PRELUDE` is the shared contract-driver prelude for any smith whose
  artifact is a runnable graph bundle (load IR → run to terminal `done` → assert the
  fixture's `expects`); graphsmith + skillsmith concatenate their own
  verdict/extension after it and pass `meta={run_id, noun}` in the payload.
- `ledger.py::Ledger` — append-only JSONL bound to one smith's `*_HOME` dir (read
  fresh each call, resolved absolute). `recall_lessons`/`recall_examples` feed
  idea 1; `append_trainset`/`drift_rate`/`load_compiled_demos` feed idea 2. CRUD
  (`find`/`update`/`delete`, prefix-matched) + `seed_trainset` rewrite atomically.
  `drift_rate` counts only `generated` rows. `read_jsonl` is the public reader.
- `retrieval.py` — RAG primitives: `Snippet`, `rank` (lexical token-overlap, top-k
  stable), `interleave`, `clip`, `format_context`. `assemble_context(brief, *, k,
  repo_snippets, recall_examples, source_field)` is the shared `retrieve_context`
  body — empty-brief guard + ledger-pair rendering (`# brief: …` + accepted source
  under `source_field`) + interleave; a smith passes only its repo corpus callable
  + its ledger source field. The corpus itself is per-domain (a smith ranks its own
  repo source + ledger pairs through these).
- `web.py` — optional, model-decided web research. `_decide(brief)` gates whether
  to search; `web_search`/`web_fetch` are keyless best-effort httpx (DuckDuckGo
  HTML) behind one `_http_get` seam; `research(brief)` returns `Snippet`s. Any
  failure / `needs=False` → `[]`, so the network never blocks a build.
- `lm.py` — `make_lm`/`configure_lm` build the DSPy LM via litellm's native
  `ollama_chat` provider (default `http://localhost:41001`), the only path that
  honors `num_ctx`. `clarify(brief, prior_findings)` is a best-effort
  `dspy.Predict` returning `{needs, question, options}`; any error → `needs=False`.

## TRUST BOUNDARY

The contract + tests tiers EXECUTE LLM-generated code in a subprocess as the
invoking user with full network + filesystem access — process isolation, not a
sandbox. Callers run every tier in a fresh throwaway temp dir; `write_files`
refuses any escaping path. Don't run a smith privileged; don't put secrets in
fixture/input values.

## Work Guidance

- No cheats/hardcoding/demo-data — the contract tier exists precisely so a
  trivially-passing generated test can't get a non-running artifact landed.
- Subprocesses use `sys.executable` (not `uv run`) so `import stargraph` resolves
  regardless of cwd; argv is always list-form (no `shell=True`).
- Keep the core domain-agnostic: anything that names nodes/tools/etc. belongs in
  the smith's plug-in, not here.
- Model compatibility: generation relies on DSPy's chat adapter parsing the
  signature's output fields. Capable instruction-followers (e.g. devstral,
  gpt-oss) work; some models return malformed/label-only fields the adapter
  mis-parses (gemma3 echoed field labels as content). State the artifact/output
  contract explicitly in each domain signature — weak models otherwise guess
  conventions (e.g. import paths) and the gate correctly rejects them.

## Verification

```
uv run ruff check src/stargraph/skills/_smith/
uv run pyright src/stargraph/skills/_smith/
```

No standalone tests — the core is exercised through each smith's suite
(`tests/integration/{nodesmith,toolsmith,storesmith,triggersmith,adaptersmith,mlsmith,packsmith,graphsmith,skillsmith,pluginsmith}/`).

## Child DOX Index

Per-domain smiths that consume this core:
- `../nodesmith/AGENTS.md` — node smith (reference instance).
- `../toolsmith/AGENTS.md` — tool smith (second instance; proves the core generalizes).
- `../storesmith/AGENTS.md` — store smith (leaf; DocStore protocol, sqlite round-trip gate).
- `../triggersmith/AGENTS.md` — trigger smith (leaf; manual variant, scheduler-delegation gate).
- `../adaptersmith/AGENTS.md` — adapter smith (leaf; MCP seam, translate/validate/sanitize/capability gate).
- `../mlsmith/AGENTS.md` — ML smith (leaf; targets the MLNode/FR-30 archetype, emits a trainer for the sklearn or onnx runtime, gated by running the trainer → constructing a live sha-pinned MLNode → predicting on the fixture).
- `../packsmith/AGENTS.md` — pack smith (leaf; targets the Bosun rule-pack archetype, emits a CLIPS governance pack + assembled descriptors, gated by loading the rules into a live Fathom engine → firing → matching the action → signing + verifying the tree).
- `../graphsmith/AGENTS.md` — graph smith (first composite; emits a multi-file bundle, gated by loading + running the assembled graph end-to-end).
- `../skillsmith/AGENTS.md` — skill smith (composite; graph bundle + Skill manifest, gated by running the subgraph AND constructing a valid registerable Skill; reuses graphsmith's assemble/run helpers).
- `../pluginsmith/AGENTS.md` — plugin smith (composite; a single `plugin.py` = a `@tool` callable + pluggy `@hookimpl`s, gated by registering it on an isolated `PluginManager` and driving its hooks + tool for real — advertise, compute, first-deny authorize, audit).
