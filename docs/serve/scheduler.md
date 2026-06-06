# Scheduler

The Stargraph scheduler is the in-process dispatcher that pulls `PendingRun`
entries off the durable queue and starts a `GraphRun` for each. It runs
inside the FastAPI lifespan so a single `stargraph serve` process owns the
entire dispatch loop (locked Decision #5).

The scheduler is a contract — `stargraph.serve.scheduler:Scheduler` — that
backs onto a `PendingStore` Protocol (default impl: `RunHistory.put_pending`
+ `list_pending` + `delete_pending`).

## Topics

- TODO: dispatch loop + concurrency cap.
- TODO: cron parsing + next-fire computation.
- TODO: pending-run lifecycle (queued → claimed → started).
- TODO: graceful drain on SIGTERM.
- TODO: scheduler metrics + `stargraph inspect --scheduler`.
