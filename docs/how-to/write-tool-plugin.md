# How to Write a Tool Plugin

## Goal

Ship a Stargraph tool as an installable Python distribution that Stargraph
discovers via `importlib.metadata` entry points and registers through
`pluggy`.

## Prerequisites

- Stargraph installed (`pip install stargraph>=0.2`).
- Python 3.13+, `hatchling` or your packaging backend of choice.
- Read the [Plugin Model](../concepts/plugins.md) — especially the
  two-stage discovery contract.

## Steps

### 1. Lay out the package

```text
my-tool-plugin/
├── pyproject.toml
└── src/
    └── my_tool_plugin/
        ├── __init__.py
        └── _plugin.py
```

`_plugin.py` will hold both the `stargraph_plugin()` manifest factory and
the `@tool`-decorated callable.

**Verify:** `find . -name pyproject.toml -o -name '*.py'` shows the four
files above.

### 2. Implement the `stargraph_plugin()` manifest factory

Stargraph's loader (see [`stargraph.plugin._manifest`][manifest]) imports this
factory before any tool code, then validates the returned
[`PluginManifest`](../reference/plugin-manifest.md) against
`STARGRAPH_API_VERSION_MAJOR=1` and a namespace conflict map.

```python
# src/my_tool_plugin/_plugin.py
from stargraph.ir import PluginManifest


def stargraph_plugin() -> PluginManifest:
    return PluginManifest(
        name="my-tool-plugin",
        version="0.1.0",
        api_version="1",
        namespaces=["mypkg"],
        provides=["tool"],
        order=5000,
    )
```

`namespaces` must match the `namespace` you pass to `@tool` below;
`order` controls registration priority (`0..10000`, default `5000`,
collisions are fatal).

**Verify:** `python -c "from my_tool_plugin._plugin import stargraph_plugin;
print(stargraph_plugin())"` prints a populated `PluginManifest`.

### 3. Decorate the tool callable

```python
# src/my_tool_plugin/_plugin.py (continued)
from decimal import Decimal

from stargraph.ir import ToolSpec
from stargraph.tools import ReplayPolicy, SideEffects, tool


@tool(
    name="echo",
    namespace="mypkg",
    version="0.1.0",
    side_effects=SideEffects.none,
    description="Return the input string verbatim.",
)
def echo(message: str) -> dict[str, str]:
    """Echo a string back to the caller."""
    return {"echoed": message}


def register_tool() -> list[ToolSpec]:
    """`stargraph.tools` entry-point factory — yields ToolSpec records."""
    return [echo.spec]
```

The `@tool` decorator (see [`stargraph.tools.decorator`][decorator]):

- attaches a `ToolSpec` to the callable as `echo.spec`,
- derives input/output JSON Schemas from the type annotations via
  `pydantic.TypeAdapter` when you omit `input_schema=`/`output_schema=`,
- defaults `replay_policy` from `side_effects` per FR-21
  (`none|read → recorded_result`, `write|external → must_stub`),
- normalises `requires_capability=` into `ToolSpec.permissions`.

**Verify:** `python -c "from my_tool_plugin._plugin import echo;
print(echo.spec.model_dump())"` prints a populated spec.

### 4. Wire entry points

```toml
# pyproject.toml
[project]
name = "my-tool-plugin"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["stargraph>=0.2"]

[project.entry-points."stargraph"]
stargraph_plugin = "my_tool_plugin._plugin:stargraph_plugin"

[project.entry-points."stargraph.tools"]
echo = "my_tool_plugin._plugin:register_tool"
```

The `stargraph` group binds the manifest factory; the `stargraph.tools` group
binds the tool factory. Both are required — Stargraph refuses to register a
plugin distribution that contributes a `stargraph.tools` entry but no
`stargraph_plugin` factory (`PluginLoadError`).

### 5. Test the tool

```python
# tests/test_echo.py
from my_tool_plugin._plugin import echo


def test_echo_passthrough():
    assert echo(message="hello") == {"echoed": "hello"}
    assert echo.spec.namespace == "mypkg"
    assert echo.spec.side_effects == "none"
```

**Verify:** `pytest -q` is green.

## Wire it up

Install the distribution into the environment running Stargraph:

```bash
pip install -e ./my-tool-plugin
STARGRAPH_TRACE_PLUGINS=1 python -c "from stargraph.plugin.loader import build_plugin_manager; build_plugin_manager()"
```

You should see structured `plugin.discovery.entry`,
`plugin.manifest.validated`, and `plugin.register` events for
`my-tool-plugin`.

## Verify

- `stargraph inspect <run_id>` (after a run that uses `mypkg.echo`) shows
  the tool call in the timeline.
- Importing without `STARGRAPH_TRACE_PLUGINS` still works and stays silent.

## Troubleshooting

!!! warning "Common failure modes"
    - **`PluginLoadError: ... no stargraph_plugin manifest factory`** — the
      dist registered a `stargraph.tools` entry but forgot the
      `[project.entry-points."stargraph"] stargraph_plugin = ...` line.
    - **`PluginLoadError: api_version '2' incompatible with Stargraph major 1`**
      — bump Stargraph or pin the manifest's `api_version` back to `"1"`.
    - **`PluginLoadError: namespace conflict`** — two installed
      distributions claimed the same `namespaces[]` entry. Uninstall the
      offender named in the error.
    - **`PluginLoadError: plugin order collision`** — pick a unique
      `order` integer in `[0, 10000]`.

## See also

- [Plugin Manifest reference](../reference/plugin-manifest.md)
- [Tools reference](../reference/tools.md)
- [Hookspecs](../reference/hookspecs.md)
- [Build an agent](build-agent.md) — using your tool from a Skill.

[manifest]: https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/plugin/_manifest.py
[decorator]: https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/tools/decorator.py
