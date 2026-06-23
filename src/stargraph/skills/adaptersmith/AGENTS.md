# AGENTS.md — adaptersmith

## Purpose

A Stargraph graph that builds Stargraph **adapters** (external-runtime seams)
from a natural-language brief, and improves at it over time. A leaf instance of
the shared smith core (`../_smith/`) — same two loops as nodesmith/toolsmith:
idea 1 (bounded generate → gate → repair; only gate-passing adapters land) and
idea 2 (file ledger of reflexion lessons + a spec→adapter trainset + drift;
offline compile of few-shot demos). Unlike a tool, an adapter has NO base class:
the artifact is a MODULE exposing module-level `async bind` + `async call_tool`,
and discovery finds those two functions.

## Ownership

Owns adapter generation only. The runtime it generates *for* (`ToolSpec`,
`Capabilities`/`CapabilityClaim`, `SideEffects`/`ReplayPolicy`, the reference MCP
seam in `src/stargraph/adapters/mcp.py`) is owned upstream. The domain-agnostic
machinery (build loop, gate tiers, ledger, RAG primitives, web, LM, the DSPy
generator shell, `SmithSpec`) lives in `../_smith/` (see its AGENTS.md). This
package is the *adapter* plug-in: signature, `coerce`, contract driver, corpus,
seeds, state, graph wiring. Keep adapter-specific code here.

## Local Contracts

- `gate.py::run_full_gate` — adapter binding of `_smith.gate.run_tiered_gate`:
  static (compile + `ruff --select F`) → **contract** → tests (pytest). The
  contract driver (subprocess) imports `adapter.py`, asserts it defines
  module-level `bind` AND `call_tool` and that BOTH are coroutine functions, then
  EXERCISES them against an EMBEDDED in-memory MCP session stub + real
  `stargraph.security` literals: (a) `bind` returns exactly two `ToolSpec` for
  the embedded catalogue with `namespace == "mcp"`, after `initialize()` ran; (b)
  a wrong-typed input raises; (c) an off-schema response raises; (d) a malicious
  string is sanitized (control chars stripped, HTML-escaped); (e) a refused
  capability raises `CapabilityError` BEFORE the session is touched
  (`session.calls == []`). That run-and-assert path is the un-cheatable floor — a
  trivially-passing generated test can't land an adapter that skips the gate,
  returns off-schema, or fails to sanitize. The driver embeds its own stub +
  literals; it never trusts the candidate's test or fixture. `verify_sources`
  gates raw source strings in a throwaway temp dir (the entry for `make`, the
  doctor, and seed checks).
- `program.py::AdapterProgram` — `_smith.program.SmithProgram` bound to
  `AdapterSignature` + the adapter `coerce`. Generation emits `adapter_name`,
  `namespace`, `fixture` (advisory; may be `{}`), `adapter_source` (`adapter.py`),
  `test_source` (`test_adapter.py`). Auto-loads `compiled.json` demos at
  construction.
- `nodes/build.py` — `ADAPTER_SPEC` (a `_smith.spec.SmithSpec`, the full
  per-domain plug-in: artifact files, gate, recall sources, landing/trainset) +
  `Build`, a thin `_smith.build.SmithBuild` subclass that constructs
  `AdapterProgram()` by name (so tests monkeypatch it). `nodes/{triage,recall,
  record}.py` are no-arg subclasses of the shared `_smith.nodes` lifecycle nodes,
  each binding `ADAPTER_SPEC`. All loop/lifecycle logic lives in `_smith`; only
  the spec is adapter-specific.
- `state.py::State` — subclasses `_smith.state.SmithState` (the generic spine),
  adding only the adapter fields `adapter_name`, `namespace` (default `"mcp"`),
  `fixture`.
- `_ledger.py` — binds `_smith.ledger.Ledger` to `ADAPTERSMITH_HOME` (default
  `.stargraph/adaptersmith/`, absolute).
- `retrieval.py` — RAG grounding: the reference MCP adapter (`adapters/mcp.py`,
  always — the seam every adapter mirrors) + the most relevant sibling adapter
  modules under `stargraph.adapters` + gate-accepted ledger pairs. Injected as
  the signature's `relevant_context`.
- `seeds.py::SEEDS` — one hand-authored, gate-verified cold-start adapter: the
  full MCP functional seam (port of `adapters/mcp.py`) whose test exercises
  translate/validate/sanitize/cap-gate against an in-test stub. `test_seeds.py`
  gates it. The seed keeps the `read-secret -> ["fs.read:/secrets/*"]` permission
  map so the capability branch is non-vacuous.
- Graph order is linear (`graph.yaml`, `rules: []`): triage → recall → build →
  record. The repair loop lives INSIDE `build`. `recall` gathers grounding
  (lessons + RAG + model-decided web) into `state.recalled_context`; it calls the
  LM, so it must run inside the scoped `dspy` context.

## TRUST BOUNDARY

The contract + tests tiers EXECUTE LLM-generated code in a subprocess as the
invoking user with full network + filesystem access — process isolation, not a
sandbox. Every gate path runs in a fresh throwaway temp dir; `write_files`
refuses escaping paths. The contract driver passes a session-shaped STUB and
never reaches the real stdio branch (which would lazy-import `mcp` and open a
subprocess). Don't point the smith at adapters with real external side effects
during gating, and don't put secrets in fixture/script values (stored verbatim in
the trainset row).

## Work Guidance

- No cheats/hardcoding/demo-data — the contract tier exists precisely so a
  trivially-passing generated test can't land an adapter that skips the
  capability gate or returns off-schema. The cap-gate case is the keystone: it
  asserts the gate fires BEFORE the session is invoked.
- Subprocesses use `sys.executable`; argv is always list-form (no `shell=True`).
- Never import the real `mcp` package at module top level — lazy-import it only on
  the real-transport branch, so the adapter stays importable offline.

## Verification

```
uv run ruff check src/stargraph/skills/adaptersmith/ tests/integration/adaptersmith/
uv run pyright src/stargraph/skills/adaptersmith/ tests/integration/adaptersmith/
uv run pytest tests/integration/adaptersmith/
```
