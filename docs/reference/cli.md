# CLI Reference

The `stargraph` CLI is a [Typer](https://typer.tiangolo.com/) app that drives engine
execution, inspection, replay, and serve startup. It is bound via
`[project.scripts]` in `pyproject.toml`:

```toml
[project.scripts]
stargraph = "stargraph.cli:main"
```

`stargraph.cli.app` registers the following subcommands:

| Subcommand       | Purpose                                                |
| ---------------- | ------------------------------------------------------ |
| `run`            | Compile and execute an IR graph end-to-end.            |
| `serve`          | Boot the FastAPI app under uvicorn.                    |
| `inspect`        | Read-only run inspector (timeline / state / facts).    |
| `replay`         | Counterfactual fork from a checkpoint + diff.          |
| `respond`        | Deliver a HITL response to an awaiting-input run.      |
| `simulate`       | Offline rule-firing trace against synthetic fixtures.  |
| `counterfactual` | Compute a cf-derived `graph_hash` from a YAML mutation.|

The helper modules `_inputs.py`, `_progress.py`, `_prompts.py`, and
`_summary.py` factor the interactive surface used by `stargraph run`
(`--inputs` parsing, live progress rendering, HITL prompting,
end-of-run summary). They are not user-facing entry points.

!!! tip "Plugin discovery tracing"
    Set `STARGRAPH_TRACE_PLUGINS=1` (any non-empty value) before invoking
    any subcommand to log every plugin discovery, manifest validation,
    and registration step at `INFO` level via
    [`stargraph.plugin.loader`](python/index.md). Use this when a tool,
    skill, store, or pack is "missing" -- the trace shows whether the
    distribution was discovered, whether its manifest validated, and at
    what `order` it registered.

---

## `stargraph run`

Run a Stargraph graph end-to-end, or print its rule-firing trace without
executing any node (`--inspect`).

**Usage**

```text
stargraph run [OPTIONS] GRAPH
```

`GRAPH` is a path to an IR YAML file. The file is parsed via
`yaml.safe_load`, validated against [`IRDocument`](ir-schema.md#irdocument),
and executed against a SQLite checkpointer
(default: `./.stargraph/run.sqlite`). The single-node loop drives to a
terminal `done` / `failed` status; exit code is `0` on `done`, non-zero
on `failed`.

| Flag                 | Type             | Default                | Description                                                                |
| -------------------- | ---------------- | ---------------------- | -------------------------------------------------------------------------- |
| `--log-file PATH`    | path             | _(none)_               | Append per-event JSONL records to this file.                               |
| `--checkpoint PATH`  | path             | `./.stargraph/run.sqlite` | SQLite checkpoint DB path.                                                 |
| `--inspect`          | flag             | `false`                | Print rule-firing trace without executing nodes (FR-8/9).                  |
| `--inputs / -i K=V`  | str (repeatable) | _(empty)_              | Seed initial state field; key must match the IR `state_schema`.            |
| `--quiet / -q`       | flag             | `false`                | Suppress per-step progress output.                                         |
| `--verbose / -v`     | flag             | `false`                | Print tool result payloads inline.                                         |
| `--no-summary`       | flag             | `false`                | Skip end-of-run summary block.                                             |
| `--summary-json`     | flag             | `false`                | Emit summary as JSON instead of text.                                      |
| `--non-interactive`  | flag             | `false`                | Fail on `WaitingForInputEvent` instead of prompting.                       |
| `--lm-url URL`       | str              | _(none)_               | LLM endpoint URL for DSPy nodes (OpenAI-compatible). Pair with `--lm-model`.|
| `--lm-model NAME`    | str              | _(none)_               | LLM model identifier (e.g. `gpt-oss:20b`).                                 |
| `--lm-key KEY`       | str              | `placeholder`          | API key for the LLM endpoint (`placeholder` works for ollama).             |
| `--lm-timeout SEC`   | int              | `60`                   | LLM call timeout in seconds.                                               |

`--quiet` and `--verbose` are mutually exclusive. `--lm-url` and
`--lm-model` must be supplied together (or neither).

**Examples**

```bash
# Execute a graph with seeded state
stargraph run graphs/triage.yaml -i message="check pack drift" -i severity=3

# Dry-run: print rule-firing trace, no nodes executed
stargraph run graphs/triage.yaml --inspect

# Bind a local LLM for dspy nodes
stargraph run graphs/triage.yaml --lm-url http://localhost:11434 --lm-model gpt-oss:20b
```

See also: [Concepts: IR](../concepts/ir.md),
[Replay](../engine/replay.md), [Counterfactual](../engine/counterfactual.md).

---

## `stargraph serve`

Boot the Stargraph FastAPI app under uvicorn (POC).

**Usage**

```text
stargraph serve [OPTIONS]
```

Resolves the deployment profile via
[`stargraph.serve.profiles.select_profile`](../serve/profiles.md). When
`--profile cleared` is selected, the cleared startup gate refuses
`--allow-pack-mutation` and `--allow-side-effects` and exits non-zero
with a `ProfileViolationError` (FR-32, FR-68, design §11.1, §15).

| Flag                       | Type | Default                 | Description                                                                  |
| -------------------------- | ---- | ----------------------- | ---------------------------------------------------------------------------- |
| `--profile NAME`           | str  | `oss-default`           | Deployment profile (`oss-default` \| `cleared`). Forwarded via `STARGRAPH_PROFILE`. |
| `--host HOST`              | str  | `127.0.0.1`             | Bind host for uvicorn.                                                       |
| `--port PORT`              | int  | `8000`                  | Bind port for uvicorn.                                                       |
| `--db PATH`                | path | _(per-process tmpfile)_ | SQLite checkpointer DB path.                                                 |
| `--audit-log PATH`         | path | _(none)_                | JSONL audit log path; passed to `RunHistory` for the event-offsets index.    |
| `--allow-pack-mutation`    | flag | `false`                 | Permit at-runtime Bosun pack mutation. **Forbidden under `cleared`**.        |
| `--allow-side-effects`     | flag | `false`                 | Permit `write` / `external` side effects. **Forbidden under `cleared`**.     |

**Examples**

```bash
# Local dev boot
stargraph serve --port 8000

# Cleared profile, with persistent checkpoint DB
stargraph serve --profile cleared --db /var/lib/stargraph/run.sqlite \
    --audit-log /var/log/stargraph/audit.jsonl
```

See also: [Serve overview](../serve/overview.md),
[Profiles](../serve/profiles.md), [API](../serve/api.md).

---

## `stargraph inspect`

Read-only run inspector. Three views over a SQLite checkpointer DB plus
a Phase-1 legacy mode that streams raw JSONL events.

**Usage**

```text
stargraph inspect RUN_ID --db PATH                  # timeline (default)
stargraph inspect RUN_ID --db PATH --step N         # state at step N
stargraph inspect RUN_ID --db PATH --diff N M       # CLIPS fact delta
stargraph inspect RUN_ID --log-file PATH            # legacy stream mode
```

| Flag                 | Type        | Default  | Description                                                                                  |
| -------------------- | ----------- | -------- | -------------------------------------------------------------------------------------------- |
| `RUN_ID`             | str (arg)   | _req._   | Run id to inspect (matches `event.run_id` and `checkpoint.run_id`).                          |
| `--db PATH`          | path        | _(none)_ | SQLite Checkpointer DB. Required for timeline / state-at-step / fact-diff views.             |
| `--log-file PATH`    | path        | _(none)_ | JSONL audit log. With `--db`, enriches the timeline; without `--db`, streams events.         |
| `--step N`           | int (>= 0)  | _(none)_ | State-at-step view: print the IR-canonical state dict at step `N`.                           |
| `--diff N M`         | int int     | _(none)_ | Fact-diff view: print CLIPS facts added/removed between step `N` and `M`.                    |

The mode selector is `--diff` > `--step` > timeline. Both `--diff` and
`--step` require `--db`. An empty filter result on the legacy stream
mode is treated as a probable typo and exits non-zero (FR-6 force-loud).

**Examples**

```bash
# Timeline view of run, enriched with audit log
stargraph inspect 0193... --db .stargraph/run.sqlite --log-file run.jsonl

# Snapshot of state at step 7
stargraph inspect 0193... --db .stargraph/run.sqlite --step 7

# CLIPS facts asserted/retracted between step 5 and step 9
stargraph inspect 0193... --db .stargraph/run.sqlite --diff 5 9
```

See also: [Checkpointer](../engine/checkpointer.md),
[Provenance](../concepts/provenance.md).

---

## `stargraph replay`

Fork a counterfactual run from any checkpoint and (optionally) render
the parent-vs-cf `RunDiff`.

**Usage**

```text
stargraph replay RUN_ID --db PATH [--mutation FILE.json]
                     [--from-step N] [--diff/--no-diff]
```

`--mutation` loads a
[`CounterfactualMutation`](../engine/counterfactual.md) from JSON. With
no mutation, an empty no-op mutation is used (still produces a
cf-derived `graph_hash`). The cf-run id is minted by
`GraphRun.counterfactual` (`cf-<uuid>`); the parent's checkpoint rows
are byte-identical post-fork (Temporal "cannot change the past"
invariant).

| Flag                 | Type      | Default     | Description                                                                              |
| -------------------- | --------- | ----------- | ---------------------------------------------------------------------------------------- |
| `RUN_ID`             | str (arg) | _req._      | Parent run id to fork the counterfactual from.                                           |
| `--db PATH`          | path      | _req._      | SQLite Checkpointer DB containing the parent run's checkpoints.                          |
| `--mutation FILE`    | path      | _(none)_    | JSON file describing a `CounterfactualMutation` (state overrides, fact asserts, etc.).   |
| `--from-step N`      | int (>= 0)| `0`         | Step at which to fork the cf-run.                                                        |
| `--diff/--no-diff`   | flag      | `--no-diff` | After forking, render parent vs cf `RunDiff` as canonical IR JSON.                       |

**Examples**

```bash
# Fork a no-op counterfactual at step 0; print only cf-run-id
stargraph replay 0193... --db .stargraph/run.sqlite

# Fork at step 4 with a state override and print the diff
stargraph replay 0193... --db .stargraph/run.sqlite \
    --mutation cf/override.json --from-step 4 --diff
```

See also: [Replay](../engine/replay.md),
[Counterfactual](../engine/counterfactual.md).

---

## `stargraph respond`

Deliver a HITL response to a run that is `awaiting-input`. Thin wrapper
over `POST /v1/runs/{run_id}/respond` on a running `stargraph serve`.

**Usage**

```text
stargraph respond RUN_ID --response FILE.json --actor NAME [--server URL]
```

| Flag                 | Type      | Default                 | Description                                                                  |
| -------------------- | --------- | ----------------------- | ---------------------------------------------------------------------------- |
| `RUN_ID`             | str (arg) | _req._                  | Run id awaiting input.                                                       |
| `--response FILE`    | path      | _req._                  | JSON file containing the analyst response payload.                           |
| `--actor NAME`       | str       | _req._                  | Principal id; sent as `Authorization: Bypass <actor>`.                       |
| `--server URL`       | str       | `http://localhost:8000` | Base URL of the running `stargraph serve` process.                              |

The CLI maps HTTP errors to operator-friendly messages:

* `200` -- `RunSummary` JSON printed to stdout.
* `401` -- `auth failed for actor=...`.
* `404` -- `run ... not found or not awaiting input`.
* `409` -- `run ... not awaiting input -- already responded or in conflicting state`.
* Any other non-2xx -- raw response body printed (FR-6).

**Example**

```bash
stargraph respond 0193... --response analyst-decision.json --actor alice
```

See also: [HITL](../serve/hitl.md), [API](../serve/api.md).

---

## `stargraph simulate`

Offline rule-firing trace for an IR. Validates rule logic against
caller-supplied synthetic node outputs without invoking any tool, LLM,
or checkpoint (FR-9).

**Usage**

```text
stargraph simulate GRAPH --fixtures FIXTURES
```

Both arguments are YAML files. `FIXTURES` is a mapping of `node_id` to
synthetic output dict (one entry per IR node).

| Flag                 | Type      | Default | Description                                          |
| -------------------- | --------- | ------- | ---------------------------------------------------- |
| `GRAPH`              | path (arg)| _req._  | Path to an IR YAML graph definition.                 |
| `--fixtures FILE`    | path      | _req._  | YAML mapping of `node_id` -> synthetic output.       |

Output format mirrors `stargraph run --inspect`: a leading
`graph_hash=<hex>` and `rule_firings=<count>` line followed by one row
per rule firing.

**Example**

```bash
stargraph simulate graphs/triage.yaml --fixtures fixtures/triage-zeros.yaml
```

---

## `stargraph counterfactual`

Compute the counterfactual-derived `graph_hash` for a parent IR + a
mutation YAML, without forking a run (FR-27).

**Usage**

```text
stargraph counterfactual GRAPH --step N --mutate FILE.yaml
```

Validates the mutation YAML through `CounterfactualMutation` (`extra='forbid'`,
so typos surface here) and prints the cf-derived hash. Use this to
verify a mutation file round-trips and to pin the `graph_hash` you
should see in the resulting cf-checkpoint.

| Flag                 | Type      | Default | Description                                                          |
| -------------------- | --------- | ------- | -------------------------------------------------------------------- |
| `GRAPH`              | path (arg)| _req._  | Path to the parent run's IR YAML graph definition.                   |
| `--step N`           | int (>= 0)| _req._  | Checkpoint step index at which to fork (recorded in output).         |
| `--mutate FILE`      | path      | _req._  | YAML file describing a `CounterfactualMutation`.                     |

**Example**

```bash
stargraph counterfactual graphs/triage.yaml --step 4 --mutate cf/swap-tool.yaml
```

Output:

```text
original_graph_hash=...
cf_step=4
derived_graph_hash=...
```

See also: [Counterfactual](../engine/counterfactual.md).
