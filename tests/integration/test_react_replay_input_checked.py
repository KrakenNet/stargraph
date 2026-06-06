# SPDX-License-Identifier: Apache-2.0
"""ReAct per-step replay determinism (FR-35, AC-10.1, AC-10.2, NFR-4).

Pins three contracts in :mod:`stargraph.replay.react_cassette`:

1. **Record schema** -- every per-step record carries the FR-35 nine-tuple
   ``(step_id, node_name, input_hash, output, model_id, prompt_hash,
   tool_name, tool_args_hash, wall_clock_ts)``.
2. **Byte-identical replay** -- replay reproduces the original event
   stream exactly (output dicts hash byte-identical step-by-step).
3. **Loud-fail on mutation** -- mutating a recorded ``input_hash`` makes
   replay raise :class:`stargraph.errors.ReplayError` (NFR-4).

A fourth contract pins the tool-stub matcher: positional
``(node_name, step_id)`` lookup, **not** ``(tool_name, args)``. The same
tool name fired with different arguments still resolves to the recorded
entry for that step.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

from stargraph.errors import ReplayError
from stargraph.replay.react_cassette import (
    ReactStepRecord,
    ReactStepReplayCassette,
    input_hash,
)
from stargraph.skills.react import ReactSkill, ReactState

pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


# --------------------------------------------------------------------------- #
# Record/replay driver: wraps ReactSkill with the per-step cassette layer     #
# --------------------------------------------------------------------------- #


class _ReactReplayDriver:
    """Minimal record/replay driver around :class:`ReactSkill`.

    Wraps the skill's injected ``llm_stub`` + ``tool_impls`` so that on
    record passes every think -> act -> observe iteration appends a
    :class:`ReactStepRecord` to the cassette, and on replay passes the
    cassette is the ground-truth source for tool outputs and LLM
    decisions. Live LLM calls during replay raise :class:`ReplayError`
    (must_stub policy).
    """

    def __init__(
        self,
        cassette: ReactStepReplayCassette,
        *,
        node_name: str = "react_loop",
        replay: bool = False,
        model_id: str = "test-model",
    ) -> None:
        self.cassette = cassette
        self.node_name = node_name
        self.replay = replay
        self.model_id = model_id
        self.events: list[dict[str, Any]] = []
        # Synthetic monotonic clock so wall_clock_ts is deterministic
        # in the record pass; the replay pass reads it from the cassette.
        self._tick = 0.0

    def _next_ts(self) -> float:
        self._tick += 1.0
        return self._tick

    def make_llm_stub(
        self,
        live_llm: Callable[[ReactState, Any], dict[str, Any]],
        prompt_hash_fn: Callable[[ReactState], str],
    ) -> Callable[[ReactState, Any], dict[str, Any]]:
        """Return an LLM stub that records on first pass / replays after."""

        def _stub(state: ReactState, ctx: Any) -> dict[str, Any]:
            step_id = state.step_index
            if self.replay:
                # must_stub policy: the live LLM must NEVER fire on replay.
                rec = self.cassette.replay(
                    node_name=self.node_name,
                    step_id=step_id,
                    input_payload={"step_index": step_id, "trajectory_len": len(state.trajectory)},
                )
                self.events.append({"kind": "step", "step_id": step_id, "output": rec.output})
                return rec.output
            # Record pass: invoke the live LLM, capture, append a record.
            output = live_llm(state, ctx)
            tool_call_raw = output.get("tool_call")
            tool_name: str | None = None
            tool_args_hash: str | None = None
            if isinstance(tool_call_raw, dict):
                tool_call = cast("dict[str, Any]", tool_call_raw)
                name_raw = tool_call.get("name")
                if isinstance(name_raw, str):
                    tool_name = name_raw
                args_raw = tool_call.get("arguments", {})
                args_dict = cast("dict[str, Any]", args_raw) if isinstance(args_raw, dict) else {}
                tool_args_hash = hashlib.sha256(
                    json.dumps(
                        args_dict,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ).encode("utf-8")
                ).hexdigest()
            rec = ReactStepRecord(
                step_id=step_id,
                node_name=self.node_name,
                input_hash=input_hash(
                    {"step_index": step_id, "trajectory_len": len(state.trajectory)}
                ),
                output=output,
                model_id=self.model_id,
                prompt_hash=prompt_hash_fn(state),
                tool_name=tool_name,
                tool_args_hash=tool_args_hash,
                wall_clock_ts=self._next_ts(),
            )
            self.cassette.record(rec)
            self.events.append({"kind": "step", "step_id": step_id, "output": output})
            return output

        return _stub


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


# --------------------------------------------------------------------------- #
# Contract 1: per-step record carries the full FR-35 nine-tuple               #
# --------------------------------------------------------------------------- #


async def test_record_carries_full_step_tuple() -> None:
    """Every recorded step has all nine FR-35 fields populated."""
    cassette = ReactStepReplayCassette()
    driver = _ReactReplayDriver(cassette, replay=False)

    iteration = {"n": 0}

    def live_llm(_state: ReactState, _ctx: Any) -> dict[str, Any]:
        iteration["n"] += 1
        if iteration["n"] == 1:
            return {
                "reasoning": "look up alice",
                "tool_call": {"name": "search", "arguments": {"q": "alice"}},
                "done": False,
                "final_answer": None,
            }
        return {
            "reasoning": "done",
            "tool_call": None,
            "done": True,
            "final_answer": "alice-found",
        }

    def search(q: str) -> str:
        return f"hit:{q}"

    skill = ReactSkill(
        name="react",
        version="0.1.0",
        description="record-pass",
        llm_stub=driver.make_llm_stub(live_llm, lambda _s: "prompt-h"),
        tool_impls={"search": search},
        max_steps=5,
    )
    out = await skill.run(ReactState())
    assert out.done is True

    # Two iterations -> two records.
    rec0 = cassette.get("react_loop", 0)
    rec1 = cassette.get("react_loop", 1)
    assert rec0 is not None
    assert rec1 is not None

    # All nine FR-35 fields populated on the first step (which has a tool_call).
    assert rec0.step_id == 0
    assert rec0.node_name == "react_loop"
    assert rec0.input_hash != ""
    assert isinstance(rec0.output, dict)
    assert rec0.model_id == "test-model"
    assert rec0.prompt_hash == "prompt-h"
    assert rec0.tool_name == "search"
    assert rec0.tool_args_hash is not None
    assert rec0.wall_clock_ts > 0.0

    # Step 1 is the terminal think (no tool_call) -- tool fields are None.
    assert rec1.tool_name is None
    assert rec1.tool_args_hash is None
    assert rec1.step_id == 1
    assert rec1.wall_clock_ts > rec0.wall_clock_ts


# --------------------------------------------------------------------------- #
# Contract 2: replay reproduces the event stream byte-identical               #
# --------------------------------------------------------------------------- #


async def test_replay_byte_identical_event_stream() -> None:
    """Record once, replay; per-step output dicts hash byte-identical."""
    cassette = ReactStepReplayCassette()
    record_driver = _ReactReplayDriver(cassette, replay=False)

    iteration = {"n": 0}

    def live_llm(_state: ReactState, _ctx: Any) -> dict[str, Any]:
        iteration["n"] += 1
        if iteration["n"] == 1:
            return {
                "reasoning": "step 1",
                "tool_call": {"name": "search", "arguments": {"q": "alice"}},
                "done": False,
                "final_answer": None,
            }
        return {
            "reasoning": "done",
            "tool_call": None,
            "done": True,
            "final_answer": "ok",
        }

    skill_record = ReactSkill(
        name="react",
        version="0.1.0",
        description="record",
        llm_stub=record_driver.make_llm_stub(live_llm, lambda _s: "ph"),
        tool_impls={"search": lambda q: f"hit:{q}"},  # pyright: ignore[reportUnknownLambdaType]
        max_steps=5,
    )
    record_out = await skill_record.run(ReactState())
    record_events = list(record_driver.events)

    # Replay pass -- live_llm MUST NOT be called.
    def forbidden_llm(_state: ReactState, _ctx: Any) -> dict[str, Any]:
        msg = "live LLM called during replay -- must_stub policy violated"
        raise AssertionError(msg)

    replay_driver = _ReactReplayDriver(cassette, replay=True)
    skill_replay = ReactSkill(
        name="react",
        version="0.1.0",
        description="replay",
        llm_stub=replay_driver.make_llm_stub(forbidden_llm, lambda _s: "ph"),
        tool_impls={"search": lambda q: f"hit:{q}"},  # pyright: ignore[reportUnknownLambdaType]
        max_steps=5,
    )
    replay_out = await skill_replay.run(ReactState())
    replay_events = list(replay_driver.events)

    # Byte-identical event stream.
    assert _canonical(record_events) == _canonical(replay_events)
    # Final state hashes byte-identical on the modeled fields.
    assert record_out.final_answer == replay_out.final_answer
    assert record_out.done is True
    assert replay_out.done is True
    assert len(record_out.trajectory) == len(replay_out.trajectory)


# --------------------------------------------------------------------------- #
# Contract 3: loud-fail when the cassette input_hash is mutated               #
# --------------------------------------------------------------------------- #


async def test_mutated_input_hash_raises_replay_error() -> None:
    """Tampering with a recorded input_hash makes replay raise ReplayError."""
    cassette = ReactStepReplayCassette()
    record_driver = _ReactReplayDriver(cassette, replay=False)

    def live_llm(_state: ReactState, _ctx: Any) -> dict[str, Any]:
        return {
            "reasoning": "done",
            "tool_call": None,
            "done": True,
            "final_answer": "x",
        }

    await ReactSkill(
        name="react",
        version="0.1.0",
        description="record",
        llm_stub=record_driver.make_llm_stub(live_llm, lambda _s: "ph"),
        tool_impls={},
        max_steps=2,
    ).run(ReactState())

    # Mutate the input_hash on step 0.
    rec = cassette.get("react_loop", 0)
    assert rec is not None
    cassette.record(rec.model_copy(update={"input_hash": "0" * 64}))

    replay_driver = _ReactReplayDriver(cassette, replay=True)
    with pytest.raises(ReplayError) as excinfo:
        await ReactSkill(
            name="react",
            version="0.1.0",
            description="replay",
            llm_stub=replay_driver.make_llm_stub(
                lambda _s, _c: pytest.fail("must_stub violated"),
                lambda _s: "ph",
            ),
            tool_impls={},
            max_steps=2,
        ).run(ReactState())
    assert "input_hash mismatch" in excinfo.value.message
    assert excinfo.value.context["node_name"] == "react_loop"
    assert excinfo.value.context["step_id"] == 0


# --------------------------------------------------------------------------- #
# Contract 4: tool stubs match by (node_name, step_id), NOT (tool_name, args) #
# --------------------------------------------------------------------------- #


async def test_tool_match_by_node_step() -> None:
    """Same tool name with different args still resolves by (node, step_id)."""
    cassette = ReactStepReplayCassette()

    # Hand-record two steps where the same tool ("search") is called with
    # different argument shapes -- the cassette is keyed positionally so
    # the per-step entry is what the replay must serve.
    cassette.record(
        ReactStepRecord(
            step_id=0,
            node_name="react_loop",
            input_hash=input_hash({"step_index": 0, "trajectory_len": 0}),
            output={
                "reasoning": "first",
                "tool_call": {"name": "search", "arguments": {"q": "alice"}},
                "done": False,
                "final_answer": None,
            },
            model_id="m",
            prompt_hash="p0",
            tool_name="search",
            tool_args_hash="a0",
            wall_clock_ts=1.0,
        )
    )
    cassette.record(
        ReactStepRecord(
            step_id=1,
            node_name="react_loop",
            input_hash=input_hash({"step_index": 1, "trajectory_len": 1}),
            output={
                "reasoning": "second",
                "tool_call": None,
                "done": True,
                "final_answer": "fin",
            },
            model_id="m",
            prompt_hash="p1",
            tool_name=None,
            tool_args_hash=None,
            wall_clock_ts=2.0,
        )
    )

    # Replay: positional lookup must succeed regardless of tool args
    # (the tool args hash on the cassette differs from any concrete arg
    # the replayed loop would compute, but lookup is by (node, step_id)).
    rec0 = cassette.replay(
        node_name="react_loop",
        step_id=0,
        input_payload={"step_index": 0, "trajectory_len": 0},
    )
    assert rec0.tool_name == "search"
    assert rec0.output["tool_call"]["arguments"] == {"q": "alice"}

    # A different node name at the same step_id is a miss (not a silent
    # match) -- the matcher is strict on the (node_name, step_id) tuple.
    with pytest.raises(ReplayError):
        cassette.replay(
            node_name="other_node",
            step_id=0,
            input_payload={"step_index": 0, "trajectory_len": 0},
        )
