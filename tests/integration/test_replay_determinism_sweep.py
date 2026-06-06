# SPDX-License-Identifier: Apache-2.0
"""ReAct replay determinism source sweep (FR-35, NFR-2).

Phase-5 calibration sweep that extends the per-step contracts pinned by
``test_react_replay_input_checked.py`` across a *fleet* of ten distinct
ReAct runs. Each run varies tool arguments, prompt content, and
trajectory length so the byte-identical replay assertion exercises the
positional ``(node_name, step_id)`` matcher under realistic skew.

Three contracts are pinned here:

1. **Sweep determinism** -- for each of the ten distinct inputs, replay
   reproduces the recorded event stream byte-identical (per-step output
   dicts hash equal under canonical JSON).
2. **Mutated input_hash loud-fails** -- mutating the recorded
   ``input_hash`` on a single cassette in the sweep makes that replay
   raise :class:`stargraph.errors.ReplayError`.
3. **Prompt-hash drift surfaces as input-hash mismatch** -- because the
   per-step ``input_payload`` carries the prompt fingerprint, a replay
   that re-derives a different ``prompt_hash`` than the record-pass
   trips the ``input_hash`` check and raises :class:`ReplayError`.
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
# Driver: per-step record/replay around ReactSkill, parameterised by prompt   #
# fingerprint so the input_payload carries prompt-hash drift sensitivity.     #
# --------------------------------------------------------------------------- #


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _step_payload(state: ReactState, prompt_hash_value: str) -> dict[str, Any]:
    """Per-step payload hashed into ``input_hash``.

    Including ``prompt_hash`` here is the contract that lets a replay
    pass detect a drifted prompt: the cassette's ``input_hash`` was
    recorded with the original prompt fingerprint, so a replay that
    derives a different fingerprint computes a different ``input_hash``
    and ``ReactStepReplayCassette.replay`` loud-fails.
    """
    return {
        "step_index": state.step_index,
        "trajectory_len": len(state.trajectory),
        "prompt_hash": prompt_hash_value,
    }


class _SweepDriver:
    """Record/replay driver wrapping the LLM stub of :class:`ReactSkill`."""

    def __init__(
        self,
        cassette: ReactStepReplayCassette,
        *,
        node_name: str = "react_loop",
        replay: bool = False,
        model_id: str = "sweep-model",
    ) -> None:
        self.cassette = cassette
        self.node_name = node_name
        self.replay = replay
        self.model_id = model_id
        self.events: list[dict[str, Any]] = []
        self._tick = 0.0

    def _next_ts(self) -> float:
        self._tick += 1.0
        return self._tick

    def make_llm_stub(
        self,
        live_llm: Callable[[ReactState, Any], dict[str, Any]],
        prompt_hash_fn: Callable[[ReactState], str],
    ) -> Callable[[ReactState, Any], dict[str, Any]]:
        def _stub(state: ReactState, ctx: Any) -> dict[str, Any]:
            step_id = state.step_index
            ph = prompt_hash_fn(state)
            payload = _step_payload(state, ph)
            if self.replay:
                rec = self.cassette.replay(
                    node_name=self.node_name,
                    step_id=step_id,
                    input_payload=payload,
                )
                self.events.append({"kind": "step", "step_id": step_id, "output": rec.output})
                return rec.output
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
                input_hash=input_hash(payload),
                output=output,
                model_id=self.model_id,
                prompt_hash=ph,
                tool_name=tool_name,
                tool_args_hash=tool_args_hash,
                wall_clock_ts=self._next_ts(),
            )
            self.cassette.record(rec)
            self.events.append({"kind": "step", "step_id": step_id, "output": output})
            return output

        return _stub


# --------------------------------------------------------------------------- #
# Ten distinct ReAct scenarios (varied tool args, prompts, intermediate steps) #
# --------------------------------------------------------------------------- #


def _scenario(
    *,
    query: str,
    tool: str,
    final: str,
    intermediate: int,
) -> dict[str, Any]:
    """Build a record-pass spec: ``intermediate`` tool calls + a final."""
    return {
        "query": query,
        "tool": tool,
        "final": final,
        "intermediate": intermediate,
    }


_SCENARIOS: list[dict[str, Any]] = [
    _scenario(query="alice", tool="search", final="alice-found", intermediate=1),
    _scenario(query="bob", tool="search", final="bob-found", intermediate=1),
    _scenario(query="paris", tool="lookup", final="paris-fr", intermediate=2),
    _scenario(query="rome", tool="lookup", final="rome-it", intermediate=2),
    _scenario(query="42", tool="calc", final="answer-42", intermediate=1),
    _scenario(query="primes", tool="calc", final="2-3-5-7", intermediate=3),
    _scenario(query="weather", tool="fetch", final="sunny", intermediate=1),
    _scenario(query="news", tool="fetch", final="quiet", intermediate=2),
    _scenario(query="empty", tool="search", final="none", intermediate=0),
    _scenario(query="long-trajectory", tool="search", final="ok", intermediate=4),
]


def _make_live_llm(spec: dict[str, Any]) -> Callable[[ReactState, Any], dict[str, Any]]:
    """Build a live-LLM stub that fires ``intermediate`` tool calls then ends."""
    intermediate: int = int(spec["intermediate"])
    tool_name: str = str(spec["tool"])
    query: str = str(spec["query"])
    final: str = str(spec["final"])

    def live_llm(state: ReactState, _ctx: Any) -> dict[str, Any]:
        if state.step_index < intermediate:
            return {
                "reasoning": f"call {tool_name} #{state.step_index}",
                "tool_call": {
                    "name": tool_name,
                    "arguments": {"q": query, "i": state.step_index},
                },
                "done": False,
                "final_answer": None,
            }
        return {
            "reasoning": "done",
            "tool_call": None,
            "done": True,
            "final_answer": final,
        }

    return live_llm


def _make_tools(spec: dict[str, Any]) -> dict[str, Callable[..., Any]]:
    tool_name = str(spec["tool"])

    def tool_impl(q: str, i: int) -> str:
        return f"{tool_name}:{q}:{i}"

    return {tool_name: tool_impl}


def _prompt_hash_for(spec: dict[str, Any]) -> Callable[[ReactState], str]:
    """Synthetic prompt fingerprint that depends on the scenario + state."""
    query = str(spec["query"])

    def fn(state: ReactState) -> str:
        material = f"{query}|{state.step_index}|{len(state.trajectory)}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    return fn


async def _record_one(
    spec: dict[str, Any],
) -> tuple[ReactStepReplayCassette, list[dict[str, Any]], ReactState]:
    """Record-pass: drive ReactSkill, capture cassette + event stream."""
    cassette = ReactStepReplayCassette()
    driver = _SweepDriver(cassette, replay=False)
    skill = ReactSkill(
        name="react",
        version="0.1.0",
        description=f"sweep-record-{spec['query']}",
        llm_stub=driver.make_llm_stub(_make_live_llm(spec), _prompt_hash_for(spec)),
        tool_impls=_make_tools(spec),
        max_steps=10,
    )
    out = await skill.run(ReactState())
    return cassette, list(driver.events), out


async def _replay_one(
    spec: dict[str, Any],
    cassette: ReactStepReplayCassette,
    *,
    prompt_hash_fn: Callable[[ReactState], str] | None = None,
) -> tuple[list[dict[str, Any]], ReactState]:
    """Replay-pass: must_stub policy; live LLM here would be a violation."""
    driver = _SweepDriver(cassette, replay=True)

    def forbidden(_state: ReactState, _ctx: Any) -> dict[str, Any]:
        msg = "live LLM called during replay -- must_stub violated"
        raise AssertionError(msg)

    skill = ReactSkill(
        name="react",
        version="0.1.0",
        description=f"sweep-replay-{spec['query']}",
        llm_stub=driver.make_llm_stub(forbidden, prompt_hash_fn or _prompt_hash_for(spec)),
        tool_impls=_make_tools(spec),
        max_steps=10,
    )
    out = await skill.run(ReactState())
    return list(driver.events), out


# --------------------------------------------------------------------------- #
# Contract 1: sweep determinism -- byte-identical replay across 10 inputs     #
# --------------------------------------------------------------------------- #


async def test_replay_byte_identical_across_sweep() -> None:
    """Each of the ten distinct ReAct runs replays byte-identical."""
    assert len(_SCENARIOS) == 10  # FR-35 sweep contract

    for spec in _SCENARIOS:
        cassette, record_events, record_out = await _record_one(spec)
        replay_events, replay_out = await _replay_one(spec, cassette)

        # Per-step output dicts hash byte-identical under canonical JSON.
        assert _canonical(record_events) == _canonical(replay_events), (
            f"event stream drift on scenario {spec['query']!r}"
        )
        # Final state hashes byte-identical on the modeled fields.
        assert record_out.final_answer == replay_out.final_answer
        assert record_out.done == replay_out.done is True
        assert len(record_out.trajectory) == len(replay_out.trajectory)
        # The cassette holds exactly intermediate+1 records (one per think).
        expected_steps = int(spec["intermediate"]) + 1
        assert len(cassette.to_state()) == expected_steps


# --------------------------------------------------------------------------- #
# Contract 2: mutated input_hash on one cassette in the sweep -> ReplayError  #
# --------------------------------------------------------------------------- #


async def test_mutated_input_hash_in_sweep_loud_fails() -> None:
    """Tampering with one cassette's input_hash trips ReplayError on replay."""
    spec = _SCENARIOS[3]  # arbitrary non-degenerate scenario in the sweep
    cassette, _events, _out = await _record_one(spec)

    # Mutate input_hash on step 0 of the cassette.
    rec0 = cassette.get("react_loop", 0)
    assert rec0 is not None
    cassette.record(rec0.model_copy(update={"input_hash": "0" * 64}))

    with pytest.raises(ReplayError) as excinfo:
        await _replay_one(spec, cassette)
    assert "input_hash mismatch" in excinfo.value.message
    assert excinfo.value.context["node_name"] == "react_loop"
    assert excinfo.value.context["step_id"] == 0


