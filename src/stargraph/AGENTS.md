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
- Adding a node kind: subclass `NodeBase`, wire it into the CLI factory table in
  `cli/run.py` (`_NODE_FACTORIES`) if it should be `kind:`-addressable from YAML.
- Adding a tool: `@tool` decorator + register via the `stargraph.tools`
  entry-point in `pyproject.toml`.
- Big files to be careful in: `serve/api.py`, `serve/scheduler.py`,
  `graph/loop.py`, `graph/run.py`, `serve/auth.py`.

## Verification

`make lint && make typecheck && make test`; run the marker matching your subtree
(`-m serve`, `-m integration`, etc.) before declaring done.
