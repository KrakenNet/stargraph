# ManualTrigger

`stargraph.triggers.manual.ManualTrigger` is the explicit-caller path used by
both the `stargraph run` CLI subcommand and the `POST /v1/runs` HTTP route.
Unlike [Cron](cron.md) and [Webhook](webhook.md), it does not poll a clock
or listen on a socket — it is the convergence point for synchronous
operator-initiated runs.

Source: `src/stargraph/triggers/manual.py`.

## Lifecycle

| Method | Behaviour |
| --- | --- |
| `init(deps)` | Stash `deps["scheduler"]` (a lifespan-built `stargraph.serve.scheduler.Scheduler`). Raises `StargraphRuntimeError` if the key is missing. |
| `start()` | No-op. No background loop. |
| `stop()` | No-op. Nothing to drain. |
| `routes()` | Returns `[]`. `POST /v1/runs` is mounted by the serve app directly; no plugin-owned routes are needed. |

The trigger is stateless apart from the `Scheduler` reference captured in
`init`. Multiple callers may invoke `enqueue` concurrently; the underlying
`Scheduler` queue is the synchronisation point.

## `enqueue`

```python
def enqueue(
    self,
    graph_id: str,
    params: Mapping[str, Any],
    idempotency_key: str | None = None,
) -> str: ...
```

Delegates to `Scheduler.enqueue` and discards the returned
`asyncio.Future`. Manual callers retrieve the run handle via
`GET /v1/runs/{run_id}` rather than awaiting the future in-process; the
future remains live on the scheduler side and resolves normally when the
run terminates.

Returns the synthesised `run_id` so callers can immediately poll for
terminal state.

!!! note
    `run_id` is currently synthesised as `f"poc-{graph_id}"` to match the
    `POST /v1/runs` route convention in `stargraph.serve.api`. Phase 2 task
    2.13 wires the canonical Checkpointer-persisted `run_id` once the
    pending-row write lands.

Raises `StargraphRuntimeError` if `init` has not been called — the trigger
needs a scheduler reference before it can enqueue.

## CLI / HTTP convergence

Both the `stargraph run` CLI and `POST /v1/runs` resolve to the same
`enqueue` call:

```text
stargraph run                 POST /v1/runs
       \                          /
        \                        /
         ManualTrigger.enqueue(graph_id, params, idempotency_key)
                       │
                       ▼
              Scheduler.enqueue(...)
                       │
                       ▼
              Checkpointer pending row
                       │
                       ▼
              GET /v1/runs/{run_id}
```

This is the FR-3 convergence guarantee: an operator who scripts the CLI
and an operator who scripts the HTTP API see identical run records,
identical idempotency behaviour, and identical observability through
`GET /v1/runs/{run_id}`.

## Example

```python
from stargraph.triggers.manual import ManualTrigger

trigger = ManualTrigger()
trigger.init({"scheduler": scheduler})

run_id = trigger.enqueue(
    graph_id="nautilus_demo",
    params={"agent_id": "agent-42", "intent": "What is CVE-2024-12345?"},
    idempotency_key="op-1234",
)
# poll GET /v1/runs/{run_id} for terminal state
```

## See also

- [Triggers index](index.md) — the `Trigger` Protocol and dispatcher.
- [Serve: scheduler](../../serve/scheduler.md) — the queue `enqueue` writes to.
- [Serve: HTTP API](../../serve/api.md) — the `POST /v1/runs` route.
- [CLI](../cli.md) — the `stargraph run` subcommand.
