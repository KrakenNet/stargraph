# Plugin Manifest and Hookspec Catalog

Every Stargraph plugin distribution declares a `PluginManifest` and
registers under one of the supported entry-point groups. This page is
the canonical hookspec catalog — the hooks Stargraph core declares and
that plugins may implement.

## Manifest fields

| Field         | Type    | Required | Description                                                              |
|---------------|---------|----------|--------------------------------------------------------------------------|
| `api_version` | str     | yes      | SemVer string Stargraph checks before importing the plugin module.          |
| `name`        | str     | yes      | Human-readable plugin name.                                              |
| `kind`        | enum    | yes      | One of `tool`, `skill`, `store`, `pack`, `trigger`, `mcp_adapter`.       |
| `provides`    | list    | no       | Capabilities the plugin advertises (used by graph resolution).           |
| `requires`    | list    | no       | Other plugin capabilities this plugin depends on.                        |

## Entry-point groups

Plugins register under one of the following groups in `pyproject.toml`:

| Group | Purpose | Hookspec |
|-------|---------|----------|
| `stargraph` | Plugin manifest entry-point (root) | n/a |
| `stargraph.tools` | Tool plugins | `register_tools` |
| `stargraph.skills` | Skill plugins | `register_skills` |
| `stargraph.stores` | Store plugins | `register_stores` |
| `stargraph.packs` | Bosun rule packs | `register_packs` |
| `stargraph.triggers` | Trigger plugins | `trigger_*` family |
| `stargraph.mcp_adapters` | MCP server adapters | (loader-discovered) |

## Hookspec catalog

Source of truth: [`src/stargraph/plugin/hookspecs.py`](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/plugin/hookspecs.py).
Type aliases used in the signatures live in
[`src/stargraph/plugin/types.py`](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/plugin/types.py).

### Lifecycle

```python
def stargraph_startup(pm: PluginManager) -> None: ...
def stargraph_shutdown(pm: PluginManager) -> None: ...
```

Fire once after plugin manager builds, and once on graceful shutdown.
`PluginManager` is `pluggy.PluginManager`.

### Registration (collect-all)

```python
def register_tools() -> list[ToolSpec]: ...
def register_skills() -> list[SkillSpec]: ...
def register_stores() -> list[StoreSpec]: ...
def register_packs() -> list[PackSpec]: ...
```

Each plugin returns the entries it provides; results are aggregated
across plugins. `ToolSpec` / `SkillSpec` / `StoreSpec` are the IR
records (`stargraph.ir._models`); `PackSpec` is defined in
`stargraph.plugin.types` as `(id, version, manifest_path)`.

### Tool-call observation

```python
def before_tool_call(call: ToolCall) -> None: ...
def after_tool_call(call: ToolCall, result: ToolResult) -> None: ...
```

Fire around every dispatched tool call. `ToolCall` is a frozen
dataclass `(tool_name, namespace, args, call_id)`; `ToolResult` is
re-exported from `stargraph.runtime.tool_exec`. Plugin observers can
correlate call-to-result via `call_id`.

### Authorisation (first-deny)

```python
@hookspec(firstresult=True)
def authorize_action(action: dict[str, Any]) -> bool | None: ...
```

First non-`None` result wins. `False` denies, `True` allows, `None`
abstains. Implements Bosun's first-deny semantics — order plugins
deliberately if multiple register this hook.

### Trigger lifecycle

```python
def trigger_init(deps: dict[str, Any]) -> None: ...
def trigger_start(deps: dict[str, Any]) -> None: ...
def trigger_stop(deps: dict[str, Any]) -> None: ...
def trigger_routes() -> list[Route]: ...
```

Fire at lifespan startup, scheduler start, graceful shutdown, and
route-mount time respectively. `Route` is the FastAPI
`starlette.routing.BaseRoute` (typed as `Any` in v1 to keep
`stargraph.plugin` import-light — see
[v1 limits](v1-limits.md)).
Per-plugin try/except isolation lives in
`stargraph.plugin.triggers_dispatcher` — call those dispatchers, not
`pm.hook.<name>()` directly, for trigger lifecycles.

## Type contract

Plugin authors import the placeholder types from a single shared module:

```python
from stargraph.plugin.types import (
    PackSpec,
    PluginManager,
    Route,
    StoreSpec,
    ToolCall,
    ToolResult,
)
```

This module is the seam between the hookspec declarations and the
runtime / IR / pluggy types each one resolves to. Future tightening
(e.g. real `Route` once FastAPI is unconditional) updates here without
touching `hookspecs.py` or third-party plugins.
