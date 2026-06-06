# SPDX-License-Identifier: Apache-2.0
"""ReAct replay refuses live LLM calls (FR-35, AC-10.2, NFR-4).

The must_stub policy: a replayed ReAct loop MUST consume per-step records
from :class:`stargraph.replay.react_cassette.ReactStepReplayCassette`. A miss
(no recorded entry for ``(node_name, step_id)``) is a loud failure --
:class:`stargraph.errors.ReplayError`, never a fall-through to a live LLM
call. This pins the contract directly on the cassette surface so the
guarantee is a structural property of the replay layer, not a policy
the loop driver has to remember to enforce.
"""

from __future__ import annotations

import pytest

from stargraph.errors import ReplayError
from stargraph.replay.react_cassette import (
    ReactStepRecord,
    ReactStepReplayCassette,
    input_hash,
)


def _payload(step_id: int, traj_len: int) -> dict[str, int]:
    return {"step_index": step_id, "trajectory_len": traj_len}


def test_replay_miss_raises_replay_error() -> None:
    """No recorded entry for ``(node, step)`` -> :class:`ReplayError`.

    The must_stub policy: replay MUST NOT fall through to a live LLM.
    Any cache miss surfaces as a loud structured error.
    """
    cassette = ReactStepReplayCassette()
    with pytest.raises(ReplayError) as excinfo:
        cassette.replay(
            node_name="react_loop",
            step_id=0,
            input_payload=_payload(0, 0),
        )
    assert "no recorded ReAct step" in excinfo.value.message
    assert excinfo.value.context["node_name"] == "react_loop"
    assert excinfo.value.context["step_id"] == 0


def test_replay_input_hash_mismatch_raises_replay_error() -> None:
    """Recorded ``input_hash`` differs from replay-side -> :class:`ReplayError`.

    Mirrors the AC-10.2 contract: a tampered cassette (or a divergent
    replay-time input) loud-fails instead of silently returning the
    recorded output. ``expected_hash`` and ``actual_hash`` are both
    populated on the structured error so operators can diff.
    """
    cassette = ReactStepReplayCassette()
    cassette.record(
        ReactStepRecord(
            step_id=0,
            node_name="react_loop",
            input_hash=input_hash(_payload(0, 0)),
            output={"reasoning": "x", "tool_call": None, "done": True, "final_answer": "x"},
            model_id="test-model",
            prompt_hash="ph",
            tool_name=None,
            tool_args_hash=None,
            wall_clock_ts=1.0,
        )
    )

    # Replay-side payload diverges from the recorded payload.
    with pytest.raises(ReplayError) as excinfo:
        cassette.replay(
            node_name="react_loop",
            step_id=0,
            input_payload=_payload(0, 99),  # trajectory_len mismatch
        )
    assert "input_hash mismatch" in excinfo.value.message
    assert excinfo.value.context["expected_hash"] == input_hash(_payload(0, 0))
    assert excinfo.value.context["actual_hash"] == input_hash(_payload(0, 99))


def test_replay_match_returns_recorded_output() -> None:
    """Matching ``(node, step, input_hash)`` returns the recorded record.

    The complement of the must_stub contract: when the cassette has the
    expected entry AND the input_hash matches, replay returns the
    recorded output verbatim. No live LLM call happens because the
    cassette is the only path to the output dict.
    """
    cassette = ReactStepReplayCassette()
    expected_output = {
        "reasoning": "stubbed",
        "tool_call": None,
        "done": True,
        "final_answer": "answer-from-cassette",
    }
    cassette.record(
        ReactStepRecord(
            step_id=0,
            node_name="react_loop",
            input_hash=input_hash(_payload(0, 0)),
            output=expected_output,
            model_id="test-model",
            prompt_hash="ph",
            tool_name=None,
            tool_args_hash=None,
            wall_clock_ts=1.0,
        )
    )
    rec = cassette.replay(
        node_name="react_loop",
        step_id=0,
        input_payload=_payload(0, 0),
    )
    assert rec.output == expected_output
    assert rec.model_id == "test-model"
