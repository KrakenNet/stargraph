# AGENTS.md — skills

## Purpose

`stargraph.skills` is the Skill plugin-API surface plus the in-tree reference
skill bundles. A **skill** is an agent/workflow/utility packaged as a subgraph:
it declares its own `state_schema` (the FR-23 output-channel whitelist), names
the tools it needs, and composes into any parent graph as a `SubGraphNode`. Three
kinds (`SkillKind`): `agent` (open tool loop), `workflow` (fixed topology, no
LLM-driven control flow), `utility` (pure transform, no side effects).

A skill is the *capability*; a **plugin** is the distribution that registers
capabilities (tools/skills/stores/packs + hooks) under the `stargraph.*`
entry-point groups. The two mirror Claude Code's skill-vs-plugin split.

## Ownership

- **Framework surface** (owned here, do not fork per-bundle): `base.py`
  (`Skill`, `SkillKind`, `Example`), `react.py` (`ReactSkill` tool-loop
  primitive), `salience.py` (scorer), `refs/` (single-file reference skills
  `rag`/`autoresearch`/`wiki`).
- **Bundles** (each a self-contained subtree): `nodesmith` (own AGENTS.md),
  `shipwright`, and the reference bundles below. The runtime they generate
  *for* (NodeBase, State, graph loop) is owned upstream in `src/stargraph/`.

## Local Contracts

- **Bundle shape** (mirror `extract/`, the canonical minimal reference):
  `__init__.py` (exports the Skill instance + State), `_skill.py` (the
  `Skill(...)` instance — `kind`, `state_schema`, `subgraph=`
  `"stargraph.skills.<name>:graph.yaml"`, `requires`), `state.py`, `nodes/`,
  `manifest.yaml`, `graph.yaml` (linear IR: `id "graph:<name>"`, node `kind`
  `"...nodes.<mod>:<Class>"`, `rules: []`).
- **Declared channels only.** `state_schema` field names ARE the write
  whitelist. No `set`/`set[...]` fields — the `Skill` validator rejects them
  (NFR-2; use `list`/`tuple`/`frozenset`). A field that shadows a `BaseModel`
  attribute (e.g. `schema`) is forbidden — it warns at runtime; rename it
  (`table_schema`).
- **LLM behind an injected seam.** Any model call is a `Callable` injected into
  the node's `__init__`, defaulting to a `_default_*` that lazy-imports `dspy`
  (same inline pyright ignores as `extract`). Tests inject a deterministic stub,
  so the suite needs no live model. Rule-driven skills (`triage`) use a bundled
  CLIPS `rules.clp` via a Fathom engine instead — no LLM at all.
- **In-tree skills are not entry-point-registered.** The `stargraph.skills`
  group is empty by design (external dists populate it); in-tree bundles run by
  path/import and serve as references.
- **No cheats.** The deterministic logic is real and tested; only the model/DB
  seam is stubbed. No `NotImplementedError`, hardcoded outputs, or skipped tests.

## Verification

```
uv run ruff check src/stargraph/skills/<name>/ tests/integration/<name>/
uv run pyright src/stargraph/skills/<name>/ tests/integration/<name>/
uv run pytest tests/integration/<name>/ -q
```

## Child DOX Index

- `nodesmith/AGENTS.md` — self-improving graph that builds Stargraph nodes
  (generate → 3-tier gate → repair; trainset curator CLI/TUI). Own contract.
- `shipwright/` — meta-graph that synthesizes graphs from a brief (live, tested;
  no AGENTS.md yet).
- Reference bundles (this scaffold; no per-bundle AGENTS.md — they are simple):
  `extract` (utility), `approval` (workflow, HITL gate), `triage` (workflow,
  CLIPS-rule routing), `sql_analyst` (agent, NL→query→repair), `digest`
  (workflow, map-reduce summary). The `pii_guard` plugin example lives under
  `src/stargraph/plugins/`.
