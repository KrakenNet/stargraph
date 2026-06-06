# SPDX-License-Identifier: Apache-2.0
"""End-to-end replay determinism contract (NFR-2, FR-28).

Temporal-style replay test: a canonical 3-node graph is driven once in
*record* mode (the determinism shims log every wall-clock / RNG / UUID
draw into a recording dict), then again in *replay* mode against the
same recording. The contract per design §3.8.5:

* The post-run state at every step is byte-identical between the two
  runs (canonical-JSON SHA-256 of the per-step ``state`` dict).
* The event sequence is byte-identical when normalized to drop the
  wall-clock fields the shims do *not* cover (``ts`` on
  :class:`stargraph.runtime.events.EventBase`, ``run_duration_ms`` on
  :class:`~stargraph.runtime.events.ResultEvent`).

The "LLM call" is stubbed by a node that consumes the determinism
shims directly (``now()``, ``random()``, ``uuid4()``); no network,
no vcrpy cassette required for this hermetic check (the cassette
layer has its own round-trip test in ``tests/replay/test_must_stub_policy.py``).
The shimmed values land in the state dict so the byte-identical
state-hash check exercises the determinism contract end-to-end.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

import anyio
import pytest

from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.graph import Graph, GraphRun
from stargraph.ir import IRDocument, NodeSpec
from stargraph.ir._models import HaltAction
from stargraph.nodes.base import NodeBase
from stargraph.replay import determinism
from stargraph.runtime.events import ResultEvent

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel


# --------------------------------------------------------------------------- #
# Test fixtures: a node that consumes the determinism shims                   #
# --------------------------------------------------------------------------- #


class _ShimConsumingNode(NodeBase):
    """Node that draws from every determinism shim and writes the result to state.

    This is the stand-in for an LLM-calling node: instead of an HTTP
    cassette, the non-determinism is sourced through the FR-28 shims
    so the replay can re-feed identical values without going off-box.
    """

    def __init__(self, node_id: str) -> None:
        self._id = node_id

    async def execute(
        self,
        state: BaseModel,
        ctx: Any,
    ) -> dict[str, Any]:
        del ctx
        prev_trail: str = getattr(state, "trail", "")
        # Draw one value per shim per node tick. Order matters: the
        # shim recording dict is positional per shim name, so the
        # replay must call them in the same order to pop the right value.
        # Store the shimmed primitives as strings: the POC IR ``state_schema``
        # type map only carries str/int/bool/bytes, and string-encoding is
        # the most aggressive byte-identity check (no float-precision wiggle).
        ts = determinism.now()
        rnd = determinism.random()
        uid = determinism.uuid4()
        new_trail = self._id if not prev_trail else f"{prev_trail},{self._id}"
        return {
            "trail": new_trail,
            "ts_repr": repr(ts),
            "rnd_repr": repr(rnd),
            "uid_repr": str(uid),
        }


class _HaltAfterAllNodesFathom:
    """Tiny stub Fathom that halts once the loop has visited every node.

    Mirrors :class:`tests.integration.test_runtime_loop._HaltOnNthFathom`
    so the loop terminates at a deterministic step boundary regardless of
    record vs replay scheduling.
    """

    def __init__(self, *, halt_after: int) -> None:
        self._halt_after = halt_after
        self._calls = 0

    def mirror_state(self, state: object, *, annotations: dict[str, Any]) -> list[Any]:
        del state, annotations
        return []

    def assert_with_provenance(
        self,
        template: str,
        slots: dict[str, Any],
        provenance: Any = None,
    ) -> None:
        del template, slots, provenance

    def evaluate(self) -> list[Any]:
        self._calls += 1
        if self._calls >= self._halt_after:
            return [HaltAction(reason="done")]
        return []


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _build_graph() -> Graph:
    """3-node sequential graph with state slots for the shim outputs."""
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:replay-determinism-itest",
        nodes=[
            NodeSpec(id="n0", kind="echo"),
            NodeSpec(id="n1", kind="echo"),
            NodeSpec(id="n2", kind="echo"),
        ],
        state_schema={
            "trail": "str",
            "ts_repr": "str",
            "rnd_repr": "str",
            "uid_repr": "str",
        },
    )
    return Graph(ir)


def _canonical_hash(payload: Any) -> str:
    """SHA-256 over canonical JSON (sorted keys, no whitespace)."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalize_event(ev: Any) -> dict[str, Any]:
    """Strip wall-clock fields that the FR-28 shims do not cover.

    ``ts`` (event envelope) and ``run_duration_ms`` (terminal
    :class:`ResultEvent`) are sourced from :func:`datetime.now` /
    monotonic time inside the loop driver, *not* through the
    determinism shims. The byte-identity contract is over the
    *modeled* event payload; wall-clock noise is excluded.
    """
    dump: dict[str, Any] = ev.model_dump(mode="json")
    dump.pop("ts", None)
    if isinstance(ev, ResultEvent):
        dump.pop("run_duration_ms", None)
    return dump


async def _drain_events(run: GraphRun) -> list[Any]:
    received: list[Any] = []
    with anyio.fail_after(5.0):
        while True:
            ev = await run.bus.receive()
            received.append(ev)
            if isinstance(ev, ResultEvent):
                return received