# --------------------------------------------------------------------------- #
# Contract 3: prompt_hash drift surfaces as an input_hash mismatch            #
# --------------------------------------------------------------------------- #


async def test_prompt_hash_drift_detected_via_input_hash() -> None:
    """A drifted prompt fingerprint trips the input_hash check on replay.

    The per-step ``input_payload`` carries ``prompt_hash`` (see
    :func:`_step_payload`), so a replay that derives a different
    fingerprint than the record pass computes a different
    ``input_hash`` and the cassette loud-fails.
    """
    spec = _SCENARIOS[5]  # "primes" scenario, three intermediate steps
    cassette, _events, _out = await _record_one(spec)

    # Replay with a prompt-hash function that drifts on step 0 (only).
    original_fn = _prompt_hash_for(spec)

    def drifted(state: ReactState) -> str:
        if state.step_index == 0:
            return "deadbeef" * 2  # 16-char drifted fingerprint
        return original_fn(state)

    with pytest.raises(ReplayError) as excinfo:
        await _replay_one(spec, cassette, prompt_hash_fn=drifted)
    assert "input_hash mismatch" in excinfo.value.message
    assert excinfo.value.context["step_id"] == 0
    # The recorded vs actual hashes must both surface in the error context
    # so an operator can diagnose the drift without re-instrumenting.
    assert excinfo.value.context["expected_hash"] != excinfo.value.context["actual_hash"]
