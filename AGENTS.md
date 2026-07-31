# AGENTS.md — Stargraph root

Binding work contract for this repo. Read this before editing anything; read the
nearest child `AGENTS.md` (see index below) for local rules.

## Purpose

Stargraph is a stateful agent-graph framework with deterministic governance.
Nodes do work (LLM/ML/tool/retrieval); **Fathom (a CLIPS rules engine) decides
transitions** over provenance-typed facts — not an LLM router. Determinism,
provenance, and replayability are the point. Public API is unstable until v1.0.

## Ownership

- Source: `src/stargraph/` (Python, packaged). See `tests/`, `docs/`, `examples/`,
  `demos/`, `design-docs/`.
- Deployment artifacts: root `Dockerfile` + `compose.yaml` + `deploy/helm/stargraph/`.
  Single serve process per state volume is the supported topology (audit chain =
  one fsync'd writer); the Helm chart hard-pins `replicas: 1` — keep it that way.
- This root doc owns repo-wide rules + the recipe index. Child docs own their
  subtree (see Child DOX Index).

## Local Contracts

### Build / verify loop (run from repo root)

```bash
make install        # uv sync --group dev --group docs
make lint           # ruff check + ruff format --check (src/ tests/)
make typecheck      # pyright (strict on src/stargraph + tests)
make test           # pytest -m unit         <- fast inner loop
make test-all       # full suite             <- before pushing
make docs-build     # mkdocs build --strict
```

Inner loop = `make test` (unit only, fast). Always run `make lint && make
typecheck && make test` before declaring a change done; `make test-all` before a
PR.

### Pytest markers (`--strict-markers` is on — an unregistered marker fails)

`unit` (fast, no external deps) · `integration` (needs Fathom CLIPS / services) ·
`slow` · `knowledge` (stores/skills/retrieval/memory) · `serve` (serve umbrella) ·
`websocket` · `api` · `trigger` · `scheduler`. Pick the marker that matches; new
markers must be registered in `pyproject.toml` first.

### The contract that breaks first: state ↔ CLIPS boundary

- Mutate Pydantic `state` freely **inside** a node.
- Only fields marked `Annotated[T, Mirror(...)]` (from `stargraph.ir`) cross into
  CLIPS, and only **on node exit**.
- Rules read **facts**, never Python state. Facts are namespaced:
  `stargraph.*` (runtime, read-only) · `bosun.*` (governance packs) · `user.*`
  (app) · `<plugin>.*` (must register prefix). Unregistered namespaces are
  rejected at graph load. Full vocab: `design-docs/stargraph-facts.md`.

### House rules

- **SPDX:** every `.py` under `src/` must start with
  `# SPDX-License-Identifier: Apache-2.0` (`scripts/check_spdx.py --fix`). `.yaml`
  follows the same convention by habit.
- **DCO:** commits must be signed off — `git commit -s`. CI rejects unsigned.
- **Surgical changes:** every changed line traces to the request; don't refactor
  adjacent code or delete pre-existing dead code without asking.
- **No stubs / demo-data / broken examples** committed as if real. `examples/` is
  golden-tested; keep it green.

## Work Guidance — recipe index

Land on the right path in one hop. Each row: intent → doc → command/scaffold.

| I want to… | Read | Then |
|---|---|---|
| Ground on this install's exact contracts | — | `stargraph context dump` (JSON: API, node kinds, IR schema, errors, fact namespaces, examples) |
| Understand the layout | `docs/architecture-map.md` | — |
| Run / try a graph | `examples/README.md` | `stargraph run examples/hello.yaml` |
| Build a graph the easy way | `src/stargraph/AGENTS.md` (authoring compiler) | short authoring YAML (`id/state/nodes/routes`, no `ir_version`) — `stargraph run` compiles it transparently; `stargraph new research-bot` scaffolds; `stargraph compile --show-clips` shows the lowered IR + rules |
| Build a graph (full IR) | `docs/how-to/build-graph.md` | author IR YAML or Python `Graph` |
| Use a built-in tool from YAML | `docs/reference/tools.md` | `kind: tool` + `config: {tool: std.<name>@1, ...}` — the `std` pack seeds automatically; heavy deps = `stargraph[tools]`. SaaS packs (`slack`/`github`/`s3`/`email`/`postgres`) also seed: capability-gated, writes dry-run until `STARGRAPH_<NS>_LIVE=1`; boto3/psycopg = `stargraph[tools-saas]` |
| Write a tool plugin | `docs/how-to/write-tool-plugin.md` | `@tool` + entry-point in `pyproject.toml` |
| Drop in a markdown skill | `docs/how-to/write-markdown-skill.md` | `./skills/<name>/SKILL.md` (Claude Code format loads unmodified); `stargraph skills compile\|list` |
| Add a store provider | `docs/how-to/add-store-provider.md` | implement the Store Protocol |
| Add a trigger | `docs/how-to/add-trigger.md` | `stargraph.triggers` entry-point |
| Write Fathom rules | `docs/tutorials/fathom-rules.md`, `design-docs/stargraph-facts.md` | `RuleSpec` / `.clp` pack; inline `when:` takes the mapping sugar `{node: <id>, <mirror-field>: <value>}` (compiled by `ir/_when.py`) or raw CLIPS |
| Add an MCP server | `docs/how-to/add-mcp-server.md` | `stargraph.mcp_adapters` entry-point |
| Publish a torch/SB3 model as ONNX | `docs/reference/nodes/ml.md` | `stargraph.ml.export` (`onnx-export` extra) |
| Train / gate / plan with RL policies | `docs/reference/rl.md` | `stargraph.rl` (`rl` extra): `rl:train_ppo`, gauntlet eval graph, `stargraph.planners` entry-point |
| Start from a prebuilt agent pattern | `src/stargraph/AGENTS.md` (bundles) | `stargraph new <bundle>` (copies `graph.yaml` + `SKILL.md`) — coding-agent, deep-research, evaluator-optimizer, hitl-approval, orchestrator-workers, rag-qa, triage-router |
| Serve / replay | `docs/serve/`, `docs/tutorials/serve-and-replay.md` | `stargraph serve`, `stargraph replay` |
| Deploy in a container / K8s | `docs/guides/containers.md` | root `Dockerfile` + `compose.yaml` + `deploy/helm/stargraph/` (single-replica only) |
| Monitor / roll back a deployment | `docs/serve/api.md` (Ops), `docs/reference/cli.md` | `GET /health`, `GET /metrics`; `stargraph model rollback`, `stargraph pack pin\|revert` |

There are also builder subagents for some of these (`harbor:node-builder`,
`harbor:graph-builder`, `nautilus:source-builder`, the forge pipeline).

## Verification

A change is done when, scoped to what you touched: `make lint`, `make typecheck`,
the relevant `make test*` target, and (for docs) `make docs-build` all pass.
Examples must keep `tests/integration/test_examples.py` green.

## Workspace noise (ignore these — gitignored, not part of the repo)

`.venv/ .forge/ .harbor/ .stargraph/ .remember/ .checkpoints/ .hypothesis/
.pytest_cache/ .ruff_cache/ .playwright-mcp/ site/ runs/ test-results/ data/
graphify-out/ coverage.xml audit.jsonl __pycache__/` and stray `*.pt` model
weights at root (demo downloads). `git ls-files` is the source of truth for what
belongs to the repo.

## Child DOX Index

- [`src/stargraph/AGENTS.md`](src/stargraph/AGENTS.md) — engine source: layout,
  the Mirror boundary, error conventions, adding nodes/tools/stores.
- [`tests/AGENTS.md`](tests/AGENTS.md) — test layout, markers, fixtures, golden
  example tests.
- [`examples/AGENTS.md`](examples/AGENTS.md) — runnable examples + the golden-test
  contract.
- [`docs/AGENTS.md`](docs/AGENTS.md) — mkdocs structure, strict build, where each
  doc kind goes.
