# AGENTS.md — examples

Local contract for runnable examples. Parent: [`../AGENTS.md`](../AGENTS.md).

## Purpose

Minimal, self-contained graphs that an AI or human can run in one command to see
the engine work. Onboarding surface; `docs/getting-started.md` points here.

## Local Contracts

- **Every `.yaml` must run to `status=done`** via `stargraph run`. The golden test
  `tests/integration/test_examples.py` globs this directory and enforces it — a
  broken example fails CI.
- **Self-contained:** inline `state_schema:` (primitives), `echo`/`halt` nodes, no
  external models/stores/keys — unless the example deliberately demonstrates more
  and ships everything it needs.
- SPDX header (`# SPDX-License-Identifier: Apache-2.0`) at the top of each file.
- No stubs or fake data. If it can't run green, it doesn't go here — point to
  `demos/` for heavier, fully-wired graphs instead.

## Work Guidance

Add an example: write `examples/<name>.yaml` → `stargraph run examples/<name>.yaml
--inputs message=hello` → confirm `uv run pytest tests/integration/test_examples.py`
passes (auto-discovered by glob). Update `examples/README.md`'s table.

## Verification

`uv run pytest tests/integration/test_examples.py`.
