# MCP Adapter

`stargraph.adapters.mcp` is the Model Context Protocol stdio adapter (FR-25,
design §3.3.2). It translates an MCP server's tool catalogue into Stargraph
[`ToolSpec`](../tools.md#toolspec) records and gates every `call_tool`
invocation through three controls before the response reaches the LM
context.

Source: `src/stargraph/adapters/mcp.py`.

## `bind`

```python
async def bind(
    server: object,
    *,
    capabilities: Capabilities,
) -> list[ToolSpec]: ...
```

`server` is one of:

- A session-shaped object exposing `initialize` / `list_tools` / `call_tool`
  (the integration tests' in-memory stub takes this branch). Used directly,
  no transport open/close.
- Anything else, treated as `mcp.StdioServerParameters` and opened via
  `mcp.client.stdio.stdio_client` per design §3.3.2.

`bind` records the `Capabilities` instance keyed by `id(session)` in the
module-level `_SESSION_CAPS` dict so `call_tool` can consult the gate
without the caller threading the same instance through twice.

```python
_SESSION_CAPS: dict[int, Capabilities] = {}
```

The stdio branch lazy-imports `mcp` so the module remains importable in
environments where the optional dependency is absent.

### Translation

Each MCP `Tool` becomes a `ToolSpec` with:

| MCP wire field | Stargraph field |
| --- | --- |
| `name` | `name` |
| `description` | `description` |
| `inputSchema` | `input_schema` |
| `outputSchema` | `output_schema` |
| (n/a) | `namespace = "mcp"` |
| (n/a) | `version = "1"` |
| (n/a) | `side_effects = SideEffects.external` |
| (n/a) | `replay_policy = ReplayPolicy.must_stub` |
| (n/a) | `permissions = _required_permissions(name)` |

MCP tools are untrusted by design (§3.11 threat model), so the engine
treats them as worst-case for replay routing unless the caller overrides
post-bind. `_required_permissions` consults a tiny static map; v1.1 will
expose this via config.

## `call_tool`

```python
async def call_tool(
    session: _MCPSessionLike,
    tool: ToolSpec,
    arguments: dict[str, Any],
    *,
    capabilities: Capabilities | None = None,
) -> dict[str, Any]: ...
```

The five-step gauntlet (order is load-bearing):

1. **Capability gate.** The `Capabilities` recorded at `bind` time (or the
   explicit `capabilities=` override) is consulted *before* the session is
   touched. Refusal raises `CapabilityError`; the underlying `call_tool`
   is never reached.
2. **Input validation.** `arguments` is validated against
   `tool.input_schema` with `jsonschema.Draft202012Validator`. Failures
   raise `IRValidationError` with `violation="mcp-input-schema"`.
3. **Invoke** the underlying session's `call_tool(name, arguments)`.
4. **Output validation.** The structured payload is extracted via
   `_extract_structured` and validated against `tool.output_schema`.
   Failures raise `IRValidationError` with `violation="mcp-output-schema"`.
5. **Sanitization.** The validated payload is recursively sanitized
   before return.

!!! warning
    All five steps are mandatory. Tests assert that step 1 short-circuits
    before the session is touched (no `call_tool` recorded on refusal) and
    that step 4 catches a bad response before the LM sees it.

## Schema validation

Validation uses `jsonschema.Draft202012Validator`. Errors are sorted by
JSON Pointer path so the first reported error is the leftmost violation in
the document tree. The error message is wrapped in `IRValidationError`
with the offending JSON Pointer attached as `schema_path`.

## Capability gate

`Capabilities.check(tool)` returns `False` when the tool's required
permissions are not granted to the session. The adapter raises:

```python
raise CapabilityError(
    f"tool {tool.name!r} requires permissions not granted",
    capability=",".join(tool.permissions),
    tool_id=tool.name,
    deployment="mcp",
)
```

## Output sanitization

Three transforms run, in this order, on every string leaf in the response
JSON:

1. Strip ASCII control chars (C0 `0x00-0x1f` minus TAB/LF/CR, DEL `0x7f`,
   and C1 `0x80-0x9f`).
2. HTML-escape (`html.escape(value, quote=True)`).
3. Remove system-marker tokens via the regex
   `__system__|<\|im_start\|>|<\|im_end\|>` (case-insensitive).

```python
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_SYSTEM_MARKER_RE = re.compile(r"__system__|<\|im_start\|>|<\|im_end\|>", re.IGNORECASE)
```

Order matters: control-char strip runs before HTML-escape so the escape
pass cannot reintroduce caret/ampersand sequences that look like control
sequences. Marker removal runs last so the literal marker characters
cannot survive the escape pass and re-coalesce.

The recursion preserves shape: a sanitized object is still an object, an
array is still an array. A non-dict top-level response raises
`IRValidationError` with `violation="mcp-non-object-output"` because the
FR-25 contract requires a JSON object.

## Errors

| Error | Cause | Notable kwargs |
| --- | --- | --- |
| `IRValidationError` | Bad MCP `Tool` shape (missing `name`, non-dict schemas), bad input/output payload, non-object response. | `tool_id`, `violation`, `schema_path` |
| `CapabilityError` | The session's `Capabilities` denies the tool's required permissions. | `capability`, `tool_id`, `deployment="mcp"` |

## Example

```python
from stargraph.adapters import mcp
from stargraph.security import Capabilities

caps = Capabilities(...)
specs = await mcp.bind(server_params, capabilities=caps)

read_secret = next(s for s in specs if s.name == "read-secret")
result = await mcp.call_tool(
    session,
    read_secret,
    {"path": "/secrets/api-key"},
)
```

## See also

- [Adapters index](index.md)
- [Tools reference](../tools.md) — the `ToolSpec` shape `bind` emits.
- [DSPy adapter](dspy.md) — the other v1 adapter seam.
