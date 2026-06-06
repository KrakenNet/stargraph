# How-to Guides

Task-oriented recipes for building, integrating, and operating Stargraph.
Each guide assumes you already have Stargraph installed (`pip install
stargraph`) and have read the [Plugin Model](../concepts/plugins.md) and
[IR Concepts](../concepts/ir.md).

## Authoring

Build the things that run inside Stargraph.

- [Build a skill](build-skill.md) — instantiate `Skill(...)`, declare
  `state_schema`, and register via the `register_skills` hook.
- [Write a tool plugin](write-tool-plugin.md) — package a `@tool`-decorated
  callable as a discoverable distribution.
- [Build an agent](build-agent.md) — compose a Skill, tools, and a graph
  into the canonical ReAct-style loop with HITL and memory hooks.
- [Author a stargraph.yaml graph](build-graph.md) — write an IR document
  end-to-end and run it.
- [Author a Bosun pack](bosun-pack.md) — bundle CLIPS rules, sign, and
  distribute as a `stargraph.packs` plugin.
- [Add a Fathom / Bosun rule pack](add-rule-pack.md) — wire a packaged
  rule pack into a graph's `governance` block.

## Integrations

Wire Stargraph to the outside world.

- [Add an MCP server](add-mcp-server.md) — bind a stdio MCP server's
  tools and gate them through capability + schema + sanitization.
- [Add a Nautilus broker source](add-nautilus-source.md) — wire a
  broker request as a `BrokerNode` in a graph.
- [Add a store provider](add-store-provider.md) — implement one of the
  five Store Protocols (Vector, Graph, Doc, Memory, Fact).
- [Add a custom trigger](add-trigger.md) — register a Trigger plugin
  that emits `TriggerEvent` rows into the scheduler.

## Operations

Run, replay, and persist.

- [Persist with a checkpointer](checkpointer.md) — implement the
  `Checkpointer` Protocol or pick one of the bundled drivers
  (`SQLiteCheckpointer`, `PostgresCheckpointer`).

## See also

- [CLI Reference](../reference/cli.md) — every `stargraph` subcommand.
- [IR Schema](../reference/ir-schema.md) — the wire shape of every
  field referenced below.
- [Plugin Manifest](../reference/plugin-manifest.md) — the
  `stargraph_plugin()` factory contract.
- [Hookspecs](../reference/hookspecs.md) — pluggy hook surface.
