# SPDX-License-Identifier: Apache-2.0
"""ReactSkill termination-rule integration tests (FR-25, AC-7.5, AC-10.4).

Three independent termination paths that the loop body in
:meth:`stargraph.skills.react.ReactSkill.run` honors in priority order:

1. ``test_max_steps_termination`` -- llm_stub never sets ``done``;
   the loop terminates exactly at ``max_steps`` iterations.
2. ``test_done_flag_termination`` -- llm_stub sets ``done=True`` on the
   second think step; loop exits before reaching ``max_steps``.
3. ``test_error_budget_termination`` -- every tool call raises; the
   ``error_budget`` field decrements per failure and the loop exits
   once it reaches zero (default budget = 3).
"""

from __future__ import annotations

from typing import Any

import pytest

from stargraph.skills.react import ReactSkill, ReactState

pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


async def test_max_steps_termination() -> None:
    """llm_stub never returns done; loop terminates at max_steps."""

    def llm_stub(_state: ReactState, _ctx: Any) -> dict[str, Any]:
        return {
            "reasoning": "still thinking",
            "tool_call": None,
            "done": False,
            "final_answer": None,
        }

    skill = ReactSkill(
        name="react",
        version="0.1.0",
        description="max-steps termination test",
        llm_stub=llm_stub,
        tool_impls={},
        max_steps=4,
    )

    out = await skill.run(ReactState())

    assert out.done is False
    assert out.final_answer is None
    assert out.step_index == 4
    assert len(out.trajectory) == 4
    # No tool dispatches happened, so the dispatch log is empty.
    assert out.tool_calls == []
    # error_budget never decrements without tool failures.
    assert out.error_budget == 3


async def test_done_flag_termination() -> None:
    """llm_stub returns done=True on step 2; loop exits before max_steps."""
    iteration = {"n": 0}

    def llm_stub(_state: ReactState, _ctx: Any) -> dict[str, Any]:
        iteration["n"] += 1
        if iteration["n"] >= 2:
            return {
                "reasoning": "done now",
                "tool_call": None,
                "done": True,
                "final_answer": "result",
            }
        return {
            "reasoning": "still working",
            "tool_call": None,
            "done": False,
            "final_answer": None,
        }

    skill = ReactSkill(
        name="react",
        version="0.1.0",
        description="done-flag termination test",
        llm_stub=llm_stub,
        tool_impls={},
        max_steps=10,
    )

    out = await skill.run(ReactState())

    assert out.done is True
    assert out.final_answer == "result"
    assert out.step_index == 2
    assert len(out.trajectory) == 2


async def test_error_budget_termination() -> None:
    """Tool always raises; error_budget decrements; loop exits at zero."""

    def boom(**_kwargs: Any) -> Any:
        msg = "tool exploded"
        raise RuntimeError(msg)

    iteration = {"n": 0}

    def llm_stub(_state: ReactState, _ctx: Any) -> dict[str, Any]:
        iteration["n"] += 1
        return {
            "reasoning": f"try tool, attempt {iteration['n']}",
            "tool_call": {"name": "boom", "arguments": {}},
            "done": False,
            "final_answer": None,
        }

    skill = ReactSkill(
        name="react",
        version="0.1.0",
        description="error-budget termination test",
        llm_stub=llm_stub,
        tool_impls={"boom": boom},
        max_steps=99,  # large -- termination must come from error_budget
    )

    out = await skill.run(ReactState(error_budget=3))

    # Default error_budget is 3 -- exactly 3 failed tool calls then exit.
    assert out.error_budget == 0
    assert out.done is False
    assert out.step_index == 3
    assert len(out.tool_calls) == 3
    for record in out.tool_calls:
        assert record.error is not None
        assert "RuntimeError" in record.error
        assert "tool exploded" in record.error
