# AGENTS.md — packsmith

## Purpose

A Stargraph graph that builds whole **Bosun rule packs** from a natural-language brief,
and improves at it over time. A leaf smith on the shared core (`../_smith/`): it targets
the Bosun rule-pack archetype by emitting a governance pack — a `rules.clp` (CLIPS
deftemplates + defrules that read an input fact and `assert` a decision/action fact) plus
the deterministically-assembled `pack.yaml` + `manifest.yaml` descriptors and a test.
Same two loops as the other smiths: idea 1 (bounded generate → gate → repair; only
gate-passing packs land) and idea 2 (file ledger of reflexion lessons + a brief→rules
trainset + drift).

## Ownership

Owns rule-pack generation only. The runtime it generates *for* — the Fathom CLIPS engine
(`fathom.Engine`), the pack signing/verification contract (`src/stargraph/bosun/signing.py`:
`sign_pack` / `verify_pack` / `StaticTrustStore`), and the verify profiles
(`src/stargraph/serve/profiles.py`) — is owned upstream. The domain-agnostic machinery
(build loop, gate tiers, ledger, RAG, web, LM, the DSPy generator shell, `SmithSpec`)
lives in `../_smith/`. Like the other leaves a pack is a single authored artifact (the
rules; the descriptors are auto-assembled) and does not run a graph, so it has its own
contract driver (it does NOT use `_smith.gate.RUN_GRAPH_PRELUDE`). Wiring the pack into a
graph (a Fathom engine spec) is the orchestrator's job (Phase D). This package adds: the
compile→fire→sign+verify contract gate, the descriptor assemblers, the signature/coerce,
the contract corpus, seeds, state, graph wiring, and the spec's bundle landing config. No
node overrides — the lifecycle is wholly shared.

## Local Contracts

- `gate.py` — pack binding of `_smith.gate.run_tiered_gate`: static → **contract** →
  tests. The contract driver (subprocess) (1) loads `rules.clp` into a real
  `fathom.Engine` via the native CLIPS whole-file load (`engine._env.load` — catches
  malformed rules/templates), (2) asserts the fixture's `input` fact on `input_template`,
  fires the engine (`_env.run`), reads `output_template` facts, and asserts one matches
  the fixture's `expects` slots, and (3) signs the assembled tree with an ephemeral
  Ed25519 key (`sign_pack`) and verifies it under a mandatory-verify `ClearedProfile`
  with a `StaticTrustStore` (`verify_pack`) — proving `rules.clp` + `pack.yaml` +
  `manifest.yaml` cohere as one tree-hash-verifiable pack. `assemble_pack_yaml` /
  `assemble_manifest_yaml` build the descriptors deterministically (correct by
  construction). `verify_sources` (keyword-only) assembles + gates a raw bundle in a
  throwaway temp dir. The static + tests tiers run only over the `.py` files
  (`test_pack.py`); `ruff` ignores the `.clp`/`.yaml`.
- `program.py::PackProgram` — `_smith.program.SmithProgram` bound to `PackSignature` +
  the pack `coerce`. Generation emits `pack_name`, `flavor` (governance/routing),
  `input_template`, `output_template`, `rules_clp`, `fixture` (`input`/`expects`),
  `test_source`. The signature embeds a concrete CLIPS shape + the `test_pack.py` loader
  recipe so weak models copy the contract rather than guess it.
- `nodes/build.py` — `PACK_SPEC` (a `_smith.spec.SmithSpec`: the four-file
  `artifact_files` — `rules.clp` + `test_pack.py` from the model, `pack.yaml` +
  `manifest.yaml` assembled — the gate binding `meta`={input/output template,pack_name}+
  fixture, recall sources, landing/trainset) + `Build`, a thin `_smith.build.SmithBuild`
  subclass constructing `PackProgram()` by name.
- `nodes/{triage,recall,record}.py` — no-arg subclasses of the shared `_smith.nodes`
  lifecycle nodes binding `PACK_SPEC`; no method overrides. Landing the four files under
  `output_dir/<stem>/` (so the test's `Path(__file__).with_name("rules.clp")` resolves;
  entry = `pack.yaml`, the descriptor a deployer points at) is driven by
  `PACK_SPEC.bundle_files` + `entry_file` through the shared `SmithRecord._land`.
- `state.py::State` — subclasses `_smith.state.SmithState`, adding `pack_name`, `flavor`,
  `input_template`, `output_template`, `fixture`.
- `_ledger.py` — binds `_smith.ledger.Ledger` to `PACKSMITH_HOME` (default
  `.stargraph/packsmith/`, absolute).
- `retrieval.py` — RAG grounding: a shipped CLIPS pack (`bosun/budgets/rules.clp`) + the
  signing contract (`bosun/signing.py`) + gate-accepted ledger packs. Injected as
  `relevant_context`.
- `seeds.py::SEEDS` — hand-authored, gate-verified cold-start packs: a risk-escalation
  governance pack and a budget-guard governance pack. `test_seeds.py` gates every one.
- Graph order is linear (`graph.yaml`, `rules: []`): triage → recall → build → record.
  The repair loop lives INSIDE `build`. `recall` calls the LM.

## TRUST BOUNDARY

The contract + tests tiers EXECUTE LLM-generated code in a subprocess as the invoking
user with full network + filesystem access — process isolation, not a sandbox. The
contract tier additionally compiles + FIRES the generated CLIPS rules on a Fathom engine
in that subprocess. CLIPS is not Python, but a rule engine still runs arbitrary matching
logic, and untrusted slot values are stripped of s-expression metacharacters before being
asserted. Every gate path runs in a fresh throwaway temp dir; `_smith.gate.write_files`
refuses escaping paths. Don't run a smith as a privileged user, don't put secrets in
fixture/input values, and treat a generated pack as untrusted.

## Work Guidance

- No cheats/hardcoding/demo-data — the contract tier exists precisely so a
  trivially-passing test can't land a pack whose rules don't compile, don't fire, or
  don't cohere as a signable unit.
- Bosun packs are CLIPS (`rules.clp`), loaded via the engine's native CLIPS path
  (`_env.load`), NOT `Engine.load_rules` (that parses Fathom's YAML ruleset DSL — a
  different artifact).
- The ephemeral sign+verify proves the tree is well-formed + signable; operators re-sign
  with their own key at deploy time. The gate does not ship a signature in the landed
  pack.
- Subprocesses use `sys.executable`; argv is always list-form (no `shell=True`).
- Requires `fathom` (the CLIPS engine) + `cryptography` installed; the gate's contract +
  test tiers import them.

## Verification

```
uv run ruff check src/stargraph/skills/packsmith/ tests/integration/packsmith/
uv run pyright src/stargraph/skills/packsmith/ tests/integration/packsmith/
uv run pytest tests/integration/packsmith/ --override-ini=addopts=
```
