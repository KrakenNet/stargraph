# Stargraph Serve — Overview

Stargraph's `serve` subsystem is the FastAPI + WebSocket surface that exposes
the engine to operators, agents, and human-in-the-loop workflows. It pins
a profile-driven authentication chain, a default-deny capability gate, and
a single-process invariant (locked Decision #5).

The serve surface is the contract between Stargraph and the outside world:
HTTP routes, WebSocket streams, scheduler triggers, run-history queries,
and HITL response handling. Everything else (engine, replay, fathom) sits
behind it.

## Topics

- [HTTP API](api.md) — REST routes under `/v1/*`.
- [WebSocket stream](ws.md) — `/v1/runs/{id}/stream` events.
- [Triggers](triggers.md) — cron, webhook, and idempotency-keyed inbound.
- [Scheduler](scheduler.md) — pending-runs queue + dispatch.
- [Run history](runs.md) — `runs_history` table + listing API.
- [CLI](cli.md) — `stargraph serve` flags + lifecycle.
- [Bosun packs](bosun.md) — pack discovery + signing in serve context.
- [Profiles](profiles.md) — `oss-default` + `cleared`.
- [Nautilus broker](nautilus.md) — broker-emit integration.
- [HITL](hitl.md) — human-in-the-loop pause/respond flow.
- [Artifacts](artifacts.md) — content-addressed artifact store.

TODO: expand with cross-references to the matching `stargraph.serve.*` modules.
