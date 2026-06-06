# `MemoryWriteNode`

POC node (FR-27, design §3.4) that persists a single
[`Episode`](../python/index.md) into an injected memory-store provider — a
[`MemoryStore`](../python/index.md) such as
`stargraph.stores.sqlite_memory.SQLiteMemoryStore`.

## Constructor

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `memory_store` | `MemoryStore` | required (positional) | Injected provider implementing the `MemoryStore` protocol. |
| `episode_field` | `str` | `"episode"` | Name of the state field carrying the `Episode` payload. |

`memory_store` is positional; `episode_field` is keyword-only.

## State contract

- **Reads** — `state.<episode_field>` (default `state.episode`).
- **Writes** — `{"memory_written": True, "episode_id": episode.id}`.

The `(user, session, agent)` triple is read from `ctx`. Phase-1's
[`ExecutionContext`](base.md) Protocol is intentionally minimal (`run_id`
only), so the node falls back to `"anon"` / `"default"` / `"default"` when
the concrete context object lacks the optional fields. Phase 2 tightens the
Protocol (design §3.4).

## Side effects + replay

| Field | Value |
| --- | --- |
| `SIDE_EFFECTS` | `SideEffects.write` |
| `REPLAY_POLICY` | `ReplayPolicy.must_stub` |

Mutates external state — must be stubbed under replay (FR-33, design §3.4.2).

## YAML

```yaml
nodes:
  - id: persist_episode
    kind: memory
    spec:
      episode_field: episode
state_schema:
  episode: dict
```

The `memory:` store binding lives at the IR top level:

```yaml
stores:
  memory: redis://localhost:6379/0
```

## Errors

- `AttributeError` — `state.<episode_field>` missing.
- Whatever `MemoryStore.put(...)` raises — provider-specific.

## See also

- [`NodeBase`](base.md) — abstract contract.
- [IR Schema](../ir-schema.md) — `stores.memory:` binding.
