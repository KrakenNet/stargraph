# Counterfactual forks

A *counterfactual* is a what-if branch off a recorded run: at step N, mutate the
state (or facts, or rule pack, or a node output), then re-execute from there.
The original run is untouched — Stargraph enforces the Temporal "cannot change the
past" invariant: every cf-derived checkpoint lives under a fresh `run_id` and a
domain-separated `graph_hash`.

## The mutation builder

`CounterfactualMutation` (in `stargraph.replay.counterfactual`) is the typed
Pydantic builder for the five FR-27 fields a caller may override at the cf
fork point. `extra="forbid"` rejects unknown keys at construction time so
typos are loud:

```python
from stargraph.replay.counterfactual import CounterfactualMutation

mutation = CounterfactualMutation(
    state_overrides={"risk_score": 0.95},      # patch state at fork step
    facts_assert=[{"head": "high-risk"}],      # add CLIPS facts
    facts_retract=[{"head": "low-risk"}],      # remove CLIPS facts
    rule_pack_version="2.4.0",                 # pin a different rule pack
    node_output_overrides={"classifier": {"label": "deny"}},
)
```

All fields default to `None`; the empty mutation is a valid no-op probe — useful
for asking "does this run look the same under cf-replay semantics with no
semantic change?" The derived hash still differs by virtue of the
domain-separation tag.

## Forking a run

`GraphRun.counterfactual()` mints the cf child:

```python
from stargraph.graph import GraphRun

cf_run = await GraphRun.counterfactual(
    checkpointer,
    run_id="run-abc",   # the original run
    step=5,             # fork point
    mutate=mutation,
)

# cf_run.run_id is fresh ("cf-<uuid4>"); cf_run.parent_run_id == "run-abc".
summary = await cf_run.wait()  # drive cf branch to terminal
```

Five things happen inside `counterfactual()` (per design §3.8.4):

1. Load the original checkpoint at `step` (loud-fail on miss via
   `CheckpointError(reason="missing-step")`).
2. Compute the cf-derived `graph_hash` via `derived_graph_hash(...)`:
   `sha256(b"stargraph-cf-v1\x00" + original_hash + b"\x00" + jcs(mutation))`.
   The 12-byte tag prefix lives only in the pre-image — the on-the-wire
   artifact is the 64-char hex digest.
3. Mint a fresh `run_id` (`f"cf-{uuid.uuid4()}"`) so cf checkpoints never
   shadow original rows.
4. Apply `mutation.state_overrides` to the fork-step state in memory only —
   the original checkpoint row is byte-identical at its `(run_id, step)`
   coordinate.
5. Return a fresh `GraphRun` bound to the new `run_id`, pointing at the
   cf-derived `graph_hash` via `parent_run_id`.

## Resume refuses cf-prefix

`GraphRun.resume()` deliberately refuses checkpoints whose `graph_hash` starts
with `stargraph-cf-v1` — a cf-derived row is not eligible for resume against the
parent `run_id` (AC-3.4):

```python
from stargraph.errors import CheckpointError

try:
    await GraphRun.resume(checkpointer, run_id=cf_run.run_id, graph=graph)
except CheckpointError as exc:
    assert exc.reason == "cf-prefix-hash-refused"
```

If you want to continue a cf branch, call `resume()` against the cf `run_id`
itself (the cf branch's own checkpoints carry the cf-prefix hash *and* live
under the cf `run_id`, so the parent-run refusal is what's blocked, not
self-resume).

## Direct hash computation

Most callers use `GraphRun.counterfactual()` and never touch the hash
directly, but `derived_graph_hash` is exposed for test fixtures and audit
tools that need to predict the cf identity before forking:

```python
from stargraph.replay.counterfactual import derived_graph_hash

cf_hash = derived_graph_hash(graph.graph_hash, mutation)
# cf_hash is the same digest GraphRun.counterfactual() will pin for this fork.
```

Two mutations with identical fields produce identical cf-hashes — the
`rfc8785.dumps(...)` JCS canonicalization makes the hash insensitive to
dict-key insertion order.
