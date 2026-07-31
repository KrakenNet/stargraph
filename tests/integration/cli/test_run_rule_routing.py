# SPDX-License-Identifier: Apache-2.0
"""``stargraph run`` routes live on IR rules (``build_ir_routing`` wiring).

The cyclic fixture's ``work`` node must run twice (its Mirror-annotated
``phase_verdict`` stays ``"refine"`` until round 2) before the rules route
to ``finish`` and halt. The pre-wiring linear driver ran each node exactly
once in declaration order, so ``rounds: 2`` in the final state is the
live-routing proof.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from stargraph.cli import app

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.mark.integration
@pytest.mark.parametrize(
    "fixture",
    ["cyclic-graph.yaml", "cyclic-graph-sugar.yaml"],  # raw CLIPS vs when-sugar twin
)
def test_cyclic_rules_fire_live(tmp_path: Path, fixture: str) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run",
            str(_FIXTURES / fixture),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "status=done" in result.output
    assert "rounds: 2" in result.output
    assert "finished after 2 rounds" in result.output
