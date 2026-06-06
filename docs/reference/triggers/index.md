# Triggers Reference

Triggers are pluggy plugins that emit [`TriggerEvent`](#triggerevent) objects
into the scheduler queue. The four lifecycle methods
(`init` / `start` / `stop` / `routes`) mirror the hookspec wrappers in
`stargraph.plugin.hookspecs` (design §6.3); the scheduler invokes them through
[`dispatch_trigger_lifecycle`](#per-plugin-isolation) so one misbehaving
trigger cannot block the others.

Source: `src/stargraph/triggers/`.

## Catalog

| Trigger | Module | Path |
| --- | --- | --- |
| [Manual](manual.md) | `stargraph.triggers.manual` | CLI `stargraph run` + `POST /v1/runs` |
| [Cron](cron.md) | `stargraph.triggers.cron` | `cronsim`-driven background loop |
| [Webhook](webhook.md) | `stargraph.triggers.webhook` | HMAC-verified `POST` route per spec |

## Trigger Protocol

```python
@runtime_checkable
class Trigger(Protocol):
    def init(self, deps: dict[str, Any]) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def routes(self) -> list[Any]: ...
```

| Method | Purpose |
| --- | --- |
| `init(deps)` | Set up internal state from the `deps` mapping at lifespan startup. `deps` carries the serve `ServeContext`, the `Scheduler`, and any plugin-specific config (e.g. `cron_specs`, `webhook_specs`, `audit_sink`). |
| `start()` | Begin emitting `TriggerEvent`s into the scheduler queue. Manual + Webhook are no-ops here; Cron spawns one `asyncio.Task` per spec. |
| `stop()` | Drain in-flight work and stop emitting events (graceful shutdown). |
| `routes()` | Return FastAPI routes the plugin wants the serve app to mount (empty list for cron-only triggers). |

Implementations may be sync or async. The dispatcher invokes the bound
method directly and the scheduler awaits the result if it is a coroutine.

## TriggerEvent

`stargraph.triggers.TriggerEvent` (subclass of `IRBase`):

| Field | Type | Description |
| --- | --- | --- |
| `trigger_id` | `str` | Stable identifier of the emitting trigger plugin instance (e.g. `"cron:nightly-cve-feed"`, `"webhook:nvd-mirror"`). |
| `scheduled_fire` | `datetime` | The trigger's canonical fire time. Cron uses the cron-tick instant; webhook/manual use `datetime.now(UTC)` at receipt. |
| `idempotency_key` | `str` | Pre-computed dedup key. Cron: `sha256(trigger_id || scheduled_fire)`. Webhook: `sha256(trigger_id || body_hash)`. Manual: caller-supplied UUID. |
| `payload` | `dict[str, Any]` | JSON-serialisable parameters forwarded to the run as `params`. |

The scheduler uses `idempotency_key` to dedupe against pending-run state in
the Checkpointer (FR-9.x, NFR-3) before enqueueing a run.

!!! note
    `IRBase` carries `extra='forbid'`, so unknown top-level fields fail
    validation. `payload` is the escape hatch for trigger-specific data.

## Per-plugin isolation

Pluggy's default behaviour is to halt iteration if any hook impl raises.
That is unsafe for trigger lifecycle hooks: a single bad plugin would
prevent the rest from initialising or shutting down (FR-2, AC-12.2).

`stargraph.plugin.triggers_dispatcher.dispatch_trigger_lifecycle` iterates
`pm.get_plugins()` directly and isolates exceptions per implementation:

```python
@dataclass(frozen=True, slots=True)
class DispatchResult:
    plugin_name: str
    success: bool
    result: Any = None
    error: BaseException | None = None
```

Failures are logged via the structlog `stargraph.plugin.triggers` logger and
captured in the returned list. `collect_trigger_routes(pm)` applies the
same isolation to the no-arg `trigger_routes` hook.

```python
from stargraph.triggers import (
    DispatchResult,
    collect_trigger_routes,
    dispatch_trigger_lifecycle,
)

results = dispatch_trigger_lifecycle(pm, "trigger_init", deps)
for r in results:
    if not r.success:
        logger.error("trigger %s failed: %s", r.plugin_name, r.error)
```

## Entry-point group

Trigger plugins register under the `stargraph.triggers` entry-point group.
Discovery is performed by the standard pluggy loader at lifespan startup;
the Phase 1 plugin loader auto-registers anything declared in a
distribution's `[project.entry-points."stargraph.triggers"]` table.

```toml
# pyproject.toml of a trigger-providing distribution
[project.entry-points."stargraph.triggers"]
my_trigger = "my_pkg.triggers:MyTriggerPlugin"
```

## See also

- [Manual trigger](manual.md)
- [Cron trigger](cron.md)
- [Webhook trigger](webhook.md)
- [Serve: triggers](../../serve/triggers.md) — operational guide.
- [Plugin manifest](../plugin-manifest.md) — the `stargraph.triggers`
  entry-point group and adjacent groups.
