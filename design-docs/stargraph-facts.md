# Stargraph — Fact Vocabulary Specification

**Status:** Draft v0.1
**Purpose:** The contract between Stargraph's runtime, Fathom rule packs, and user-defined rules. Every pack composes against this surface.

> **Stability promise:** Stargraph will not remove or rename `stargraph.*` facts within a major version. New slots may be added with defaults; existing slots will not change type.

---

## 1. Namespacing

| Namespace | Owner | Mutability |
|---|---|---|
| `stargraph.*` | Runtime | Emitted by Stargraph; rules read only |
| `bosun.*` | Governance packs | Emitted/consumed by Bosun packs |
| `user.*` | Application | User code and rules |
| `<plugin>.*` | Plugin authors | Must register prefix in plugin manifest |

Unregistered namespaces are rejected at graph load.

---

## 2. Provenance slots (universal)

Every fact representing a value-bearing claim **must** carry the provenance bundle:

| Slot | Type | Required | Notes |
|---|---|---|---|
| `origin` | enum: `llm` \| `tool` \| `user` \| `rule` \| `model` \| `external` \| `runtime` | yes | Class of producer |
| `source` | string | yes | Identifier of the producer (model name, tool name, rule ID) |
| `run_id` | string | yes | Run that produced the fact |
| `step` | int | yes | Step number when produced |
| `confidence` | float [0,1] | no | Defaults to 1.0 for `tool`/`runtime`/`user` |
| `timestamp` | ISO 8601 | yes | Produced-at time, UTC |

Facts that are pure structural events (e.g., `stargraph.transition`) carry `origin=runtime` and skip `confidence`.

---

## 3. Core runtime facts (`stargraph.*`)

### `stargraph.run`
Run lifecycle. One per run, slots updated as it progresses.
```
(stargraph.run id=<run_id> graph_hash=<sha> started=<ts> status=<active|done|failed|paused>)
```

### `stargraph.transition`
Emitted on every node-to-node move.
```
(stargraph.transition from=<node> to=<node> rule=<rule_id> step=<n> reason=<str>)
```

### `stargraph.node-started` / `stargraph.node-completed`
```
(stargraph.node-started name=<node> step=<n> type=<dspy|ml|tool|retrieval|memory|sub>)
(stargraph.node-completed name=<node> step=<n> duration_ms=<int> ok=<bool>)
```

### `stargraph.tool-call` / `stargraph.tool-result`
```
(stargraph.tool-call name=<tool> namespace=<ns> args=<json> step=<n> call_id=<id>)
(stargraph.tool-result call_id=<id> ok=<bool> result=<json> error=<str?> step=<n>)
```

### `stargraph.tokens-used`
Per-call. Aggregation is a Bosun pack concern.
```
(stargraph.tokens-used model=<str> input=<int> output=<int> step=<n>)
```

### `stargraph.error`
```
(stargraph.error scope=<node|rule|tool|runtime> message=<str> step=<n> recoverable=<bool>)
```

### `stargraph.checkpoint`
```
(stargraph.checkpoint id=<ckpt_id> step=<n>)
```

### `stargraph.evidence`
The general-purpose value-bearing fact. Used for any field a rule may want to reason over.
```
(stargraph.evidence
  field=<dotted.path>
  value=<any>
  origin=... source=... run_id=... step=... confidence=... timestamp=...)
```

### `stargraph.disagreement`
Emitted when dual-truth state detects mismatch between LLM-derived and CLIPS-inferred values.
```
(stargraph.disagreement field=<path> llm=<value> rules=<value> step=<n>)
```

---

## 4. Bosun fact conventions (`bosun.*`)

Reference patterns for governance packs. Packs may add their own under their own subnamespace (`bosun.budgets.*`).

### `bosun.budget`
```
(bosun.budget tokens_remaining=<int> calls_remaining=<int> cost_remaining=<float>)
```

### `bosun.audit`
```
(bosun.audit event=<str> subject=<str> step=<n> details=<json>)
```

### `bosun.violation`
```
(bosun.violation pack=<name> rule=<id> severity=<info|warn|error|halt> message=<str> step=<n>)
```

---

## 5. User-defined facts (`user.*`)

Authors declare templates in graph YAML:

```yaml
facts:
  user.intent:
    slots: [value: str, confidence: float]
  user.entity:
    slots: [type: str, name: str, mentions: int]
```

Templates are validated at load. Facts violating templates fail-fast.

---

## 6. Lifecycle

| Lifecycle | Behavior |
|---|---|
| **Run-scoped** (default) | Persists for the run; available on resume |
| **Step-scoped** | Retracted at the next transition; useful for ephemeral signals |
| **Pinned** | Persists across runs in `FactStore`; promotion is explicit |

Declared per template:
```yaml
facts:
  user.intent: { lifecycle: run }
  user.transient_signal: { lifecycle: step }
```

---

## 7. Reserved actions (rule emissions)

Rules may emit these into working memory; the runtime consumes and clears them per step:

| Action | Effect |
|---|---|
| `(action.goto target=<node>)` | Set next node |
| `(action.parallel targets=[<node>...] join=<node> strategy=<...>)` | Spawn parallel branches |
| `(action.halt reason=<str>)` | End the run |
| `(action.retry node=<node> backoff_ms=<int>)` | Re-execute node |
| `(action.assert fact=<template> slots=<json>)` | Add a fact |
| `(action.retract pattern=<json>)` | Remove matching facts |

Multiple `goto`/`halt` actions in a single rule firing pass are an error.

---

## 8. Versioning

Fact templates are versioned with the pack/graph. Breaking changes require:
- A new major version of the pack
- Or a `migrate` block in the graph YAML

Adding optional slots is non-breaking.

---

## 9. Anti-patterns

- **Don't put runtime concerns in `user.*`.** If the runtime should care, it's `stargraph.*`.
- **Don't overload `stargraph.evidence`** when a domain-specific template is clearer.
- **Don't omit provenance** because it's "obvious." Cleared environments and counterfactual replay both require it.
- **Don't write rules that match facts across unrelated namespaces** without explicit reason — couples your rule to packs you didn't author.
