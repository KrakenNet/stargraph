# Hookspec Catalog

Reference for every hook specification declared by Stargraph core in `stargraph.plugin.hookspecs`. Plugins implement these hooks with the `@hookimpl` decorator from `stargraph.plugin`.

See also: [PluginManifest](plugin-manifest.md), [Skills](skills.md), [Plugin model concepts](../concepts/plugins.md).

## Markers

```python
from stargraph.plugin import hookimpl, hookspec  # both bound to PROJECT="stargraph"
```

Both markers are `pluggy.HookspecMarker("stargraph")` / `pluggy.HookimplMarker("stargraph")` (`src/stargraph/plugin/_markers.py`). All Stargraph hookspecs and hookimpls **must** use these markers — pluggy routes calls by project name.

## firstresult vs collect-all

Pluggy hookspecs come in two flavours; Stargraph uses both:

| Flavour | Behaviour | Used by |
|---------|-----------|---------|
| **collect-all** | Pluggy calls every registered hookimpl in registration order and returns the list of return values. | `register_tools`, `register_skills`, `register_stores`, `register_packs`, `before_tool_call`, `after_tool_call`, `stargraph_startup`, `stargraph_shutdown`, `trigger_routes` and the trigger lifecycle hooks. |
| **firstresult** | Pluggy calls hookimpls in registration order; the **first non-`None`** return wins and the rest are skipped. | `authorize_action` (Bosun first-deny). |

Every Stargraph hookspec exposes `<hook>.firstresult: bool` as a stable attribute so callers and tests can introspect collect semantics without reaching into pluggy internals.

!!! warning "Trigger lifecycle hooks need the dispatcher"
    `trigger_init` / `trigger_start` / `trigger_stop` / `trigger_routes` are declared as ordinary collect-all hooks, but pluggy's default behaviour halts iteration on the first exception. **Do not** call `pm.hook.trigger_init(...)` directly — use `stargraph.plugin.triggers_dispatcher.dispatch_trigger_lifecycle` (and `collect_trigger_routes`) which iterates plugins manually inside per-plugin `try/except` blocks. See [Trigger lifecycle](#trigger-lifecycle).

## Lifecycle

### `stargraph_startup(pm)`

```python
@hookspec
def stargraph_startup(pm: PluginManager) -> None: ...
```

- **Returns:** `None` (collect-all; return values discarded).
- **When:** invoked once after `build_plugin_manager` finishes registering plugins.
- **Use for:** opening connections, warming caches, registering observability sinks.

### `stargraph_shutdown(pm)`

```python
@hookspec
def stargraph_shutdown(pm: PluginManager) -> None: ...
```

- **Returns:** `None` (collect-all).
- **When:** invoked once during graceful shutdown.
- **Use for:** flushing buffers, closing handles. Mirror of `stargraph_startup`.

## Registration (collect-all)

Each `register_*` hookspec returns a list. Pluggy aggregates lists across plugins; Stargraph's registries flatten them.

### `register_tools() -> list[ToolSpec]`

```python
@hookspec
def register_tools() -> list[ToolSpec]:
    return []
```

Each plugin returns the tools it provides. `ToolSpec` (`stargraph.ir._models`) carries `name`, `namespace`, `version`, schemas, `side_effects`, `replay_policy`, `permissions`, `cost_estimate`, etc.

### `register_skills() -> list[SkillSpec]`

```python
@hookspec
def register_skills() -> list[SkillSpec]:
    return []
```

Each plugin returns the skills it provides. The IR-portable `SkillSpec` is the registration record; runtime `Skill` instances (`stargraph.skills.Skill`) are pre-validated by the loader. See [Skills](skills.md).

### `register_stores() -> list[StoreSpec]`

```python
@hookspec
def register_stores() -> list[StoreSpec]:
    return []
```

Each plugin returns the named store bindings it provides. `StoreSpec.protocol` is one of `vector`, `graph`, `doc`, `memory`, `fact`. Bootstrap-time `${VAR}` interpolation and JSON Schema validation live in `stargraph.plugin._config`.

### `register_packs() -> list[PackSpec]`

```python
@hookspec
def register_packs() -> list[PackSpec]:
    return []
```

Each plugin returns the Bosun rule packs it ships.

<!-- TODO: verify PackSpec field set once concrete type lands; hookspecs.py currently aliases it to Any. -->

## Tool-call observation (collect-all)

### `before_tool_call(call)`

```python
@hookspec
def before_tool_call(call: ToolCall) -> None: ...
```

Fired immediately before a tool dispatch. Use for tracing, audit, capability checks that **observe** but do not gate.

### `after_tool_call(call, result)`

```python
@hookspec
def after_tool_call(call: ToolCall, result: ToolResult) -> None: ...
```

Fired immediately after a tool dispatch (success or failure surfaces in `result`). Use for metrics, telemetry, episodic-memory writes.

<!-- TODO: verify ToolCall / ToolResult shapes once Phase 2 lands the concrete types — hookspecs.py currently aliases them to Any. -->

