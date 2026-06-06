# `stargraph serve` CLI

Boots the Stargraph FastAPI app via `stargraph.serve.api.create_app` under
`uvicorn.run`. Profile selection, graph loading, LLM endpoint wiring,
and the SQLite checkpointer all happen at boot.

For the other six subcommands (`run`, `inspect`, `simulate`, `counterfactual`,
`replay`, `respond`) see the [CLI reference](../reference/cli.md).

## Synopsis

```bash
stargraph serve \
  [--profile <name>] [--host <addr>] [--port <int>] \
  [--db <path>] [--audit-log <path>] \
  [--graph <ir.yaml>]... \
  [--lm-url <url> --lm-model <id>] [--lm-key <key>] [--lm-timeout <sec>] \
  [--allow-pack-mutation] [--allow-side-effects]
```

## Flags

### Server

| Flag | Default | Description |
|---|---|---|
| `--profile` | `oss-default` | Deployment profile (`oss-default` or `cleared`). Forwarded as `STARGRAPH_PROFILE` to `stargraph.serve.profiles.select_profile`. |
| `--host` | `127.0.0.1` | uvicorn bind host. |
| `--port` | `8000` | uvicorn bind port. |

### State

| Flag | Default | Description |
|---|---|---|
| `--db` | temp file | SQLite checkpointer DB path. When unset, a per-process tempdir is used (not durable). Phase 3 reads `stargraph.toml: serve.checkpoint.path`. |
| `--audit-log` | unset | JSONL audit-log path. Builds the `run_event_offsets` index in `RunHistory`; `get_event_offset` returns `None` for all lookups when unset. |

### Graphs

| Flag | Default | Description |
|---|---|---|
| `--graph <path>` | none | IR YAML to load and register at boot. Repeatable. The graph's `id` (e.g. `graph:sdw-pipeline`) is the key `POST /v1/runs` uses. Each `--graph` builds a per-graph node registry resolving each `NodeSpec.kind` (`module:Class`) to a real callable; an unregistered `graph_id` falls back to a synthetic POC `RunSummary`. |

### LLM (DSPy nodes)

| Flag | Default | Description |
|---|---|---|
| `--lm-url` | unset | OpenAI-compatible endpoint URL. Pair with `--lm-model`. No-op when both are unset; `typer.BadParameter` if exactly one is set. |
| `--lm-model` | unset | Model id (e.g. `gpt-oss:20b`). |
| `--lm-key` | `placeholder` | API key; works as-is for ollama. |
| `--lm-timeout` | `60` | Per-call timeout (seconds). |

### Profile escape hatches

Both flags below are FORBIDDEN under `--profile cleared` (FR-32, FR-68,
design §11.1, §15). The cleared startup gate raises
`stargraph.errors.ProfileViolationError` before any
I/O so misconfiguration shows a clear non-zero exit on stderr.

| Flag | Description |
|---|---|
| `--allow-pack-mutation` | Permit at-runtime Bosun pack mutation (developer convenience). |
| `--allow-side-effects` | Permit nodes/tools declaring `side_effects ∈ {write, external}`. Defense in depth: the engine still REFUSES write/external under cleared regardless of this flag. |

## Lifespan

On startup, `stargraph serve` runs this sequence inside the FastAPI lifespan:

1. `select_profile()` — resolves the profile from `STARGRAPH_PROFILE`.
2. Cleared-profile startup gate — checks `--allow-*` flags, raises if violated.
3. `_configure_lm(...)` — wires DSPy LM if `--lm-url` + `--lm-model` set.
4. `SQLiteCheckpointer.bootstrap()` — creates `runs_history` + `pending_runs` tables.
5. `RunHistory.bootstrap()` — builds the JSONL audit-offsets index.
6. `FilesystemArtifactStore.bootstrap()` — creates artifact root + NFS-refusal probe.
7. `Scheduler.set_deps(deps)` + `set_run_history(run_history)` + `start()`.
8. `broker_lifespan()` — composes the Nautilus broker context (soft-fail on missing `nautilus.yaml`).

On shutdown the order reverses: scheduler stops, broker tears down,
checkpointer closes.

## Examples

Boot with the default profile + no graphs (synthetic POC runs only):

```bash
stargraph serve
```

Persistent state + one registered graph:

```bash
stargraph serve \
  --db ./stargraph.sqlite \
  --audit-log ./audit.jsonl \
  --graph demos/sentinel_dark_watch/graph/stargraph.yaml
```

Cleared profile with an external LLM endpoint:

```bash
stargraph serve \
  --profile cleared \
  --lm-url http://localhost:11434/v1 \
  --lm-model gpt-oss:20b
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Clean shutdown via SIGINT/SIGTERM. |
| `1` | Generic uvicorn / runtime error. |
| `2` | Typer parse error (invalid flag combo, e.g. `--lm-url` without `--lm-model`). |
| non-zero | Cleared-profile gate raised `ProfileViolationError`. |

## See also

- [HTTP API](api.md) — route inventory served by the boot.
- [Profiles](profiles.md) — `oss-default` vs `cleared` capability sets.
- [Scheduler](scheduler.md) — lifecycle of in-flight runs.
- [Run history](runs.md) — `RunHistory` semantics.
- [Nautilus broker](nautilus.md) — broker lifespan composition.
- [`reference/cli.md`](../reference/cli.md) — full CLI reference (run, inspect, simulate, counterfactual, replay, respond).
