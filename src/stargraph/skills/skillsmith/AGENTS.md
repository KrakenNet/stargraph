# AGENTS.md — skillsmith

## Purpose

A Stargraph graph that builds whole Stargraph **skills** from a natural-language
brief, and improves at it over time. A composite smith on the shared core
(`../_smith/`), one rung above `graphsmith`: where graphsmith emits a runnable
graph bundle, skillsmith emits a registerable **skill** — that same subgraph bundle
plus a `Skill` manifest (kind, description, capability `requires`, optional
`system_prompt`, and the declared output boundary). Same two loops as the other
smiths: idea 1 (bounded generate → gate → repair; only gate-passing skills land)
and idea 2 (file ledger of reflexion lessons + a brief→skill trainset + drift).

## Ownership

Owns skill-bundle generation only. The runtime it generates *for* (the `Skill`
manifest contract in `src/stargraph/skills/base.py`, the `NodeBase`/`Graph`/IR
runtime, the plugin loader's `register_skills` path) is owned upstream. The
domain-agnostic machinery (build loop, gate tiers, ledger, RAG, web, LM, the DSPy
generator shell, `SmithSpec`) lives in `../_smith/`. The load-and-run contract
prelude is **shared from `_smith.gate` (`RUN_GRAPH_PRELUDE`)** and the graph-bundle
assembly is **reused from `../graphsmith/`** (`assemble_graph_yaml`); this package
adds only the *skill* layer: the manifest assembly, the `Skill`-construction contract
block, the signature/coerce, corpus, seeds, state, graph wiring, and the spec's
bundle landing config. No node overrides — the lifecycle is wholly shared.

## Local Contracts

- `gate.py` — skill binding of `_smith.gate.run_tiered_gate`: static → **contract**
  → tests. `assemble_graph_yaml` is imported from `../graphsmith/gate.py` (pure
  helper reuse). `assemble_manifest_yaml(skill_name, kind, description, requires,
  system_prompt)` wires the manifest with fixed `state_schema: state:State` +
  `subgraph: graph.yaml` refs (correct by construction; the model supplies only the
  domain fields). The contract driver is `RUN_GRAPH_PRELUDE` (from `_smith.gate`,
  shared with graphsmith) + a skill block: (1) the prelude loads the assembled
  subgraph into a real `Graph` and RUNS it to a terminal `done` (asserting
  `fixture.expects` are produced), THEN (2) the skill block reads `manifest.yaml`,
  resolves `state_schema`, and
  constructs a `stargraph.skills.base.Skill` — asserting it validates (valid `kind`,
  replay-safe `state_schema` with no `set` fields per the FR-23 validator) and that
  its `declared_output_keys` cover the fixture's expected fields. Both asserts are
  on real objects, so a trivially-passing test can't land a skill whose subgraph
  doesn't run or whose manifest isn't a valid, registerable skill. `verify_sources`
  (keyword-only) gates raw source strings in a throwaway temp dir.
- `program.py::SkillProgram` — `_smith.program.SmithProgram` bound to
  `SkillSignature` + the skill `coerce`. Generation emits `skill_name`, `kind`
  (agent|workflow|utility), `description`, `node_classes` (ordered, ≥2),
  `state_source`, `nodes_source`, `requires`, `system_prompt`, `fixture`,
  `test_source`. It does NOT emit `graph.yaml` or `manifest.yaml` (both assembled).
- `nodes/build.py` — `SKILL_SPEC` (a `_smith.spec.SmithSpec`: the five-file
  `artifact_files`, the gate, recall sources, landing/trainset) + `Build`, a thin
  `_smith.build.SmithBuild` subclass constructing `SkillProgram()` by name. The
  trainset row stores the domain fields (which regenerate the assembled files).
- `nodes/{triage,recall,record}.py` — no-arg subclasses of the shared `_smith.nodes`
  lifecycle nodes binding `SKILL_SPEC`; no method overrides. The five-file bundle
  landing under `output_dir/<stem>/` (entry = `manifest.yaml`, the skill's
  registration entry point) is driven by `SKILL_SPEC.bundle_files` + `entry_file`
  through the shared `SmithRecord._land`.
- `state.py::State` — subclasses `_smith.state.SmithState`, adding `skill_name`,
  `kind`, `description`, `node_classes`, `requires`, `system_prompt`, `fixture`.
- `_ledger.py` — binds `_smith.ledger.Ledger` to `SKILLSMITH_HOME` (default
  `.stargraph/skillsmith/`, absolute).
- `retrieval.py` — RAG grounding: the `Skill` contract (`skills/base.py`, always) +
  the `NodeBase` contract (`nodes/base.py`, always) + the most relevant sibling node
  modules + gate-accepted ledger bundles. Injected as `relevant_context`.
- `seeds.py::SEEDS` — hand-authored, gate-verified cold-start skills (a `workflow`
  normalize → classify; a `utility` tokenize → count). Both are two-node pipelines
  whose second node reads a channel the first wrote. `test_seeds.py` gates every one.
- Graph order is linear (`graph.yaml`, `rules: []`): triage → recall → build →
  record. The repair loop lives INSIDE `build`. `recall` calls the LM.

## TRUST BOUNDARY

The contract + tests tiers EXECUTE LLM-generated code in a subprocess as the
invoking user with full network + filesystem access — process isolation, not a
sandbox. The contract tier additionally LOADS and RUNS the generated subgraph and
constructs the generated `Skill` (its nodes' `execute` bodies + state model run in
that subprocess). Every gate path runs in a fresh throwaway temp dir;
`_smith.gate.write_files` refuses escaping paths. Don't run a smith as a privileged
user, don't put secrets in fixture/input values, and treat a generated bundle as
untrusted code.

## Work Guidance

- No cheats/hardcoding/demo-data — the contract tier exists precisely so a
  trivially-passing test can't land a skill whose subgraph doesn't run or whose
  manifest isn't a valid skill.
- `graph.yaml` + `manifest.yaml` are auto-assembled; never accept LLM-emitted ones.
- Subprocesses use `sys.executable`; argv is always list-form (no `shell=True`).

## Verification

```
uv run ruff check src/stargraph/skills/skillsmith/ tests/integration/skillsmith/
uv run pyright src/stargraph/skills/skillsmith/ tests/integration/skillsmith/
uv run pytest tests/integration/skillsmith/
```
