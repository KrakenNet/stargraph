# SPDX-License-Identifier: Apache-2.0
"""ReactSkill tool-loop subgraph integration tests (FR-25, AC-7.5, AC-10.4).

Pins the think -> act -> observe execution shape against an injected
LLM-shaped stub callable + an in-memory tool table:

* ``test_native_function_calling`` -- the loop dispatches a tool through
  the constructor-supplied ``tools`` dict using attribute access on the
  ``tool_call`` dict (NOT regex parsing). Asserts the tool actually
  fired with the structured ``arguments`` payload.
* ``test_think_act_observe_subgraph`` -- the three nodes fire in the
  declared order: think (emits tool_call) -> act (dispatch) -> observe
  (trajectory append) -> think (done=True) -> terminate. Asserts
  trajectory length, ordering invariants, and final_answer plumbing.
"""

from __future__ import annotations

from typing import Any

import pytest

from stargraph.skills.react import ReactSkill, ReactState

pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


async def test_native_function_calling() -> None:
    """tool_call is a dict; dispatch is attribute access, not regex."""
    captured_args: dict[str, Any] = {}

    def search_docs(query: str, k: int = 3) -> list[str]:
        captured_args["query"] = query
        captured_args["k"] = k
        return [f"hit:{query}:{i}" for i in range(k)]

    calls: list[int] = []

    def llm_stub(_state: ReactState, _ctx: Any) -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 1:
            return {
                "reasoning": "I should look up alice",
                "tool_call": {
                    "name": "search_docs",
                    "arguments": {"query": "alice", "k": 2},
                },
                "done": False,
                "final_answer": None,
            }
        return {
            "reasoning": "I have enough information",
            "tool_call": None,
            "done": True,
            "final_answer": "alice knows bob",
        }

    skill = ReactSkill(
        name="react",
        version="0.1.0",
        description="tool-loop native function-calling test",
        llm_stub=llm_stub,
        tool_impls={"search_docs": search_docs},
        max_steps=5,
    )

    out = await skill.run(ReactState())

    # Tool fired with the structured arguments dict (no regex parsing).
    assert captured_args == {"query": "alice", "k": 2}

    # The dispatch path recorded the call onto state.tool_calls.
    assert len(out.tool_calls) == 1
    record = out.tool_calls[0]
    assert record.name == "search_docs"
    assert record.arguments == {"query": "alice", "k": 2}
    assert record.error is None
    assert record.result == ["hit:alice:0", "hit:alice:1"]

    # Loop terminated cleanly via the done flag.
    assert out.done is True
    assert out.final_answer == "alice knows bob"


async def test_think_act_observe_subgraph() -> None:
    """think -> act -> observe -> think (done) -> terminate (in order)."""
    fired: list[str] = []

    def echo(value: str) -> str:
        fired.append(f"act:{value}")
        return value.upper()

    iteration = {"n": 0}

    def llm_stub(_state: ReactState, _ctx: Any) -> dict[str, Any]:
        iteration["n"] += 1
        fired.append(f"think:{iteration['n']}")
        if iteration["n"] == 1:
            return {
                "reasoning": "echo it",
                "tool_call": {"name": "echo", "arguments": {"value": "hello"}},
                "done": False,
                "final_answer": None,
            }
        return {
            "reasoning": "we are done",
            "tool_call": None,
            "done": True,
            "final_answer": "HELLO",
        }

    skill = ReactSkill(
        name="react",
        version="0.1.0",
        description="three-node subgraph order test",
        llm_stub=llm_stub,
        tool_impls={"echo": echo},
        max_steps=5,
    )

    out = await skill.run(ReactState())

    # Three-node subgraph ordering: think_1 -> act -> think_2 (no act).
    # _observe is implicit (state mutation between iterations).
    assert fired == ["think:1", "act:hello", "think:2"]

    # Trajectory carries both iterations: step 0 has tool_call, step 1 doesn't.
    assert len(out.trajectory) == 2
    step0, step1 = out.trajectory
    assert step0.thought == "echo it"
    assert step0.tool_call is not None
    assert step0.tool_call.name == "echo"
    assert step0.observation == "HELLO"
    assert step1.thought == "we are done"
    assert step1.tool_call is None
    assert step1.observation is None

    # tool_calls log is the dispatch-only projection (one entry).
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].name == "echo"

    # step_index increments per think -> act -> observe iteration.
    assert out.step_index == 2
    assert out.done is True
    assert out.final_answer == "HELLO"
