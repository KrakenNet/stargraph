# AGENTS.md — src/stargraph (engine source)

Local contract for the packaged engine. Parent: [`../../AGENTS.md`](../../AGENTS.md).

## Purpose

The `stargraph` package: graph orchestration, CLIPS rule routing, nodes, stores,
serve, replay. Strictly typed (pyright `strict`), Apache-2.0.

## Local Contracts

- **SPDX header required** on every `.py`: `# SPDX-License-Identifier: Apache-2.0`.
- **Typed:** code here is under pyright `strict`. No `Any` leaks across public
  boundaries; prefer Protocols for pluggable seams (see `stores/`, `checkpoint/`,
  `triggers/`).
- **The Mirror boundary:** mutate Pydantic state inside a node; only
  `Annotated[T, Mirror(...)]` fields (from `stargraph.ir`) sync to CLIPS on node
  exit. Rules read facts, not state. Don't reach into CLIPS from node code.
- **Errors:** raise from `stargraph.errors`, never bare `Exception`/`ValueError`
  for engine conditions. Carry a `hint=`/`see=` when the fix is knowable (see
  `errors/_hierarchy.py`). Catch broad categories via `StargraphRuntimeError`.
- **IR is the canonical waist.** Python `Graph` and YAML both resolve to the IR
  models in `ir/_models.py`; new graph features land in the IR + `validate()`.

## Work Guidance

- Where things live + key symbols per package: `docs/architecture-map.md`.
- Adding a node kind: subclass `NodeBase`, wire it into the kind table in
  `nodes/registry.py` (`_NODE_FACTORIES`, or `register_node_kind()` /
  the `register_nodes` plugin hookspec) if it should be `kind:`-addressable
  from YAML. Builders take the full `NodeSpec` so `config` drives constructors;
  `module:Class` kinds bind config uniformly (`config_model` attr → validated
  config kwarg, else `**config`, else zero-arg). `kind: dspy` builds a real
  LM-backed `DSPyNode` from config (`stub: true` is the only stub route).
- Prebuilt LLM kinds (`nodes/prebuilt.py`): `reason`/`summarize`/`classify`/
  `extract`/`judge`/`plan` — signature-preset DSPy nodes with deterministic
  post-processors that whitelist outputs and emit the standard routing fields
  (`verdict`, `confidence`/`score` as float-as-str). The LLM never routes;
  Fathom rules match the mirrored facts. New presets follow the same shape:
  config model (extra=forbid) → synthesized `kind: dspy` config → `PrebuiltNode`
  wrapper with a pure, unit-tested post function.
- Composition prebuilts: `kind: react` (`nodes/react.py`) runs `dspy.ReAct`
  over an allowlist of registry tools, every call bridged through
  `execute_tool` (capability gate + provenance facts); async via
  `module.acall`, force-loud via `adapters.dspy.install_loud_fallback_filter`.
  `kind: code` (`nodes/code.py`) generates a script with CoT then runs it via
  `std.python_exec@1` through the same pipeline; emits
  `code`/`run_result`/`verdict`.
- Adding a built-in tool: `@tool` decorator + append its `module:attr` ref to
  `_BUILTIN_TOOL_REFS` in `tools/builtin.py` (in-process seeding; the core dist
  has NO `stargraph.tools` entry-points — that pipeline is for external plugin
  dists only). Tools behind an optional extra go in `_EXTRA_TOOL_REFS` (import
  gates registration) or, like the `std` pack, lazy-import the heavy dep inside
  the tool body with a `pip install stargraph[<extra>]` hint (always registers).
- `tools/std/` (the batteries pack, namespace `std`, 12 tools): honest
  `side_effects` (network read → `read`, caller-chosen mutation → `external`);
  code/shell execution capability-gated (`tools:std:exec` / `tools:std:shell`,
  default-deny); filesystem/sqlite paths jailed via `tools/std/_jail.py`
  (`STARGRAPH_TOOLS_FS_ROOT`, default cwd); network tools build clients through
  `tools/_http.py::build_client` (the tests' mock seam, shared with the SaaS
  packs). Heavy deps (`ddgs`, `readability-lxml`, `duckdb`) = the `tools` extra.
- SaaS packs (`tools/{slack,github,s3,email,postgres}/`, 12 tools): EVERY tool
  is capability-gated (`tools:<ns>:read|write` — reads touch private data);
  every write is dry-run by default behind `STARGRAPH_<NS>_LIVE` and takes a
  caller-supplied `dedupe_key` (`tools/_saas.py` holds the shared guards;
  ServiceNow is the pattern's origin). Mock seams: slack/github →
  `tools/_http.py`; s3 → `s3/_client.py`; postgres → `postgres/_conn.py`
  (DSN from `STARGRAPH_POSTGRES_DSN` only, never a tool arg); email fakes
  stdlib `smtplib`/`imaplib`. Heavy deps (`boto3`, `psycopg`) = the
  `tools-saas` extra (lazy-imported; pip hint at call time).
- Optional-dep seams (`ml/export.py`, `stores/rerankers.py`, `rl/`): import the
  optional package lazily inside the function, raise `MLNodeError` /
  `RLNodeError` / `StargraphRuntimeError` with a `hint=` naming the extra.
  `ml/export.py` funnels torch/sb3 through `importlib.import_module` + explicit
  `Any` so pyright strict stays green with or without the extra installed.
- `rl/` (the `rl` extra): `GymEnvNode` / `PolicyNode` / `rl:train_ppo` need the
  extra; the gauntlet library + reference eval graph
  (`rl/gauntlet/eval-graph.yaml`, stations read dotted refs from `EvalState`)
  run on the base install. Gauntlet math is the ARLO port — keep it IDENTICAL
  (it backs a cited admission result; acceptance test
  `tests/integration/rl/test_arlo_admission_repro.py` pins the numbers).
  Planners register under the `stargraph.planners` entry-point group
  (`Planner` protocol, `PlannerNode`). Docs: `docs/reference/rl.md`.
- torch is never a core dep or an MLNode runtime; torch/SB3 models publish
  into the registry as ONNX via `stargraph.ml.export` (`onnx-export` extra).
- Ops surface: `GET /health` is deliberately ungated (K8s probes carry no
  credentials); `GET /metrics` requires `metrics:read` and hand-rolls Prometheus
  text exposition (`serve/_api_helpers._scan_audit_metrics`) — do not add a
  prometheus client dep. `stargraph model rollback` / `pack pin|revert` live in
  `cli/model.py` / `cli/pack.py` and audit via `BosunAuditEvent`.
- Big files to be careful in: `serve/api.py`, `serve/scheduler.py`,
  `graph/loop.py`, `graph/run.py`, `serve/auth.py`.

## Verification

`make lint && make typecheck && make test`; run the marker matching your subtree
(`-m serve`, `-m integration`, etc.) before declaring done.

## Child DOX Index

- [`ovarp/AGENTS.md`](ovarp/AGENTS.md) — attest governance ticks as OVARP
  offline-verifiable receipts (ADR-0012); emit sink, reproducer, shared harness.
- [`skills/AGENTS.md`](skills/AGENTS.md) — code-authoring skills (graphs, nodes,
  packs, tools, stores, triggers, adapters, ML).
