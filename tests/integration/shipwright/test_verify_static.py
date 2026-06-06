# SPDX-License-Identifier: Apache-2.0
"""VerifyStatic — Python syntax + ruff + harbor graph verify on artifact_files."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from harbor.skills.shipwright.nodes.verify import VerifyStatic
from harbor.skills.shipwright.state import State

if TYPE_CHECKING:
    from pathlib import Path

    from harbor.nodes.base import ExecutionContext


VALID_STATE_PY = """\
from pydantic import BaseModel
class State(BaseModel):
    pass
"""

VALID_HARBOR_YAML = """\
name: x
state: ./state.py:State
nodes:
  - name: a
    type: dspy:Predict
"""

INVALID_STATE_PY = "this is not python\n"


@pytest.mark.integration
async def test_static_pass_on_valid_files(tmp_path: Path) -> None:
    state = State(
        artifact_files={
            "state.py": VALID_STATE_PY,
            "harbor.yaml": VALID_HARBOR_YAML,
        }
    )
    out = await VerifyStatic(work_dir=tmp_path).execute(
        state, cast("ExecutionContext", SimpleNamespace(run_id="r-test"))
    )
    statics = [r for r in out["verifier_results"] if r.kind == "static"]
    assert len(statics) == 1
    assert statics[0].passed is True


@pytest.mark.integration
async def test_static_fail_on_syntax_error(tmp_path: Path) -> None:
    state = State(
        artifact_files={
            "state.py": INVALID_STATE_PY,
            "harbor.yaml": VALID_HARBOR_YAML,
        }
    )
    out = await VerifyStatic(work_dir=tmp_path).execute(
        state, cast("ExecutionContext", SimpleNamespace(run_id="r-test"))
    )
    statics = [r for r in out["verifier_results"] if r.kind == "static"]
    assert len(statics) == 1
    assert statics[0].passed is False
    assert any("syntax" in (f.get("msg") or "").lower() for f in statics[0].findings)
