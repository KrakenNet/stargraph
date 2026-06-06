# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import shutil
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from harbor.skills.shipwright.nodes.verify import VerifySmoke
from harbor.skills.shipwright.state import State

if TYPE_CHECKING:
    from pathlib import Path

    from harbor.nodes.base import ExecutionContext


pytestmark = pytest.mark.skipif(
    shutil.which("harbor") is None, reason="harbor CLI not installed in test env"
)


SMOKE_IR_YAML = """\
ir_version: "1.0.0"
id: "run:smoke"
nodes:
  - id: noop
    kind: echo
"""

SMOKE_FIXTURES_OK = "noop: ok\n"
SMOKE_FIXTURES_EMPTY = "{}\n"


@pytest.mark.integration
async def test_smoke_runs_on_minimal_graph(tmp_path: Path) -> None:
    state = State(artifact_files={"harbor.yaml": SMOKE_IR_YAML, "fixtures.yaml": SMOKE_FIXTURES_OK})
    out = await VerifySmoke(work_dir=tmp_path).execute(
        state, cast("ExecutionContext", SimpleNamespace(run_id="r-test"))
    )
    [r] = [r for r in out["verifier_results"] if r.kind == "smoke"]
    assert r.passed is True


@pytest.mark.integration
async def test_smoke_fails_on_missing_fixture(tmp_path: Path) -> None:
    state = State(
        artifact_files={"harbor.yaml": SMOKE_IR_YAML, "fixtures.yaml": SMOKE_FIXTURES_EMPTY}
    )
    out = await VerifySmoke(work_dir=tmp_path).execute(
        state, cast("ExecutionContext", SimpleNamespace(run_id="r-test"))
    )
    [r] = [r for r in out["verifier_results"] if r.kind == "smoke"]
    assert r.passed is False