async def _drive_run(
    *,
    run_id: str,
    cp: SQLiteCheckpointer,
    scope: determinism.DeterminismScope,
) -> tuple[list[dict[str, Any]], list[Any]]:
    """Drive one full run inside ``scope`` and return per-step states + events."""
    graph = _build_graph()
    initial_state = graph.state_schema(trail="", ts_repr="", rnd_repr="", uid_repr="")
    registry: dict[str, NodeBase] = {
        "n0": _ShimConsumingNode("n0"),
        "n1": _ShimConsumingNode("n1"),
        "n2": _ShimConsumingNode("n2"),
    }
    run = GraphRun(
        run_id=run_id,
        graph=graph,
        initial_state=initial_state,
        node_registry=registry,
        checkpointer=cp,
        fathom=_HaltAfterAllNodesFathom(halt_after=3),
    )

    events: list[Any] = []

    async def _drive() -> None:
        # The DeterminismScope is a context-var-bound scope; entering it
        # *inside* the producer task ensures the shim calls performed by
        # _ShimConsumingNode see the recording dict.
        with scope:
            await run.start()

    async def _drain() -> None:
        events.extend(await _drain_events(run))

    async with anyio.create_task_group() as tg:
        tg.start_soon(_drive)
        tg.start_soon(_drain)

    # Per-step checkpoint states (in order). One checkpoint per node tick.
    states: list[dict[str, Any]] = []
    step = 0
    while True:
        ckpt = await cp.read_at_step(run_id, step)
        if ckpt is None:
            break
        states.append(ckpt.state)
        step += 1
    return states, events


# --------------------------------------------------------------------------- #
# The contract                                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.integration
async def test_record_then_replay_is_byte_identical(tmp_path: Path) -> None:
    """Record a canonical run, then replay it -- state + event sequence must match.

    The contract is the FR-28 / NFR-2 promise: given a recording dict
    captured during a record-mode run, replaying the same graph against
    that dict yields byte-identical *modeled* output. Wall-clock noise
    on the event envelope (``ts``) is excluded from the byte-identity
    check because it is sourced outside the determinism shims (see
    :func:`_normalize_event` for the exclusion list).
    """
    # ------ Record pass -----------------------------------------------------
    record_recording: dict[str, list[Any]] = {}
    record_scope = determinism.DeterminismScope(
        replay=False,
        recording=record_recording,
    )
    cp_record = SQLiteCheckpointer(tmp_path / "record.sqlite")
    await cp_record.bootstrap()
    try:
        record_states, record_events = await _drive_run(
            run_id="run-canonical",
            cp=cp_record,
            scope=record_scope,
        )
    finally:
        await cp_record.close()

    # Sanity: the record pass actually exercised every shim three times
    # (one per node tick). If this regresses the byte-identity check
    # below would still pass trivially against an empty recording.
    assert len(record_recording.get("now", [])) == 3
    assert len(record_recording.get("random", [])) == 3
    assert len(record_recording.get("uuid4", [])) == 3
    assert len(record_states) == 3
    assert sum(1 for ev in record_events if isinstance(ev, ResultEvent)) == 1

    # ------ Replay pass -----------------------------------------------------
    # Deep-copy the recording so the replay scope's ``pop(0)`` cannot
    # mutate the record-pass evidence we asserted on above.
    replay_recording: dict[str, list[Any]] = {
        name: list(values) for name, values in record_recording.items()
    }
    replay_scope = determinism.DeterminismScope(
        replay=True,
        recording=replay_recording,
    )
    cp_replay = SQLiteCheckpointer(tmp_path / "replay.sqlite")
    await cp_replay.bootstrap()
    try:
        # Same logical run_id as the record pass: replay re-executes the
        # *same* run against a fresh DB, so the modeled events (which carry
        # ``run_id``) are byte-identical to the record-pass envelopes.
        replay_states, replay_events = await _drive_run(
            run_id="run-canonical",
            cp=cp_replay,
            scope=replay_scope,
        )
    finally:
        await cp_replay.close()

    # The replay pass must consume every recorded value exactly: any
    # leftover entry would mean the replay called the shim fewer times
    # than the record did (a loud determinism break per design §3.8.5).
    for name, values in replay_recording.items():
        assert values == [], (
            f"replay did not consume all recorded {name!r} values; leftover: {values!r}"
        )

    # ------ Byte-identity contract -----------------------------------------
    # 1. Per-step state dicts hash identically.
    assert len(replay_states) == len(record_states)
    for i, (rec, rep) in enumerate(zip(record_states, replay_states, strict=True)):
        rec_hash = _canonical_hash(rec)
        rep_hash = _canonical_hash(rep)
        assert rec_hash == rep_hash, (
            f"state hash mismatch at step {i}: record={rec_hash} replay={rep_hash}; "
            f"record_state={rec!r} replay_state={rep!r}"
        )

    # 2. Modeled event sequence (timestamps stripped) hashes identically.
    rec_event_payload = [_normalize_event(ev) for ev in record_events]
    rep_event_payload = [_normalize_event(ev) for ev in replay_events]
    rec_events_hash = _canonical_hash(rec_event_payload)
    rep_events_hash = _canonical_hash(rep_event_payload)
    assert rec_events_hash == rep_events_hash, (
        f"event sequence hash mismatch: record={rec_events_hash} replay={rep_events_hash}; "
        f"record_events={rec_event_payload!r} replay_events={rep_event_payload!r}"
    )

    # 3. Spot-check: the terminal ResultEvent on both sides reports the same
    # final_state dict (the most user-visible byte-identity claim).
    record_result = next(ev for ev in record_events if isinstance(ev, ResultEvent))
    replay_result = next(ev for ev in replay_events if isinstance(ev, ResultEvent))
    assert record_result.final_state == replay_result.final_state
    assert record_result.status == replay_result.status == "done"
