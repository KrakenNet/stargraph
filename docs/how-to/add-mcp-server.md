# How to Add an MCP Server

## Goal

Bind a Model Context Protocol (MCP) server's tool catalogue as Stargraph
[`ToolSpec`][toolspec] records and have every `call_tool` invocation
gated through three controls: schema validation, capability check, and
output sanitization.

## Prerequisites

- Stargraph installed (`pip install stargraph>=0.2`) — `mcp>=1.0` is a core
  dependency.
- An MCP server you can launch over stdio (e.g.
  `mcp-server-filesystem`, `mcp-server-postgres`).
- Familiarity with [adapters/mcp reference](../reference/adapters/mcp.md).

## Steps

### 1. Define the capabilities you grant

Stargraph's MCP adapter is **default-deny** at the per-tool level. Build a
[`Capabilities`][capabilities] instance with explicit
[`CapabilityClaim`][capabilities] entries that match the tool's
declared `permissions`:

```python
# my_app/_mcp_wire.py
from stargraph.security import Capabilities, CapabilityClaim


CAPS = Capabilities(
    claims=frozenset({
        CapabilityClaim(namespace="fs.read", scope="/secrets/*"),
        CapabilityClaim(namespace="db.kb_facts", scope="read"),
    }),
)
```

The MCP adapter ships a tiny static permission map
(`stargraph.adapters.mcp._TOOL_PERMISSIONS`); per-deployment permission
declarations are deferred to v1.1 (config plumbing TBD).

### 2. Bind the server

Two paths into `bind`: a real stdio session via
`mcp.StdioServerParameters`, or any session-shaped object exposing
`initialize`/`list_tools`/`call_tool` (the in-memory test stub).

```python
# my_app/_mcp_wire.py (continued)
from mcp import StdioServerParameters

from stargraph.adapters import mcp as mcp_adapter


async def bind_filesystem_mcp() -> list:
    params = StdioServerParameters(
        command="mcp-server-filesystem",
        args=["--root", "/tmp/sandbox"],
    )
    tools = await mcp_adapter.bind(params, capabilities=CAPS)
    return tools                              # list[ToolSpec]
```

`bind` opens the stdio transport, calls `initialize` and `list_tools`,
and translates each MCP `Tool` into a `ToolSpec` with `namespace="mcp"`,
`version="1"`, `side_effects=external`, `replay_policy=must_stub`.

**Verify:** `python -m asyncio my_app._mcp_wire` prints the bound tools.

### 3. Call tools through the gated path

```python
from stargraph.adapters.mcp import call_tool


async def use_secret_reader(session, tool_specs):
    read_secret = next(t for t in tool_specs if t.name == "read-secret")
    payload = await call_tool(
        session,
        read_secret,
        arguments={"path": "/secrets/api-key"},
    )
    return payload                            # sanitized dict
```

The order of checks inside `call_tool` (load-bearing):

1. **Capability gate** — refusal raises [`CapabilityError`][errors];
   the underlying `session.call_tool` is **never** invoked.
2. **Input validation** against `tool.input_schema` (jsonschema Draft
   2020-12) — failure raises [`IRValidationError`][errors].
3. **Invoke** the underlying MCP session.
4. **Output validation** against `tool.output_schema`.
5. **Sanitization** — HTML-escape, control-char strip, `__system__`
   marker removal applied to every string leaf in the response payload.

### 4. Fold MCP tools into your IR

Each returned `ToolSpec` is registry-shaped and routes through the same
runtime executor as `@tool`-decorated callables:

```yaml
# stargraph.yaml
nodes:
  - id: read_secret
    kind: my_app.nodes:McpToolNode

state_schema:
  result: dict
```

Where `McpToolNode.execute` calls `mcp_adapter.call_tool(...)` against
your bound session. (The v1 adapter is library-shaped, not a
node-factory — wrap it in your own `NodeBase` subclass to attach to a
graph.)

## Wire it up

Two paths, depending on whether you want the adapter discovered
automatically or wired imperatively.

### Pluggable: register under `stargraph.mcp_adapters`

Ship your adapter as a plugin. The Stargraph loader scans the
`stargraph.mcp_adapters` entry-point group at startup; your hookimpl
returns one or more `MCPAdapterSpec` records, and serve / engine
wiring drives `bind()` against each at the appropriate lifespan
point.

```toml
# pyproject.toml
[project.entry-points."stargraph.mcp_adapters"]
filesystem = "my_plugin.mcp_adapters:filesystem_module"
```

```python
# my_plugin/mcp_adapters.py
import pluggy
from mcp import StdioServerParameters

from stargraph.plugin._markers import PROJECT
from stargraph.plugin.types import MCPAdapterSpec

hookimpl = pluggy.HookimplMarker(PROJECT)


@hookimpl
def register_mcp_adapters() -> list[MCPAdapterSpec]:
    return [
        MCPAdapterSpec(
            name="filesystem",
            server=StdioServerParameters(
                command="mcp-server-filesystem",
                args=["--root", "/tmp/sandbox"],
            ),
            required_capabilities=["fs.read:/tmp/sandbox/*"],
        ),
    ]
```

Aggregate all registered adapters with
`stargraph.adapters.mcp.collect_mcp_adapters(pm)` — that's the helper
serve / engine wiring drives at lifespan time.

### Imperative: hand-rolled wiring

If you don't need plugin discovery, expose your binding helper from
your distribution however you like and call `bind()` from your own
lifespan code. The pluggable path is a thin convenience over this.

## Verify

```bash
python - <<'PY'
import asyncio

from my_app._mcp_wire import bind_filesystem_mcp

async def main():
    tools = await bind_filesystem_mcp()
    for t in tools:
        print(t.namespace, t.name, t.permissions)

asyncio.run(main())
PY
```

Expect a list of MCP tools with `namespace="mcp"` and any permissions
you wired through `_TOOL_PERMISSIONS`.

## Troubleshooting

!!! warning "Common failure modes"
    - **`CapabilityError: tool 'X' requires permissions not granted`** —
      the `Capabilities` instance is missing a claim that satisfies the
      tool's `permissions` list. Add a `CapabilityClaim` with a matching
      namespace + scope glob.
    - **`IRValidationError: MCP tool 'X' input schema validation failed`**
      — the `arguments` dict you passed doesn't match the tool's
      `inputSchema`. Inspect `tool.input_schema` and re-shape your call.
    - **`IRValidationError: MCP tool 'X' returned non-object output`** —
      the upstream tool returned a non-JSON-object payload. Adapter
      contract requires `dict`-shaped responses.
    - **`IRValidationError: MCP tool 'X' has non-dict schema(s)`** — the
      MCP server returned a `Tool` whose `inputSchema`/`outputSchema`
      isn't a JSON object. File a bug against the upstream server.

## See also

- [Adapters: MCP](../reference/adapters/mcp.md)
- [`stargraph.adapters.mcp`][adapter] source.
- [`stargraph.security.Capabilities`][capabilities].
- [Build a tool plugin](write-tool-plugin.md) — for native (non-MCP)
  tools.

[toolspec]: https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/ir/_models.py
[capabilities]: https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/security/capabilities.py
[errors]: https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/errors/__init__.py
[adapter]: https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/adapters/mcp.py
