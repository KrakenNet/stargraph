# How to Build an Agent

## Goal

Compose a [`Skill`](build-skill.md), a tool table, and a graph into a
ReAct-style agent loop with declared output channels, HITL pauses, and
memory writes.

## Prerequisites

- A working [tool plugin](write-tool-plugin.md) — the agent's
  function-call surface.
- A [Skill](build-skill.md) — the agent's manifest and `state_schema`.
- A [`stargraph.yaml`](build-graph.md) graph for the orchestration layer.
- Familiarity with [Plugin Model](../concepts/plugins.md) and
  [Skills](../knowledge/skills.md).

## Steps

### 1. Pick the agent shape

Stargraph's canonical agent shape is the `ReactSkill` think → act → observe
loop ([`stargraph.skills.react`][react]). It sub-classes `Skill` only to
fix `kind=SkillKind.agent`, `state_schema=ReactState`, and `max_steps`;
everything else (tools, system prompt, declared outputs) flows from the
parent `Skill` contract.

```python
# src/my_agents/triage/_agent.py
from typing import Any

from stargraph.skills import ReactSkill


def llm_stub(state: Any, ctx: Any) -> dict[str, Any]:
    """Return think-step decisions: reasoning, tool_call, done, final_answer."""
    # Phase-3 wiring routes this through the engine model registry.
    return {"reasoning": "no-op", "tool_call": None, "done": True, "final_answer": "ok"}


def grep_logs(query: str) -> dict[str, Any]:
    return {"matches": []}


TRIAGE = ReactSkill(
    name="triage",
    version="0.1.0",
    description="Investigate alerts via tool loop.",
    tools=["mypkg.grep_logs@0.1.0"],
    system_prompt="Investigate the alert; return a verdict.",
    max_steps=5,
    llm_stub=llm_stub,
    tool_impls={"grep_logs": grep_logs},
)
```

`tool_impls` is the **runtime dispatch table** keyed by `tool_call["name"]`;
it is distinct from `tools` (manifest tool ids) so the engine can swap
recorded cassettes during replay.

**Verify:** `python -c "import asyncio; from my_agents.triage._agent
import TRIAGE; from stargraph.skills import ReactState; print(asyncio.run(
TRIAGE.run(ReactState())))"` runs the loop without raising.

### 2. Wrap the agent in a graph

The skill's `subgraph` field points at a `stargraph.yaml` document the
engine mounts as a `SubGraphNode`:

```yaml
# src/my_agents/triage/stargraph.yaml
ir_version: "1.0.0"
id: "skill:triage"

state_class: "stargraph.skills.react:ReactState"

nodes:
  - id: think
    kind: my_agents.triage.nodes:Think
  - id: act
    kind: my_agents.triage.nodes:Act
  - id: observe
    kind: my_agents.triage.nodes:Observe
  - id: pause_for_review
    kind: interrupt
  - id: remember
    kind: memory
```

### 3. Wire HITL with `InterruptNode`

A `kind: interrupt` node persists a checkpoint, emits a
`WaitingForInputEvent` (`prompt` + `interrupt_payload`), and exits
cleanly. Resume happens via `POST /v1/runs/{id}/respond` (gated on the
`requested_capability` you declare).

```yaml
- id: pause_for_review
  kind: interrupt
  prompt: "Approve the proposed remediation?"
  requested_capability: "ops.review"
  on_timeout: "halt"
```

See [`InterruptAction`][interrupt] for the full field list.

### 4. Wire memory with `MemoryWriteNode`

`kind: memory` persists a state-resident `Episode` to a `MemoryStore`
binding declared in `stores:`:

```yaml
stores:
  - name: ep_memory
    provider: sqlite

nodes:
  - id: remember
    kind: memory
```

The node reads `state.episode_to_write` and ships it to
`stores["ep_memory"]`. See [memory node](../reference/nodes/memory.md).

### 5. Wire retrieval with `RetrievalNode`

`kind: retrieval` parallel-fans-out to a list of `StoreRef` bindings and
fuses with RRF:

```yaml
stores:
  - name: kb_vec
    provider: lancedb
  - name: kb_facts
    provider: sqlite_fact

nodes:
  - id: lookup
    kind: retrieval
```

The node writes fused hits under `state.retrieved`. See
[retrieval node](../reference/nodes/retrieval.md).

### 6. Register the agent as a Skill

Same shape as [Build a Skill](build-skill.md): a `register_skills` hook
returns a `SkillSpec` with `kind="agent"` and `subgraph=<path>`.

## Wire it up

```toml
# pyproject.toml
[project.entry-points."stargraph"]
stargraph_plugin = "my_agents._plugin:stargraph_plugin"

[project.entry-points."stargraph.skills"]
triage = "my_agents.triage._pack"

[project.entry-points."stargraph.tools"]
grep_logs = "my_agents.triage._tools:register"
```

## Verify

```bash
pip install -e .
stargraph run src/my_agents/triage/stargraph.yaml --inputs alert_id=1234
```

Expected end-of-run output:

```
run_id=<uuid> status=done
```

Inspect the timeline:

```bash
stargraph inspect <run_id> --db .stargraph/run.sqlite
```

Each think → act → observe iteration shows up as a step, with tool
calls in the `tool_calls` column and the trajectory in
`state_at_step <N>`.

## Troubleshooting

!!! warning "Common failure modes"
    - **`StargraphRuntimeError: ReactSkill._think requires an llm_stub
      callable`** — the production model wiring isn't installed; supply
      `llm_stub=` for tests or wire the engine model registry.
    - **State write rejected** — the engine `SubGraphNode` translator
      enforces `declared_output_keys` (FR-23). Add the field to your
      `state_schema` or stop writing it.
    - **`error_budget` exhausted** — tool dispatch exceptions decrement
      `state.error_budget`. Check `state.tool_calls[-1].error` for the
      cause.

## See also

- [`InterruptNode`](../reference/nodes/index.md) — HITL primitive.
- [`MemoryWriteNode`](../reference/nodes/memory.md).
- [`RetrievalNode`](../reference/nodes/retrieval.md).
- [`SubGraphNode`](../reference/nodes/subgraph.md) — boundary translation.
- [HITL serve docs](../serve/hitl.md) — `respond` API surface.
- [`ReactSkill`][react] source.

[react]: https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/skills/react.py
[interrupt]: https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/ir/_models.py
