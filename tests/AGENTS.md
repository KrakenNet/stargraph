# AGENTS.md — tests

Local contract for the test suite. Parent: [`../AGENTS.md`](../AGENTS.md).

## Purpose

Verify the engine. Layout mirrors intent: `unit/` (fast, isolated), `integration/`
(Fathom CLIPS + cross-component), `property/` (Hypothesis), `replay/`,
`migration/`, `perf/`, `regression/`. Shared fixtures in `fixtures/`.

## Local Contracts

- **Markers are mandatory and `--strict-markers` is on.** Tag every test with a
  registered marker (`unit`, `integration`, `knowledge`, `serve`, `api`,
  `websocket`, `trigger`, `scheduler`, `slow`). Unregistered = collection error.
  Register new markers in `pyproject.toml` first.
- `asyncio_mode = "auto"` — async tests need no decorator beyond their marker.
- Reuse fixtures in `tests/fixtures/` (e.g. `sample-graph.yaml`,
  `sample-graph-phase5.yaml`) rather than hand-rolling IR.
- Coverage is on by default (`--cov`); don't pass `-p no:cov`.

## Work Guidance

- End-to-end CLI run pattern: `from stargraph.cli import app` +
  `CliRunner().invoke(app, ["run", graph, "--checkpoint", ...])`, assert
  `status == "done"` via `--summary-json`. See
  `integration/cli/test_run_lifecycle.py`.
- Example graphs are golden-tested in `integration/test_examples.py` (globs
  `examples/*.yaml`); a new example is picked up automatically.

## Verification

`make test` (unit) for the inner loop; `make test-all` for everything.
