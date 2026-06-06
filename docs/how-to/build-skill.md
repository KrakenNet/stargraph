# How to Build a Skill

## Goal

Register a Stargraph [`Skill`][skill] — a typed bundle of `state_schema`,
tool ids, optional subgraph, and system prompt — that the engine can
mount as a `SubGraphNode` with strict declared-output channels.

## Prerequisites

- Stargraph installed (`pip install stargraph>=0.2`).
- A Pydantic `BaseModel` subclass for your skill's state.
- Tools available: either built-in, or the
  [tool plugin you wrote earlier](write-tool-plugin.md).
- Familiarity with [Plugin Model](../concepts/plugins.md) and
  [Skills](../knowledge/skills.md).

## Steps

### 1. Define the state schema

`state_schema` is the **declared output channel whitelist** the engine's
`SubGraphNode` enforces (FR-23): writes to keys not on this schema fail
loudly at registration, not at runtime.

```python
# src/my_skills/summarize/state.py
from pydantic import BaseModel, Field


class SummarizeState(BaseModel):
    document: str
    summary: str | None = None
    key_points: tuple[str, ...] = Field(default_factory=tuple)
```

!!! warning "Frozenset, not set"
    `Skill.__init__` rejects any `state_schema` field annotated as
    `set` or `set[X]` (NFR-2 — replay-safe state must be hashable
    immutable). Use `frozenset` or `tuple`.

**Verify:** `python -c "from my_skills.summarize.state import
SummarizeState; print(SummarizeState.model_fields.keys())"` prints
`dict_keys(['document', 'summary', 'key_points'])`.

### 2. Instantiate the Skill

`Skill` is **not** subclassed — instantiate it directly with your
manifest fields. The `@model_validator` populates
`declared_output_keys` from `state_schema.model_fields`.

```python
# src/my_skills/summarize/_skill.py
from stargraph.skills import Skill, SkillKind

from my_skills.summarize.state import SummarizeState


SUMMARIZE = Skill(
    name="summarize",
    version="0.1.0",
    kind=SkillKind.utility,
    description="Compress a document into bullet-point key points.",
    state_schema=SummarizeState,
    tools=["mypkg.echo@0.1.0"],          # tool ids: <ns>.<name>@<ver>
    system_prompt="Summarise the document into 3-5 bullets.",
    requires=["fs.read:/docs/*"],         # capability strings
    bubble_events=True,                    # FR-24, default-on
)
```

**Verify:** `python -c "from my_skills.summarize._skill import
SUMMARIZE; print(SUMMARIZE.declared_output_keys, SUMMARIZE.site_id)"`
prints `frozenset({'document', 'summary', 'key_points'})
summarize@0.1.0`.

### 3. Optional: bundle a subgraph

For workflow- and agent-kind skills, point `subgraph` at a
[stargraph.yaml IR document](build-graph.md). The
[shipwright skill](https://github.com/KrakenNet/stargraph/tree/main/src/stargraph/skills/shipwright)
is the canonical bundled example: `manifest.yaml`, `stargraph.yaml`,
`state.py`, and a `nodes/` package live alongside the Skill instance.

```text
src/my_skills/summarize/
├── __init__.py
├── _skill.py           # Skill(...) instance
├── state.py            # SummarizeState
├── manifest.yaml       # id, version, kind, state_schema
├── stargraph.yaml         # subgraph IR
└── nodes/
    └── chunk.py        # custom NodeBase subclasses
```

```python
SUMMARIZE = Skill(
    ...,
    subgraph="my_skills.summarize:stargraph.yaml",
)
```

### 4. Register via the `register_skills` hook

```python
# src/my_skills/summarize/_pack.py
from stargraph.ir import SkillSpec
from stargraph.plugin._markers import hookimpl

from my_skills.summarize._skill import SUMMARIZE


@hookimpl
def register_skills() -> list[SkillSpec]:
    """Aggregate hook — every plugin's contributions are merged."""
    return [
        SkillSpec(
            name=SUMMARIZE.name,
            namespace="my_skills",
            version=SUMMARIZE.version,
            description=SUMMARIZE.description,
            kind=SUMMARIZE.kind.value,
            tools=SUMMARIZE.tools,
            subgraph=SUMMARIZE.subgraph,
            system_prompt=SUMMARIZE.system_prompt,
        ),
    ]
```

## Wire it up

```toml
# pyproject.toml
[project.entry-points."stargraph"]
stargraph_plugin = "my_skills._plugin:stargraph_plugin"

[project.entry-points."stargraph.skills"]
summarize = "my_skills.summarize._pack"
```

The `stargraph.skills` group's value is the **module** (not a callable) —
`pluggy` looks up `@hookimpl`-decorated functions there.

## Verify

```bash
pip install -e .
STARGRAPH_TRACE_PLUGINS=1 python -c "
from stargraph.plugin.loader import build_plugin_manager
pm = build_plugin_manager()
for spec in pm.hook.register_skills():
    for s in spec:
        print(s.namespace, s.name, s.version)
"
```

You should see `my_skills summarize 0.1.0`.

## Troubleshooting

!!! warning "Common failure modes"
    - **`ValueError: state_schema field 'tags' is typed as 'set' ...`** —
      switch the field to `frozenset` or `tuple`.
    - **`PluginLoadError: namespace conflict`** — two installed
      distributions both declared `my_skills` in their
      `PluginManifest.namespaces`. Pick a unique namespace.
    - **Skill not surfaced via `register_skills`** — verify the
      entry-point points at the **module** containing the
      `@hookimpl`-decorated function, and that you imported `hookimpl`
      from `stargraph.plugin._markers` (not pluggy directly).

## See also

- [Build an agent](build-agent.md) — wire a Skill, tools, and a graph
  into a ReAct loop.
- [Build a graph](build-graph.md) — author the IR your `subgraph=` field
  points at.
- [Reference: Skills](../knowledge/skills.md).
- [`Skill` class](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/skills/base.py)
- [Shipwright bundle](https://github.com/KrakenNet/stargraph/tree/main/src/stargraph/skills/shipwright)

[skill]: https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/skills/base.py
