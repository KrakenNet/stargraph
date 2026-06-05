# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from harbor.skills.shipwright.nodes.fix import FixLoop
from harbor.skills.shipwright.state import State, VerifierResult

if TYPE_CHECKING:
    from harbor.nodes.base import ExecutionContext


@pytest.mark.integration
async def test_fix_loop_increments_attempts_and_targets_synth() -> None:
    state = State(
        verifier_results=[VerifierResult(kind="static", passed=False, findings=[{"msg": "x"}])],
        fix_attempts=0,
    )
    out = await FixLoop().execute(state, cast("ExecutionContext", SimpleNamespace(run_id="r-test")))
    assert out["fix_attempts"] == 1
    assert out["next_node"] == "synthesize_graph"


@pytest.mark.integration
async def test_fix_loop_escalates_on_third_failure() -> None:
    state = State(
        verifier_results=[VerifierResult(kind="static", passed=False, findings=[{"msg": "x"}])],
        fix_attempts=3,
    )
    out = await FixLoop().execute(state, cast("ExecutionContext", SimpleNamespace(run_id="r-test")))
    assert out["next_node"] == "human_input"


@pytest.mark.integration
async def test_fix_loop_advances_when_all_pass() -> None:
    state = State(
        verifier_results=[
            VerifierResult(kind="static", passed=True),
            VerifierResult(kind="tests", passed=True),
            VerifierResult(kind="smoke", passed=True),
        ],
        fix_attempts=1,
    )
    out = await FixLoop().execute(state, cast("ExecutionContext", SimpleNamespace(run_id="r-test")))
    assert out["next_node"] == "landing_summary"
