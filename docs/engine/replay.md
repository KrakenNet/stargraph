# Replay

`stargraph.replay` provides the cassette and determinism machinery that lets a
recorded run be re-executed with byte-identical outputs. This is the substrate
counterfactuals build on (see [Counterfactuals](counterfactual.md)) and the
testing primitive for non-deterministic upstream services.

## Tool-call cassettes

`ToolCallCassette` records `(tool_id, args_hash) -> output` mappings during a
record-mode run and replays them during a replay-mode run. The args are hashed
through `args_hash` (JCS-canonical sha256) so dict-key insertion order does not
affect cassette lookup:

```python
from stargraph.replay import ToolCallCassette, args_hash

cassette = ToolCallCassette()

# Record mode -- the runtime writes the recorded output back into the cassette.
cassette.record(
    tool_id="weather.get",
    args={"city": "SFO"},
    output={"temp_f": 62},
)

# Replay mode -- the runtime calls .replay(...) before invoking the live tool.
recorded = cassette.replay(tool_id="weather.get", args={"city": "SFO"})
assert recorded == {"temp_f": 62}

# args_hash is exposed for callers that need to key auxiliary maps.
key = args_hash({"city": "SFO"})
```

The cassette is in-memory; serialize it to disk with `model_dump_json()` (it is
a Pydantic model) and load with `model_validate_json()`.

## Replay tutorial

The end-to-end replay flow is:

1. **Record** — execute the graph against live tools with a fresh cassette
   attached. The runtime captures every tool call into the cassette.
2. **Persist** — serialize the cassette alongside the run's checkpoints. The
   `Checkpoint.side_effects_hash` field anchors the cassette to the run.
3. **Replay** — re-execute by passing the same cassette into a new
   `GraphRun`. The runtime looks up `(tool_id, args_hash)` in the cassette
   before any live invocation; a miss is loud
   (`ReplayError(reason="cassette-miss")`).

```python
import json
from pathlib import Path

from stargraph.graph import Graph
from stargraph.replay import ToolCallCassette

# 1. Record
record_cassette = ToolCallCassette()
graph = Graph(ir)
run = await graph.start(checkpointer=ckpt, cassette=record_cassette)
await run.start()
Path("run.cassette.json").write_text(record_cassette.model_dump_json())

# 2. Replay later, possibly in a different process
replay_cassette = ToolCallCassette.model_validate_json(
    Path("run.cassette.json").read_text()
)
replay_run = await graph.start(checkpointer=ckpt, cassette=replay_cassette)
await replay_run.start()  # no live tool calls; outputs match record byte-for-byte
```

## Determinism guards

The engine enforces several determinism invariants at compile time so a record
can in fact be replayed:

- **No `set` / `frozenset` state fields** (FR-28 amendment 6). Set iteration is
  hash-randomized across processes (PEP 456). Use `list[str]` with a declared
  sort or `dict[str, bool]` keyed by would-be members. Violations raise
  `IRValidationError(violation="set-field-forbidden")` at `Graph.__init__`.
- **No write/external side-effect tools in `race`/`any` parallel branches**
  without `allow_unsafe_cancel: true` (FR-12). Cancelling a losing branch
  mid-write would leave half-committed I/O.
- **Compiled `state_schema` is part of the graph hash** (FR-4 component c). A
  silent type widening becomes a structural-hash mismatch on resume.

Comparison helpers live in `stargraph.replay.compare` for diffing two
`RunSummary` event sequences when investigating drift.
