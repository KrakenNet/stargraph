# How to Persist with a Checkpointer

Attach a checkpointer so a Stargraph graph can pause, resume, and replay.
This guide covers the v1 shipped path (`SQLiteCheckpointer`) and the
contract for writing your own driver.

## TL;DR — use the SQLite driver

The shipped driver is `stargraph.checkpoint.sqlite.SQLiteCheckpointer`. It
uses `aiosqlite` + WAL mode and is the default for `stargraph run`:

```bash
stargraph run my-graph.yaml --checkpoint /var/stargraph/run.sqlite
```

If `--checkpoint` is omitted, the CLI defaults to `./.stargraph/run.sqlite`.

## Wire it imperatively (Python)

```python
from stargraph.graph import Graph
from stargraph.ir._models import IRDocument
from stargraph.checkpoint.sqlite import SQLiteCheckpointer

ir = IRDocument(...)        # loaded or constructed elsewhere
graph = Graph(ir)

checkpointer = SQLiteCheckpointer("/var/stargraph/run.sqlite")
await checkpointer.bootstrap()                    # idempotent schema setup

run = await graph.start(checkpointer=checkpointer)
summary = await run.start()
```

Bootstrap is idempotent — call it once per process at startup. The
checkpointer is `asyncio.shield`-wrapped at the engine boundary
(`stargraph.runtime.dispatch`), so a cancelled run cannot tear a
checkpoint row in half.

## The `Checkpointer` Protocol

If you want a different store (postgres, S3-backed sqlite, custom),
implement the structural Protocol at
[`src/stargraph/checkpoint/protocol.py`](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/checkpoint/protocol.py):

```python
class Checkpointer(Protocol):
    async def bootstrap(self) -> None: ...
    async def write(self, checkpoint: Checkpoint) -> None: ...
    async def read_latest(self, run_id: str) -> Checkpoint | None: ...
    async def read_at_step(self, run_id: str, step: int) -> Checkpoint | None: ...
    async def list_runs(self, *, since=None, limit=100) -> list[RunSummary]: ...
```

`Checkpoint` and `RunSummary` are Pydantic models in the same module.
Pin your implementation to the Protocol at type-check time:

```python
from stargraph.checkpoint.protocol import Checkpointer

_: type[Checkpointer] = MyCustomCheckpointer    # mypy / pyright check
```

## Distribution: imperative-only in v1

There is **no `stargraph.checkpointers` entry-point group** in v1.x. Pass
your checkpointer instance into `Graph.start(checkpointer=...)`
directly. A discovery-based plugin path (entry-point group +
`--checkpointer <name>` CLI flag) is on the post-1.0 roadmap; track
the boundary list at [v1 limits](../reference/v1-limits.md).

## Resume

Cold-restart-only in v1. After process exit:

```python
run = await graph.start(checkpointer=checkpointer)
await run.resume(run_id="...")    # picks up at the latest checkpoint
```

`graph_hash` and `runtime_hash` mismatches between the resumed
checkpoint and the loaded graph raise `CheckpointError` loudly (FR-20).

## Replay

The `stargraph replay` CLI reconstructs a run from its checkpoint
sequence, optionally with a counterfactual mutation overlay. See
[Replay](../engine/replay.md) for the full surface.
