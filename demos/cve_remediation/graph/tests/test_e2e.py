# SPDX-License-Identifier: Apache-2.0
"""End-to-end smoke test for the cve_remediation demo.

Runs each IR through ``harbor run --inspect`` (Graph.simulate), asserts
the graph hash is stable + all rules fire. Phase E swaps inspect-mode
for live execution once real node bodies + signed packs land.
"""

from __future__ import annotations

import pytest

from demos.cve_remediation.run_demo import IR_FILES, run_inspect


@pytest.mark.parametrize("ir_path", IR_FILES, ids=lambda p: p.name)
def test_ir_inspect_succeeds(ir_path) -> None:  # noqa: ANN001
    rec = run_inspect(ir_path)
    assert rec.exit_code == 0, f"{ir_path.name} exited {rec.exit_code}"
    assert rec.graph_hash, f"{ir_path.name} produced no graph hash"
    assert len(rec.graph_hash) == 64, f"{ir_path.name} hash wrong length"
    assert rec.rule_firings > 0, f"{ir_path.name} fired no rules"


def test_demo_runner_exit_code_zero() -> None:
    """The whole demo runner script returns 0 on a clean tree."""
    from demos.cve_remediation.run_demo import main

    rc = main([])
    assert rc == 0
