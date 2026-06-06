# Skills

A **skill** is an agent or workflow packaged as a subgraph: it carries its
own `state_schema`, declares its output channels up front, and composes into
any parent `Graph` as a `SubGraphNode`. The base class is the Plugin API §3
surface; three reference skills (`rag`, `autoresearch`, `wiki`) plus the
`react` tool-loop primitive ship in-tree.

## Skill base class

```python
from stargraph.skills import Skill, SkillKind, Example
from pydantic import BaseModel

class Skill(BaseModel):
    name: str                       # slug; same validator as ToolSpec
    version: str                    # SemVer
    kind: SkillKind                 # agent | workflow | utility
    description: str
    tools: list[str] = []           # tool ids `<ns>.<name>@<ver>`
    subgraph: IRRef | None = None
    system_prompt: str | None = None
    state_schema: type[BaseModel]   # declared output channels live here
    requires: list[str] = []        # capability strings
    examples: list[Example] = []
    bubble_events: bool = True      # FR-24 default-on
```

`SkillKind` is a `StrEnum` with three members:

- `agent` — open-ended tool loop (uses `react` internally).
- `workflow` — fixed topology, no LLM-driven control flow.
- `utility` — pure transformation; no external side effects.

Manifest validation runs through the existing pluggy `register_skills`
hookspec at `stargraph.plugin.hookspecs`. Namespace conflicts loud-fail at
load (NFR-9).

## Agent-as-subgraph

A skill **is** a graph. Its `subgraph` points at an IR document — inline or
by `IRRef` — that the engine compiles and pins through the same
`structural_hash` machinery used for top-level graphs. The composition seam
is `SubGraphNode`:

```python
from stargraph.graph import Graph
from stargraph.nodes import SubgraphNode
from stargraph.skills.refs.rag import rag_skill

graph = Graph(parent_ir).with_node(
    SubgraphNode.from_skill(rag_skill, site="step-3")
)
```

Two replay-driven contracts make composition safe:

- **Site-id determinism** (AC-3.5). The skill's instantiation site id is
  derived from its IR position, **not** LangGraph's call-order assignment.
  Replay survives topology mutations within unchanged regions.
- **Topology pinning.** The subgraph's `graph_hash` is content-addressable.
  Any change to the skill's IR forces a top-level `graph_hash` mismatch —
  the engine's FR-20 gate fires loudly on resume.

## Declared output channels

LangGraph #4182 — implicit parent-state mutation from a subgraph — is a
silent-corruption bug. Stargraph mitigates it by enforcing **declared output
channels only**:

- The skill's `state_schema` defines every field the subgraph can write.
- The `SubGraphNode` boundary translator consumes those field names as the
  write whitelist.
- A subgraph attempting to mutate an undeclared parent key raises a loud
  validation error at compile time (`test_skill_compile_rejects_undeclared_output`).

This diverges from LangGraph defaults intentionally — replay-first stance.
Pair it with `bubble_events=True` (FR-24) and the parent run sees every
transition the subgraph emits without needing to thread state by hand
(LangGraph #2484 mitigation).

## ReAct primitive — `stargraph.skills.react`

The `react` skill ships as a tool-loop subgraph with three nodes:

| Node | Action | Side effects |
|---|---|---|
| `think` | LLM call producing `{reasoning, tool_call \| done}` | `external` (LLM); `must_stub` on replay |
| `act` | Tool dispatch via engine FR-24 path | per `ToolSpec` |
| `observe` | Append result; salience update | `read` |

**Native function-calling, not regex-parsed prompts** — paper-faithful
ReAct is obsolete for capable models. **Termination is rule-driven**
(AC-10.4): `max_steps`, `done` flag in tool result, `error_budget`
exceeded. Model self-termination is never relied on; the rule pack lives
in CLIPS and fires through `stargraph.fathom`.

The state schema declares the four output channels:

```python
class ReactState(BaseModel):
    trajectory: list[ReactStep] = []
    tool_calls: list[ToolCallRecord] = []
    done: bool = False
    error_budget: int = 3
    final_answer: str | None = None
```

Pydantic `set` fields are forbidden (use `frozenset` with declared sort —
inherited from engine NFR-2). Replay matches tool stubs by
`(node_name, step_id)`, **not** `(tool_name, args)`, because args may drift
across runs even when LLM output is semantically equivalent.

## Reference skills

Three skills land at `stargraph.skills.refs.*` — each is a real package, not
a doc-only placeholder:

| Skill | Kind | What it does |
|---|---|---|
| `rag` | `workflow` | `RetrievalNode (vector + doc) → LLM → answer-validation` |
| `autoresearch` | `agent` | ReAct loop: web fetch + vector retrieval, structured output |
| `wiki` | `agent` | Topic → fan-out queries → wiki entry; every claim cites a source |

Composition rule of thumb: always declare your output channels first, then
write the subgraph against them.

See [design §3.7–3.13](https://github.com/KrakenNet/stargraph/blob/main/specs/stargraph-knowledge/design.md)
for the full Skill base, ReAct, and reference-skill specs.
