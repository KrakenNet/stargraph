# Tools Reference

Tools are Pydantic-typed callables bound to a [`ToolSpec`](#toolspec) by the
[`@tool`](#tool-decorator) decorator. The runtime gates every invocation through
the side-effect classifier and the replay policy so deterministic replay (FR-21)
and capability enforcement (NFR-7) hold uniformly across in-tree, third-party,
and MCP-imported tools.

Source: `src/stargraph/tools/`.

## ToolSpec

`stargraph.ir.ToolSpec` (re-exported via `stargraph.tools.ToolSpec`) is the canonical
descriptor. The full Pydantic model (AC-9.4):

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | `str` | required | Tool short name (e.g. `broker_request`). |
| `namespace` | `str` | required | Owner namespace; the registry key is `f"{namespace}.{name}@{version}"`. |
| `version` | `str` | required | Free-form version tag. |
| `description` | `str` | required | Human-readable summary; the decorator falls back to the docstring's first line. |
| `input_schema` | `dict[str, object]` | required | JSON Schema describing positional/keyword inputs. |
| `output_schema` | `dict[str, object]` | required | JSON Schema describing the return payload. |
| `side_effects` | [`SideEffects`](#sideeffects) | required | Classifier that drives replay routing. |
| `replay_policy` | [`ReplayPolicy`](#replaypolicy) | `must_stub` | Override; default derives from `side_effects`. |
| `permissions` | `list[str]` | `[]` | Capability claims required to invoke (NFR-7). |
| `idempotency_key` | `str \| None` | `None` | Optional dedupe key forwarded to the runtime. |
| `cost_estimate` | `Decimal \| None` | `None` | FR-9 monetary estimate; never `float`. |
| `examples` | `list[dict[str, object]]` | `[]` | Reference call/response pairs. |
| `tags` | `list[str]` | `[]` | Free-form classifiers. |
| `deprecated` | `bool` | `False` | Hide from selectors when `True`. |

!!! note
    `ToolSpec` is exposed lazily via `stargraph.tools.__getattr__` to break a
    circular import: `stargraph.ir._models` already imports the enums from
    `stargraph.tools.spec`.

## SideEffects

`stargraph.tools.SideEffects` is a `StrEnum` (FR-33, design 3.4.2). Members are
plain lowercase strings on the wire.

| Value | When to use |
| --- | --- |
| `none` | Pure function. No I/O, no globals, no clock. Safe to re-execute infinitely. |
| `read` | Reads external state but does not mutate (filesystem read, HTTP `GET`, vector lookup, broker query). |
| `write` | Mutates state owned by Stargraph (Checkpointer, store write, artifact write). |
| `external` | Mutates state owned by a third party (HTTP `POST`, MCP tool call, LLM completion). |

The classifier feeds the runtime's replay router and the cost/risk surface that
Bosun packs reason about.

## ReplayPolicy

`stargraph.tools.ReplayPolicy` (FR-33, FR-21, NFR-8) is a `StrEnum` with
kebab-cased values.

| Value | Semantics |
| --- | --- |
| `must-stub` | Replay must hit a recorded cassette. Re-running mutates the world; not stubbing is a hard error. |
| `fail-loud` | No cassette exists and re-execution is disallowed. Replay raises so the operator sees the gap. |
| `recorded-result` | Prefer the cassette but fall back to re-execution if absent (only safe for `none`/`read`). |

### Default policy mapping

When a tool author omits `replay_policy`, the decorator derives it from
`side_effects`:

| `side_effects` | Default `replay_policy` |
| --- | --- |
| `none` | `recorded-result` |
| `read` | `recorded-result` |
| `write` | `must-stub` |
| `external` | `must-stub` |

Override only when you have a deterministic external call (rare) or want to
trade availability for safety on a read path.

## `@tool` decorator

`stargraph.tools.tool` binds a callable to a `ToolSpec`. The wrapped callable
keeps its original calling convention (sync or async, positional or keyword)
and gains a `wrapper.spec` attribute (Open Q8 resolution: callable wrapper,
not a descriptor).

```python
from stargraph.tools import SideEffects, tool

@tool(
    name="broker_request",
    namespace="nautilus",
    version="1",
    side_effects=SideEffects.read,
    requires_capability="tools:broker_request",
)
async def broker_request(*, agent_id: str, intent: str) -> dict:
    ...
```

### Signature

```python
def tool(
    *,
    name: str,
    namespace: str,
    version: str,
    side_effects: SideEffects,
    replay_policy: ReplayPolicy | None = None,
    requires_capability: str | list[str] | None = None,
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    description: str | None = None,
    idempotency_key: str | None = None,
    cost_estimate: Decimal | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...
```

### Auto schema derivation

When `input_schema` is omitted, the decorator builds one with
`pydantic.create_model` over the wrapped callable's annotated parameters and
takes the JSON Schema via `pydantic.TypeAdapter`. `*args` and `**kwargs` are
skipped (they are not part of the schema surface).

When `output_schema` is omitted, the decorator runs `TypeAdapter` over the
return annotation. A bare `None` return becomes `{"type": "null"}`; a missing
annotation becomes `{}` (any value).

!!! warning
    `Annotated[T, metadata]` filtering, positional-only parameters, and
    `BaseModel`-typed `input_schema` are tracked as TODOs (task 1.13). For
    those cases, pass `input_schema=` / `output_schema=` explicitly.

### `requires_capability`

A single string is normalised to a one-element list and stored on
`ToolSpec.permissions`. `None` becomes `[]`. The runtime consults the
capability gate (`stargraph.security.Capabilities.check`) before invocation;
unauthorised calls raise `CapabilityError` and never reach the underlying
callable.

## Wiring a tool into IR

Tools enter the IR via the plugin manifest entry-point group
`stargraph.tools` (see [Plugin manifest](plugin-manifest.md)). The graph YAML
references the tool by its registry key:

```yaml
graph: nautilus_demo
nodes:
  - id: ask_broker
    kind: tool
    tool: nautilus.broker_request@1
    inputs:
      agent_id: "agent-42"
      intent: "{{state.user_intent}}"
    out: broker_reply
```

The runtime resolves `nautilus.broker_request@1` against the registry, runs
the capability gate, validates `state`-derived inputs against
`ToolSpec.input_schema`, and routes through the replay layer per
`ToolSpec.replay_policy`.

## Nautilus broker tool

`stargraph.tools.nautilus` ships one in-tree registry-discoverable tool:

| Tool | Module | Side-effects | Required capability |
| --- | --- | --- | --- |
| `nautilus.broker_request@1` | `stargraph.tools.nautilus.broker_request` | `read` | `tools:broker_request` |

`broker_request(*, agent_id: str, intent: str) -> dict` resolves the
lifespan-singleton `nautilus.Broker` via `stargraph.serve.contextvars.current_broker`,
calls `Broker.arequest`, and returns `BrokerResponse.model_dump(mode="json")`
plus a `__stargraph_provenance__` envelope:

```json
{
  "...": "broker response fields",
  "__stargraph_provenance__": {
    "origin": "tool",
    "source": "nautilus",
    "external_id": "<broker request_id>"
  }
}
```

The envelope is identical to the one [`BrokerNode`](nodes/index.md) writes, so
consumers that pattern-match on the bundle work the same against either form.

!!! example
    Use `BrokerNode` when the broker call is a fixed slot in a graph spec.
    Use `nautilus.broker_request@1` (this tool) inside a ReAct skill or
    sub-graph dispatcher where the call site is dynamic.

Raises `StargraphRuntimeError` if no `Broker` is registered (lifespan factory
did not run, or `nautilus.yaml` was missing at startup).

## See also

- [Plugin manifest](plugin-manifest.md) — entry-point group `stargraph.tools`.
- [IR Schema](ir-schema.md) — wire format for `ToolSpec`.
- [Adapters](adapters/index.md) — DSPy and MCP seams that bind external
  callables as Stargraph tools/nodes.
