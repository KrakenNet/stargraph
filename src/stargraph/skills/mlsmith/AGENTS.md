# AGENTS.md — mlsmith

## Purpose

A Stargraph graph that builds whole Stargraph **ML model nodes** from a
natural-language brief, and improves at it over time. A leaf smith on the shared
core (`../_smith/`): it targets the `MLNode` archetype (FR-30) by emitting the
**trainer** that produces the model a node loads — a `trainer.py` defining
`build_model(path) -> None` that fits a small classical-ML model on an in-code
dataset and serializes it for one of the two shipped runtimes (`sklearn` via joblib,
`onnx` via skl2onnx export) — plus its test. Same two loops as the other smiths: idea
1 (bounded generate → gate → repair; only gate-passing trainers land) and idea 2
(file ledger of reflexion lessons + a brief→trainer trainset + drift).

## Ownership

Owns trainer generation only. The runtime it generates *for* — the `MLNode` contract
(`src/stargraph/nodes/ml.py`: runtime selection, the eager default-deny sklearn
pickle gate, `expected_sha256`, the input/output fields) and the runtime loaders
(`src/stargraph/ml/loaders.py`) — is owned upstream. The domain-agnostic machinery
(build loop, gate tiers, ledger, RAG, web, LM, the DSPy generator shell, `SmithSpec`)
lives in `../_smith/`. Like the other leaves a trainer is a single authored module
(no auto-assembled wiring file) and does not run a graph, so it has its own contract
driver (it does NOT use `_smith.gate.RUN_GRAPH_PRELUDE`). Wiring the trained model
into a graph as an `MLNode` (runtime/input/output/`file_uri`/sha live on the trainset
row) is the orchestrator's job (Phase D). This package adds: the train→load→predict
contract gate, the signature/coerce, the contract corpus, seeds, state, graph wiring,
and the spec's bundle landing config. No node overrides — the lifecycle is wholly
shared.

## Local Contracts

- `gate.py` — ML binding of `_smith.gate.run_tiered_gate`: static → **contract** →
  tests. The contract driver (subprocess) RUNS the generated `trainer.py`'s
  `build_model(path)` to produce a real model file, computes its sha256, constructs a
  live `stargraph.nodes.ml.MLNode` against it (`runtime=` the declared runtime,
  `allow_unsafe_pickle=True` for sklearn, `expected_sha256=` the just-computed hash so
  the pin is verified before any unpickle, `input_field`/`output_field` as declared),
  and runs `execute()` on `fixture.input` — asserting the output matches
  `fixture.expects` (numpy `predict` arrays are `.tolist()`-normalized;
  `expects=None` only requires a non-empty prediction). Because the assert runs on a
  live MLNode, a trivially-passing test can't land a trainer whose model doesn't
  serialize, load (sha-pinned), or predict. `verify_sources` (keyword-only) gates raw
  source strings in a throwaway temp dir.
- `program.py::MLProgram` — `_smith.program.SmithProgram` bound to `MLSignature` + the
  ML `coerce`. Generation emits `model_name`, `runtime` (`sklearn`|`onnx`),
  `input_field`, `output_field`, `trainer_source`, `fixture`
  (`input`/`expects`), `test_source`. The signature pins the per-runtime
  serialization (joblib vs skl2onnx) and the per-runtime fixture shapes (sklearn:
  batched 2-D input → predict-array list; onnx: rank-1 vector → unwrapped scalar).
- `nodes/build.py` — `ML_SPEC` (a `_smith.spec.SmithSpec`: the two-file
  `artifact_files`, the gate binding `meta`={runtime,input_field,output_field}+fixture,
  recall sources, landing/trainset) + `Build`, a thin `_smith.build.SmithBuild`
  subclass constructing `MLProgram()` by name.
- `nodes/{triage,recall,record}.py` — no-arg subclasses of the shared `_smith.nodes`
  lifecycle nodes binding `ML_SPEC`; no method overrides. Landing the two files with
  their FIXED names (`trainer.py` + `test_trainer.py`, so the test's
  `from trainer import …` resolves) under `output_dir/<stem>/` (entry = `trainer.py`)
  is driven by `ML_SPEC.bundle_files` + `entry_file` through the shared
  `SmithRecord._land`.
- `state.py::State` — subclasses `_smith.state.SmithState`, adding `model_name`,
  `runtime`, `input_field`, `output_field`, `fixture`.
- `_ledger.py` — binds `_smith.ledger.Ledger` to `MLSMITH_HOME` (default
  `.stargraph/mlsmith/`, absolute).
- `retrieval.py` — RAG grounding: two fixed contracts (the `MLNode` contract, the
  runtime loaders) + gate-accepted ledger trainers. Injected as `relevant_context`.
- `seeds.py::SEEDS` — hand-authored, gate-verified cold-start trainers, one per
  runtime (a sklearn `DecisionTreeClassifier` via joblib; the same classifier exported
  to ONNX). `test_seeds.py` gates every one.
- Graph order is linear (`graph.yaml`, `rules: []`): triage → recall → build →
  record. The repair loop lives INSIDE `build`. `recall` calls the LM.

## TRUST BOUNDARY

The contract + tests tiers EXECUTE LLM-generated code in a subprocess as the invoking
user with full network + filesystem access — process isolation, not a sandbox. The
contract tier additionally RUNS the generated trainer AND loads the model file it
produces via `MLNode` with `allow_unsafe_pickle=True` for the sklearn runtime — i.e.
it unpickles (joblib) a file produced by the same untrusted trainer in that
subprocess. This is no wider than already running the trainer (same process, same
trust). Every gate path runs in a fresh throwaway temp dir; `_smith.gate.write_files`
refuses escaping paths. Don't run a smith as a privileged user, don't put secrets in
fixture/input values, and treat a generated trainer + its model as untrusted code.

## Work Guidance

- No cheats/hardcoding/demo-data — the contract tier exists precisely so a
  trivially-passing test can't land a trainer whose model doesn't serialize, load
  (sha-pinned), or predict on a live `MLNode`.
- Keep generated models tiny + deterministic (in-code dataset, `random_state=0`, no
  file/network reads) so the fixture's expected prediction is stable.
- Subprocesses use `sys.executable`; argv is always list-form (no `shell=True`).
- Requires the `ml` extra (scikit-learn, joblib, skl2onnx, onnxruntime) installed;
  the gate's contract + test tiers import them.

## Verification

```
uv run ruff check src/stargraph/skills/mlsmith/ tests/integration/mlsmith/
uv run pyright src/stargraph/skills/mlsmith/ tests/integration/mlsmith/
uv run pytest tests/integration/mlsmith/ --override-ini=addopts=
```
