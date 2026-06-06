# Run History

`runs_history` is the SQLite table that records every run's lifecycle
metadata: `(run_id, status, graph_hash, trigger_source, started_at,
finished_at, duration_ms, parent_run_id)`. The `RunHistory` class
(`stargraph.serve.history:RunHistory`) is the read+write API; the
`/v1/runs` listing route + `stargraph inspect <run_id>` CLI both consume it.

The companion `pending_runs` table holds the durable scheduler queue;
both tables share the same SQLite database file as the checkpointer
(single-process invariant).

## Topics

- TODO: `runs_history` DDL + index strategy.
- TODO: parent-run linkage for cf-runs (`parent_run_id`).
- TODO: status transitions + audit emission.
- TODO: listing API filters (status, trigger_source, time-range).
- TODO: retention + pruning policy.
