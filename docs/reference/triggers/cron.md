# CronTrigger

`stargraph.triggers.cron.CronTrigger` is the cron-driven trigger plugin. One
instance owns N [`CronSpec`](#cronspec) rows; on `start` it spawns one
background `asyncio.Task` per spec that loops:

1. Compute `next_fire` via `cronsim.CronSim` (DST-safe, IANA TZ via
   `zoneinfo.ZoneInfo`).
2. `await asyncio.sleep(delay)` until then.
3. Compute the [idempotency key](#idempotency-key).
4. Enqueue via `Scheduler.enqueue`.
5. Repeat.

Source: `src/stargraph/triggers/cron.py`.

## CronSpec

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `trigger_id` | `str` | required | Stable identifier (e.g. `"cron:nightly-cve-feed"`). Goes into the idempotency key, so it must be unique across the deployment. |
| `cron_expression` | `str` | required | Standard 5-field cron expression. Parsed by `cronsim.CronSim`; invalid syntax raises at `init`, not at first fire. |
| `tz` | `str` | required | IANA timezone name (e.g. `"UTC"`, `"America/New_York"`). Resolved via `zoneinfo.ZoneInfo` at `init`. |
| `graph_id` | `str` | required | Target graph to enqueue when the trigger fires. |
| `params` | `dict[str, Any]` | `{}` | JSON-serialisable parameter dict forwarded to the run. |
| `missed_fire_policy` | `Literal["fire_once_catchup", "skip"]` | `"fire_once_catchup"` | See [missed-fire policy](#missed-fire-policy). |

## Lifecycle

| Method | Behaviour |
| --- | --- |
| `init(deps)` | Stash `deps["scheduler"]` and parse `deps["cron_specs"]`. Eagerly resolves each `tz` and constructs a `cronsim.CronSim` so bad config fails fast at startup. Raises `StargraphRuntimeError` if `deps` is missing required keys or the spec list is empty. |
| `start()` | Idempotent. Spawns one `asyncio.Task` per spec, named `stargraph.triggers.cron.<trigger_id>`. |
| `stop()` | Idempotent. Cancels each fire-loop task. The Protocol's `stop` is sync; awaiting cancellations belongs in the async lifespan dispatcher. |
| `routes()` | Returns `[]`. Cron has no HTTP surface. |

## Why `cronsim`?

Design §6.1 picked `cronsim` for DST-safety. `croniter` silently mishandles
tz transitions; the design's research call rejected it. `cronsim.CronSim`
is fed `datetime.now(zone)` each loop iteration, so DST forward/backward
shifts produce one fire each (cronsim resolves the ambiguity). IANA TZ
storage is per-trigger, with a UTC server recommendation in the air-gap
guide.

## Idempotency key

```python
@staticmethod
def idempotency_key(trigger_id: str, scheduled_fire: datetime) -> str:
    payload = f"{trigger_id}|{scheduled_fire.isoformat()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

The ISO format includes the tz offset, so the same wall-clock instant in
different zones produces distinct keys (a 09:00 `America/New_York` fire
and a 09:00 `UTC` fire are different events, correctly).

## Missed-fire policy

| Value | Semantics |
| --- | --- |
| `fire_once_catchup` (default) | On `start`, if the most recent scheduled fire was missed (system was down), fire once with the missed `scheduled_fire` so the idempotency key matches what a never-down system would have produced. The Checkpointer dedupe path drops the duplicate if it already fired; otherwise the run executes. Subsequent fires resume the normal forward cadence. |
| `skip` | Silently skip any missed fires; the first fire is the next future `next_fire`. Use for triggers whose signal value degrades after the deadline (heartbeat pings). |

The catchup probe walks `cronsim.CronSim` forward from one day ago and
returns the last entry `<= now`. The lookback is bounded at 2000 entries
defensively (cron expressions fire at most every minute, so ~1440 within
a 24h window).

!!! warning
    The in-memory `_last_fire` dict is the POC stand-in; Phase 2 task 2.13
    reads `last_fire` from the Checkpointer for cross-restart durability.

## Precision

Per NFR-3, the scheduler precision target is ±100ms. `asyncio.sleep` is
used in place of `anyio.sleep_until` because the difference is academic at
that target and the import surface stays smaller.

## Failure isolation

Per-spec task isolation: a single bad fire iteration logs and continues
(the loop sleeps 1s before retrying to avoid a tight failure loop). One
`CronSpec` failing does not kill peers — FR-2 plugin-isolation spirit
applied at the per-spec layer.

`asyncio.CancelledError` is the expected exit path on `stop`; the loop
returns cleanly.

## Example

```yaml
# stargraph.yaml fragment
triggers:
  cron:
    - trigger_id: cron:nightly-cve-feed
      cron_expression: "0 2 * * *"
      tz: America/New_York
      graph_id: cve_ingest
      params:
        feed: nvd
      missed_fire_policy: fire_once_catchup
```

```python
from stargraph.triggers.cron import CronSpec, CronTrigger

trigger = CronTrigger()
trigger.init({
    "scheduler": scheduler,
    "cron_specs": [
        CronSpec(
            trigger_id="cron:nightly-cve-feed",
            cron_expression="0 2 * * *",
            tz="America/New_York",
            graph_id="cve_ingest",
            params={"feed": "nvd"},
        ),
    ],
})
trigger.start()
# ... run ...
trigger.stop()
```

## See also

- [Triggers index](index.md)
- [Manual trigger](manual.md)
- [Webhook trigger](webhook.md)
- [Serve: scheduler](../../serve/scheduler.md)
