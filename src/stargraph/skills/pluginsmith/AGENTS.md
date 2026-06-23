# AGENTS.md — pluginsmith

## Purpose

A Stargraph graph that builds whole Stargraph **plugins** from a natural-language
brief, and improves at it over time. A composite smith on the shared core
(`../_smith/`): it emits a registerable **plugin** — a single `plugin.py` carrying a
`@tool`-decorated callable plus the pluggy `@hookimpl` functions that make it a
plugin: `register_tools` (advertise the tool), `authorize_action` (default-deny one
action kind, abstain otherwise — Bosun first-deny semantics), and
`before/after_tool_call` audit hooks. Same two loops as the other smiths: idea 1
(bounded generate → gate → repair; only gate-passing plugins land) and idea 2 (file
ledger of reflexion lessons + a brief→plugin trainset + drift).

## Ownership

Owns plugin generation only. The runtime it generates *for* (the pluggy hookspec
contract in `src/stargraph/plugin/hookspecs.py`, the `@tool` decorator in
`src/stargraph/tools/decorator.py`, the `BosunAction`/`ToolCall`/`ToolResult` payload
types, the loader's entry-point discovery) is owned upstream. The domain-agnostic
machinery (build loop, gate tiers, ledger, RAG, web, LM, the DSPy generator shell,
`SmithSpec`) lives in `../_smith/`. Unlike `graphsmith`/`skillsmith`, a plugin is a
single module (no auto-assembled wiring file) and does not run a graph, so it has
its own contract driver (it does NOT use `_smith.gate.RUN_GRAPH_PRELUDE`). This
package adds: the isolated-`PluginManager` contract gate, the signature/coerce, the
contract corpus, seeds, state, graph wiring, and the spec's bundle landing config.
No node overrides — the lifecycle is wholly shared.

## Local Contracts

- `gate.py` — plugin binding of `_smith.gate.run_tiered_gate`: static → **contract**
  → tests. The contract driver (subprocess) registers the generated `plugin.py` on a
  FRESH, ISOLATED pluggy `PluginManager` (the Stargraph hookspecs only — NO
  `load_setuptools_entrypoints`, so no other installed plugin can shadow the
  `authorize_action` verdict via `firstresult`) and drives it FOR REAL: asserts
  `register_tools` advertises the declared `(namespace, tool_name)`, calls the
  `@tool` callable (resolved by `tool_attr`) and checks its output against
  `fixture.tool_expects`, fires `authorize_action` and asserts it DENIES
  `fixture.deny_kind` (`False`) and abstains/allows `fixture.allow_kind` (`None`/`True`),
  and fires `before/after_tool_call`. Because the asserts run on a live plugin
  manager, a trivially-passing test can't land a plugin that doesn't register,
  compute, gate, or audit. `verify_sources` (keyword-only) gates raw source strings
  in a throwaway temp dir.
- `program.py::PluginProgram` — `_smith.program.SmithProgram` bound to
  `PluginSignature` + the plugin `coerce`. Generation emits `plugin_name`,
  `namespace`, `tool_name`, `tool_attr` (the `@tool` callable's python name),
  `plugin_source`, `fixture` (`tool_args`/`tool_expects`/`deny_kind`/`allow_kind`),
  `test_source`. The hook function names + parameter names are fixed by the hookspec.
- `nodes/build.py` — `PLUGIN_SPEC` (a `_smith.spec.SmithSpec`: the two-file
  `artifact_files`, the gate binding `meta`={tool_name,namespace,tool_attr}+fixture,
  recall sources, landing/trainset) + `Build`, a thin `_smith.build.SmithBuild`
  subclass constructing `PluginProgram()` by name.
- `nodes/{triage,recall,record}.py` — no-arg subclasses of the shared `_smith.nodes`
  lifecycle nodes binding `PLUGIN_SPEC`; no method overrides. Landing the two files
  with their FIXED names (`plugin.py` + `test_plugin.py`, so the test's
  `from plugin import …` resolves) under `output_dir/<stem>/` (entry = `plugin.py`)
  is driven by `PLUGIN_SPEC.bundle_files` + `entry_file` through the shared
  `SmithRecord._land`.
- `state.py::State` — subclasses `_smith.state.SmithState`, adding `plugin_name`,
  `namespace`, `tool_name`, `tool_attr`, `fixture`.
- `_ledger.py` — binds `_smith.ledger.Ledger` to `PLUGINSMITH_HOME` (default
  `.stargraph/pluginsmith/`, absolute).
- `retrieval.py` — RAG grounding: three fixed contracts (the pluggy hookspecs, the
  `@tool` decorator, the `BosunAction`/`ToolCall` payload types) + gate-accepted
  ledger plugins. Injected as `relevant_context`.
- `seeds.py::SEEDS` — hand-authored, gate-verified cold-start plugins (an
  email-redactor denying `external_send`; a secret-masker denying `exfiltrate`).
  `test_seeds.py` gates every one.
- Graph order is linear (`graph.yaml`, `rules: []`): triage → recall → build →
  record. The repair loop lives INSIDE `build`. `recall` calls the LM.

## TRUST BOUNDARY

The contract + tests tiers EXECUTE LLM-generated code in a subprocess as the
invoking user with full network + filesystem access — process isolation, not a
sandbox. The contract tier additionally IMPORTS the generated `plugin.py`, REGISTERS
it on a pluggy `PluginManager`, and CALLS its tool + hooks (their bodies run in that
subprocess). Every gate path runs in a fresh throwaway temp dir;
`_smith.gate.write_files` refuses escaping paths. Don't run a smith as a privileged
user, don't put secrets in fixture/input values, and treat a generated plugin as
untrusted code.

## Work Guidance

- No cheats/hardcoding/demo-data — the contract tier exists precisely so a
  trivially-passing test can't land a plugin that doesn't register, compute, gate, or
  audit on a live `PluginManager`.
- The contract manager is bare + isolated (`pluggy.PluginManager("stargraph")` +
  `add_hookspecs` + `register`), NOT `build_plugin_manager()` — entry-point discovery
  would let another installed plugin's `firstresult` `authorize_action` shadow the
  generated verdict and mask a non-gating plugin.
- Subprocesses use `sys.executable`; argv is always list-form (no `shell=True`).

## Verification

```
uv run ruff check src/stargraph/skills/pluginsmith/ tests/integration/pluginsmith/
uv run pyright src/stargraph/skills/pluginsmith/ tests/integration/pluginsmith/
uv run pytest tests/integration/pluginsmith/
```