## Authorisation (firstresult)

### `authorize_action(action) -> bool | None`

```python
@hookspec(firstresult=True)
def authorize_action(action: dict[str, Any]) -> bool | None: ...
```

- **Return semantics:**
    - `False` → **deny**, dispatch chain halts, decision is final.
    - `True` → **allow**, dispatch chain halts, decision is final.
    - `None` → **abstain**, pluggy moves on to the next plugin.
- **Use for:** Bosun first-deny authorisation. Earlier-registered plugins (lower `manifest.order`) get the first chance to deny.

```python
from stargraph.plugin import hookimpl

@hookimpl
def authorize_action(action: dict) -> bool | None:
    if action.get("kind") == "tool.call" and action.get("namespace") == "secrets":
        return False  # hard deny
    return None  # abstain
```

## Trigger lifecycle

Trigger plugins (cron, webhook, queue) implement four hooks. **All four must be invoked through `stargraph.plugin.triggers_dispatcher`** to get per-plugin try/except isolation (design §6.3, FR-2, AC-12.2).

### `trigger_init(deps)`

```python
@hookspec
def trigger_init(deps: dict[str, Any]) -> None: ...
```

Lifespan startup. The plugin sets up internal state from `deps` (carries the serve `ServeContext` plus any wiring the plugin needs).

### `trigger_start(deps)`

```python
@hookspec
def trigger_start(deps: dict[str, Any]) -> None: ...
```

Scheduler start. The plugin begins emitting `TriggerEvent`s.

### `trigger_stop(deps)`

```python
@hookspec
def trigger_stop(deps: dict[str, Any]) -> None: ...
```

Graceful shutdown. The plugin drains in-flight work and stops emitting events.

### `trigger_routes() -> list[Route]`

```python
@hookspec
def trigger_routes() -> list[Route]: ...
```

Webhook triggers return their FastAPI routes here; cron-only triggers return `[]`. The serve app gathers and mounts every plugin's routes during lifespan setup. `Route` is aliased to `Any` in `hookspecs.py` to keep the module FastAPI-free; callers should treat it as `starlette.routing.BaseRoute` (or an `APIRouter`).

### Dispatcher API

```python
from stargraph.plugin.triggers_dispatcher import (
    dispatch_trigger_lifecycle,  # for trigger_init / trigger_start / trigger_stop
    collect_trigger_routes,      # for trigger_routes
    DispatchResult,
)

results = dispatch_trigger_lifecycle(pm, "trigger_init", deps={...})
for r in results:
    if not r.success:
        log.error("trigger %s failed init: %s", r.plugin_name, r.error)
```

Each `DispatchResult` carries `plugin_name`, `success`, `result`, and `error`. A failure in one plugin never blocks the others.

### `TriggerHookSpec` namespace alias

`stargraph.plugin.hookspecs.TriggerHookSpec` is a documentation/grouping class that exposes the four trigger hooks as `staticmethod` attributes. It is **not** decorated with pluggy markers — pluggy still picks up the module-level functions. Use the class only as a typed reference handle when you prefer the class-shaped API.

## Complete plugin example

A minimal plugin that registers one tool and observes every dispatch:

```python
# acme_stargraph/tools/search.py
from stargraph.plugin import hookimpl
from stargraph.ir import ToolSpec, SideEffects

@hookimpl
def register_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="search",
            namespace="acme",
            version="1.0.0",
            description="Search the Acme corpus.",
            input_schema={
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
            output_schema={"type": "object"},
            side_effects=SideEffects.read,
        ),
    ]

@hookimpl
def before_tool_call(call) -> None:
    print(f"calling {call!r}")

@hookimpl
def after_tool_call(call, result) -> None:
    print(f"called {call!r} -> {result!r}")
```

Pair with the manifest factory described in [PluginManifest](plugin-manifest.md).

## Hook reference table

| Hook | firstresult | When | Returns |
|------|-------------|------|---------|
| `stargraph_startup(pm)` | no | After plugin manager build | `None` |
| `stargraph_shutdown(pm)` | no | On graceful shutdown | `None` |
| `register_tools()` | no | Registry build | `list[ToolSpec]` |
| `register_skills()` | no | Registry build | `list[SkillSpec]` |
| `register_stores()` | no | Registry build | `list[StoreSpec]` |
| `register_packs()` | no | Registry build | `list[PackSpec]` |
| `before_tool_call(call)` | no | Before each tool dispatch | `None` |
| `after_tool_call(call, result)` | no | After each tool dispatch | `None` |
| `authorize_action(action)` | **yes** | Per action authorisation check | `bool \| None` |
| `trigger_init(deps)` | no | Trigger lifespan startup | `None` (use dispatcher) |
| `trigger_start(deps)` | no | Trigger scheduler start | `None` (use dispatcher) |
| `trigger_stop(deps)` | no | Trigger shutdown | `None` (use dispatcher) |
| `trigger_routes()` | no | Trigger route mount | `list[Route]` (use dispatcher) |
