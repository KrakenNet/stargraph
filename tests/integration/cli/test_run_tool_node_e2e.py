# SPDX-License-Identifier: Apache-2.0
"""``stargraph run`` drives ``kind: tool`` through the real pipeline.

The Phase-0 verification item deferred to P1a: a plain YAML IR with a
configured ``kind: tool`` node, run through the CLI, resolves
``std.calculator@1`` from the seeded builtin registry and executes it via
:func:`stargraph.runtime.tool_exec.execute_tool` (fact emission + step
provenance covered at unit level in ``test_tool_call_node.py``). The
``14`` in the final state proves the tool body actually computed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from stargraph.cli import app

TOOL_GRAPH = Path(__file__).resolve().parents[2] / "fixtures" / "std-tool-graph.yaml"


@pytest.mark.integration
def test_kind_tool_runs_std_calculator_via_cli(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run",
            str(TOOL_GRAPH),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "status=done" in result.output
    assert "14" in result.output  # 2 + 3 * 4, computed by std.calculator
