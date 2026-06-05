# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from harbor.skills.shipwright.nodes.triage import TriageGate
from harbor.skills.shipwright.state import State

if TYPE_CHECKING:
    from harbor.nodes.base import ExecutionContext


@pytest.mark.integration
async def test_triage_passes_through_existing_mode() -> None:
    state = State(mode="new", brief="a triage graph")
    out = await TriageGate().execute(
        state, cast("ExecutionContext", SimpleNamespace(run_id="r-test"))
    )
    assert out["mode"] == "new"


@pytest.mark.integration
async def test_triage_rejects_fix_mode_without_target_path() -> None:
    state = State(mode="fix", brief=None, target_path=None)
    with pytest.raises(ValueError, match="target_path is required"):
        await TriageGate().execute(
            state, cast("ExecutionContext", SimpleNamespace(run_id="r-test"))
        )


@pytest.mark.integration
async def test_triage_rejects_new_mode_without_brief() -> None:
    state = State(mode="new", brief=None)
    with pytest.raises(ValueError, match="brief is required"):
        await TriageGate().execute(
            state, cast("ExecutionContext", SimpleNamespace(run_id="r-test"))
        )
