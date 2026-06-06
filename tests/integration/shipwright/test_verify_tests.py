# SPDX-License-Identifier: Apache-2.0
"""VerifyTests — runs pytest against synthesized tests/, captures output."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from stargraph.skills.shipwright.nodes.verify import VerifyTests
from stargraph.skills.shipwright.state import State

if TYPE_CHECKING:
    from pathlib import Path

    from stargraph.nodes.base import ExecutionContext


PASSING_STATE_PY = """\
from pydantic import BaseModel
class State(BaseModel):
    pass
"""

PASSING_TEST = """\
def test_state_loads():
    from state import State
    assert State() is not None
"""

FAILING_TEST = """\
def test_intentional_fail():
    assert 1 == 2
"""


@pytest.mark.integration
async def test_verify_tests_passes(tmp_path: Path) -> None:
    state = State(
        artifact_files={
            "state.py": PASSING_STATE_PY,
            "tests/test_smoke.py": PASSING_TEST,
            "tests/__init__.py": "",
        }
    )
    out = await VerifyTests(work_dir=tmp_path).execute(
        state, cast("ExecutionContext", SimpleNamespace(run_id="r-test"))
    )
    tests = [r for r in out["verifier_results"] if r.kind == "tests"]
    assert len(tests) == 1
    assert tests[0].passed is True


@pytest.mark.integration
async def test_verify_tests_fails(tmp_path: Path) -> None:
    state = State(
        artifact_files={
            "state.py": PASSING_STATE_PY,
            "tests/test_smoke.py": FAILING_TEST,
            "tests/__init__.py": "",
        }
    )
    out = await VerifyTests(work_dir=tmp_path).execute(
        state, cast("ExecutionContext", SimpleNamespace(run_id="r-test"))
    )
    tests = [r for r in out["verifier_results"] if r.kind == "tests"]
    assert len(tests) == 1
    assert tests[0].passed is False
    assert tests[0].findings, "expected pytest output captured in findings"
