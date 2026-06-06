# Checkpointer Protocol

The Checkpointer is the storage-driver contract the engine runtime calls into to
persist and restore execution state. Drivers implement it structurally
(`typing.Protocol` — no inheritance required); two ship in-tree:

- `stargraph.checkpoint.sqlite.SQLiteCheckpointer` — aiosqlite + WAL, single-node
- `stargraph.checkpoint.postgres.PostgresCheckpointer` — asyncpg, pgbouncer-safe

## The protocol

```python
from stargraph.checkpoint import Checkpoint, Checkpointer, RunSummary

class Checkpointer(Protocol):
    async def bootstrap(self) -> None: ...
    async def write(self, checkpoint: Checkpoint) -> None: ...
    async def read_latest(self, run_id: str) -> Checkpoint | None: ...
    async def read_at_step(self, run_id: str, step: int) -> Checkpoint | None: ...
    async def list_runs(
        self, *, since: datetime | None = None, limit: int = 100
    ) -> list[RunSummary]: ...
```

`bootstrap()` is idempotent — call it once per process (typically before the
first `start()`). The runtime calls `write()` once per step boundary under
`asyncio.shield` so cancellation does not leave a half-committed checkpoint.

## The Checkpoint record

`Checkpoint` is a Pydantic model with 12 required fields (per design §3.2.1):

| Field | Type | Purpose |
|---|---|---|
| `run_id` | `str` | Stable across restarts; re-used by `resume()` |
| `step` | `int` | Monotonically increasing per run |
| `branch_id` | `str \| None` | Parallel-branch identity; `None` = main |
| `parent_step_idx` | `int \| None` | Parent step for branched checkpoints |
| `graph_hash` | `str` | May be derived (cf-prefix `stargraph-cf-v1...`) |
| `runtime_hash` | `str` | sha256(`python_version + stargraph_version`) |
| `state` | `dict[str, Any]` | JCS-serializable snapshot of state |
| `clips_facts` | `list[Any]` | `save_facts` text-format output |
| `last_node` | `str` | The IR node id that produced this checkpoint |
| `next_action` | `dict[str, Any] \| None` | Pending action; `None` at terminal |
| `timestamp` | `datetime` | Wall clock when the row was written |
| `parent_run_id` | `str \| None` | cf parent; `None` for original runs |
| `side_effects_hash` | `str` | sha256 over recorded tool outputs |

The state is serialized through `orjson` into the JSONB column — see
`stargraph.checkpoint._codec` for the canonical codec.

## Resume contract

`GraphRun.resume()` reads the latest checkpoint by default; `from_step=N` pins
the load. Three failures are loud (FR-6, FR-19, FR-20, FR-27):

1. **Missing run/step** → `CheckpointError(reason="no-checkpoint" | "missing-step")`
2. **cf-derived hash on resume** → `CheckpointError(reason="cf-prefix-hash-refused")`.
   Counterfactual checkpoints are not eligible for resume against the parent
   `run_id`; the cf-prefix `stargraph-cf-v1` marks the row.
3. **graph_hash mismatch** without an applicable IR `migrate` block →
   `CheckpointError(reason="graph-hash-mismatch", expected_hash=..., actual_hash=..., migrate_available=False)`

```python
from stargraph.graph import GraphRun

# Continuation of the same logical run — same run_id, fresh handle.
run = await GraphRun.resume(checkpointer, run_id="run-abc", graph=graph)
summary = await run.wait()  # drive to "done"
```

Pass `graph=...` whenever you have it — the hash check is the FR-20 gate that
catches incompatible IR drift between the persisted run and the in-memory
definition.

## Writing a custom driver

Implement the five `Protocol` methods structurally. The runtime makes no
assumptions about transport — Redis, DynamoDB, an in-memory dict for tests are
all valid. Two contracts to honor:

1. `write()` must be atomic per step. Either the row commits or
   `read_latest()` does not see it.
2. `list_runs()` should treat `since=None` as "no cutoff" and respect `limit`
   for pagination.

Run the conformance suite at `tests/checkpoint/test_protocol_conformance.py`
against your driver to verify the contract.
