# Skills

Reference for `stargraph.skills` — the `Skill` base class, the `SkillKind` taxonomy, the salience scorer protocol, and the in-tree reference skills (RAG, Shipwright).

See also: [PluginManifest](plugin-manifest.md), [Hookspec catalog](hookspecs.md), [Reference skills knowledge](../knowledge/reference-skills.md), [Skills knowledge](../knowledge/skills.md).

## Public surface

```python
from stargraph.skills import (
    Example,
    ReactSkill, ReactState, ReactStep, ToolCallRecord,
    RuleBasedScorer, SalienceContext, SalienceScorer,
    Skill, SkillKind,
    refs,
)
```

## Skill model

`stargraph.skills.base.Skill` is a `pydantic.BaseModel` (FR-21..FR-24). The plugin loader pre-validates each instance and registers it via [`register_skills`](hookspecs.md#register_skills-listskillspec).

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `name` | `str` | — | Slug; same validator surface as `ToolSpec.name`. |
| `version` | `str` | — | SemVer. |
| `kind` | [`SkillKind`](#skillkind-enum) | — | Role taxonomy. |
| `description` | `str` | — | Human-readable. |
| `tools` | `list[str]` | `[]` | Fully-qualified tool ids the skill may call (`<ns>.<name>@<ver>`). |
| `subgraph` | `str \| None` | `None` | Path to an IR document or inline `IRDocument` reference. Phase 3 routes execution through `SubGraphNode` using this reference. |
| `system_prompt` | `str \| None` | `None` | Instruction template. |
| `state_schema` | `type[BaseModel]` | — | Pydantic model whose **field names** are the FR-23 declared output channels. |
| `requires` | `list[str]` | `[]` | Capability strings checked by the FR-7 capabilities gate. |
| `examples` | `list[Example]` | `[]` | Few-shot examples. |
| `bubble_events` | `bool` | `True` | FR-24 default-on (LangGraph #2484 mitigation). |
| `declared_output_keys` | `frozenset[str]` | derived | Set automatically from `state_schema.model_fields`. |
| `site_id` (computed) | `str` | derived | `f"{name}@{version}"` (POC formula). Stable handle the engine uses for checkpointing. |

!!! info "Replay-safe state"
    The `_validate_declared_outputs` model validator walks `state_schema.model_fields` and **rejects** any field typed as `set` or `set[X]` (or with a nested `set`). Replay-safe state requires hashable, immutable collections — use `frozenset` (NFR-2). The validator also populates `declared_output_keys` so the engine `SubGraphNode` boundary translator can enforce the write whitelist at registration time, not at runtime.

### `SkillKind` enum

`stargraph.skills.base.SkillKind` is a `StrEnum`:

| Value | Meaning |
|-------|---------|
| `agent` | Agentic loop (e.g. ReAct, planner-executor). Free to call tools and terminate by its own criteria. |
| `workflow` | Deterministic multi-step flow with a fixed control structure. |
| `utility` | Single-purpose helper (formatter, validator, classifier). |

### `Example` model

```python
class Example(BaseModel):
    inputs: dict[str, Any]
    expected_output: dict[str, Any] | None = None
```

Carried in `Skill.examples`. Used both for documentation and for downstream eval harnesses.

## Registration

Skills register through the standard pluggy hookspec:

```python
from stargraph.plugin import hookimpl
from stargraph.skills import Skill, SkillKind
from pydantic import BaseModel

class MyState(BaseModel):
    answer: str = ""

MY_SKILL = Skill(
    name="my-skill",
    version="0.1.0",
    kind=SkillKind.utility,
    description="...",
    state_schema=MyState,
)

@hookimpl
def register_skills() -> list[Skill]:
    return [MY_SKILL]
```

The plugin loader (see [PluginManifest](plugin-manifest.md)) pre-validates each instance and pre-checks namespace conflicts before any `register_skills` hookimpl executes its module body.

!!! note "SkillSpec vs Skill"
    `stargraph.ir.SkillSpec` is the **portable IR** record — no Pydantic validators, no Python-only types, safe to serialise across languages. `stargraph.skills.Skill` is the **runtime** Pydantic class with validators, computed fields, and `state_schema: type[BaseModel]`. Hookspecs declare `list[SkillSpec]` for IR portability; the registry accepts both shapes in the runtime path.

## state_schema and declared output channels

`state_schema` is the contract between the skill and the engine `SubGraphNode` boundary translator (FR-23, design §3.7):

1. The validator collects `state_schema.model_fields.keys()` into `declared_output_keys`.
2. At graph-construction time the engine builds a write whitelist from `declared_output_keys`.
3. Any attempt by the skill subgraph to write a key **not** in the whitelist is rejected at registration / boundary translation time — not at runtime. This is the replay-first stance from design §3.7.

`set` / `set[X]` annotations on `state_schema` fields raise at construction time; replace them with `frozenset`.

## bubble_events

```python
bubble_events: bool = True  # FR-24 default-on
```

When `True`, events emitted inside the skill subgraph bubble to the parent graph's event stream. Default-on mitigates [LangGraph issue #2484](https://github.com/langchain-ai/langgraph/issues/2484), where subgraph events were silently dropped at the parent boundary. Skills that want self-contained event scopes can set it to `False`.

## ReactSkill (POC)

`stargraph.skills.react.ReactSkill` (design §3.9, FR-25, AC-7.5, AC-10.4) is the in-tree think → act → observe tool-loop reference skill.

### `ToolCallRecord`

```python
class ToolCallRecord(BaseModel):
    name: str                 # fully-qualified tool id
    arguments: dict[str, Any] = {}
    result: Any = None
    error: str | None = None  # str-formatted exception when dispatch raised
```

Mirrors the shape engine FR-24 dispatchers consume. **No regex parsing** — attribute access on the dict only.

### `ReactStep`

```python
class ReactStep(BaseModel):
    thought: str
    tool_call: ToolCallRecord | None = None
    observation: str | None = None
```

A single trajectory entry.

### `ReactState`

`ReactState` is the `state_schema` for `ReactSkill` (engine `SubGraphNode` honors its field names as the declared output channels).

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `trajectory` | `list[ReactStep]` | `[]` | Append-only trajectory. |
| `tool_calls` | `list[ToolCallRecord]` | `[]` | Mirror of every dispatched tool call (independent of trajectory ordering). |
| `done` | `bool` | `False` | Set by `_think` when a final answer is reached. |
| `error_budget` | `int` | `3` | Decremented on each tool exception. Termination at `<= 0`. |
| `final_answer` | `str \| None` | `None` | Populated when `done` flips True with a final answer. |
| `step_index` | `int` | `0` | Counts completed iterations; pinned by `test_max_steps_termination`. |

### `ReactSkill` constructor wiring

| Field | Default | Purpose |
|-------|---------|---------|
| `kind` | `SkillKind.agent` | Hard-coded agentic. |
| `state_schema` | `ReactState` | — |
| `max_steps` | `10` | Manifest-level wall-clock cap. |
| `llm_stub` | `None` | Callable `(state, ctx) -> {"reasoning", "tool_call", "done", "final_answer"}`. **Required** at run time; production wires through the engine model registry in Phase 3. |
| `tool_impls` | `{}` | Maps `tool_call["name"]` → callable invoked with `**arguments`. Distinct from `Skill.tools` which lists declared tool ids. |

Termination order (whichever fires first):

1. `state.done` flipped True by `_think`.
2. `state.error_budget` exhausted (decremented on tool exception).
3. `self.max_steps` reached.

## Salience scoring

`stargraph.skills.salience` provides the pluggable scorer used to gate episodic → semantic memory consolidation (FR-31, design §3.6). Episodes scoring below a caller-chosen threshold are filtered before the consolidation rule body fires, so noise never gets promoted (AC-5.5).

### `SalienceScorer` Protocol

```python
@runtime_checkable
class SalienceScorer(Protocol):
    async def score(self, memory: Episode, context: SalienceContext) -> float: ...
```

Returns a float in `[0, 1]`. Stable across v1 (rule-based) → v2 (embedding similarity) → v3 (learned scorer) — only weights and the scorer instance change.

### `SalienceContext`

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `query_embedding` | `list[float] \| None` | `None` | Reserved for v2 relevance scoring; v1 ignores. |
| `last_access_ts` | `datetime` | — | Recency anchor (Park 2023 §4.1: decay since last access, **not** creation). |
| `access_count` | `int` | — | Frequency factor input. |
| `rule_match_count` | `int` | — | Rule-affinity factor input. |
| `weights` | `dict[str, float]` | `{"recency": 1.0, "relevance": 0.0, "importance": 0.0}` | v1 default weights gate relevance and importance to zero. |
| `decay_tau_seconds` | `float` | `86400.0` | Recency decay constant. |

### `RuleBasedScorer`

v1 default. Implements the Park et al. 2023 formula structurally:

```
score = w_recency * exp(-Δt / τ)
      + w_relevance * cos(q_emb, m_emb)
      + w_importance * imp(m)
```

…multiplied by `tanh(access_count / 10)` (frequency) and `tanh(rule_match_count / 5)` (rule affinity), then clamped to `[0, 1]`. v1 weights default `relevance=0.0` and `importance=0.0` (rule-based-only constraint per epic decision); v2 swaps to embedding similarity, v3 swaps to a learned scorer behind the same Protocol.

## refs subpackage

`stargraph.skills.refs` ships the in-tree reference `Skill` implementations (FR-32..FR-34):

| Module | Skill | Status |
|--------|-------|--------|
| `stargraph.skills.refs.rag` | `RagSkill` (retrieval + LLM-stub answer assembly) | POC (FR-32 / AC-7.1) |
| `stargraph.skills.refs.autoresearch` | autoresearch reference | <!-- TODO: verify status (FR-33) --> |
| `stargraph.skills.refs.wiki` | wiki reference | <!-- TODO: verify status (FR-34) --> |

`RagSkill` composes `stargraph.nodes.retrieval.RetrievalNode` (vector + doc fan-out) with a deterministic LLM stub and an answer-assembly step. Capability requirements declared on the manifest: `db.vectors:read`, `db.docs:read`, `llm.generate`. Subgraph IR lives at `tests/fixtures/skills/rag/example.yaml`; Phase 2 loads it via `Skill.subgraph` and routes execution through `SubGraphNode`.

For the bigger picture and rationale see [Reference skills knowledge](../knowledge/reference-skills.md) and [Skills knowledge](../knowledge/skills.md).

## Shipwright skill example

`stargraph.skills.shipwright` is the canonical "skill bundle" example — a Stargraph graph that authors Stargraph graphs from a brief. It lives in `src/stargraph/skills/shipwright/` and is structured like a real out-of-tree plugin so contributors can copy the layout.

| File | Role |
|------|------|
| `manifest.yaml` | Skill identity (`id`, `version`, `kind: workflow`, `description`, `state_schema` reference). |
| `stargraph.yaml` | Graph definition: `state` reference, node list (`triage_gate`, `parse_brief`, `gap_check`, `propose_questions`, `human_input`, `synthesize_graph`, `verify_static`, `verify_tests`, `verify_smoke`, `fix_loop`), Bosun rule packs, governance packs, store providers, checkpoint config. |
| `_pack.py` | Loader for `stargraph.bosun.shipwright.*` sub-packs. Splits `rules.clp` into top-level constructs and feeds each to `fathom.Engine._env.build` for precise compile-error attribution. Mirrors `tests/integration/bosun/_helpers.py` so production nodes (`GapCheck`, `FixLoop`) and tests share one canonical loader. |
| `state.py` | The `State` Pydantic model referenced from `manifest.yaml#state_schema` and `stargraph.yaml#state`. |
| `nodes/` | Per-node modules (`triage`, `parse`, `interview`, `synthesize`, `verify`, `fix`). |
| `templates/` | Prompt fragments and rendered output templates. |
| `graph.yaml` | Companion graph artifact. |

Snippet from `manifest.yaml`:

```yaml
id: stargraph.skills.shipwright
version: "0.1.0"
kind: workflow
description: |
  Shipwright — a Stargraph graph that authors Stargraph graphs from a brief.
  Interview-driven (rules + LLM dual-truth), verified end-to-end, replayable.
state_schema: stargraph.skills.shipwright.state:State
```

Snippet from `stargraph.yaml`:

```yaml
name: shipwright
state: ./state.py:State

nodes:
  - name: gap_check
    type: stargraph.skills.shipwright.nodes.interview:GapCheck
  - name: human_input
    type: stargraph.nodes.human_input
    expected_input_schema_from: open_questions

rules:
  - pack: stargraph.bosun.shipwright.gaps
  - pack: stargraph.bosun.shipwright.edits

stores:
  doc: sqlite:./.shipwright/docs.db
  fact: sqlite:./.shipwright/facts.db

checkpoints:
  every: node-exit
  store: sqlite:./.shipwright/checkpoints.db
```
