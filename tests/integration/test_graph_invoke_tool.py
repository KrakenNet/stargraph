# SPDX-License-Identifier: Apache-2.0
"""``std.graph_invoke`` -- run a child graph through the real engine.

The gate test runs through :func:`execute_tool` (default-deny with no
grant); execution tests call the tool directly and drive both YAML
front doors -- authoring-format (compiled transparently, value routes
loop live) and full IR (``examples/hello.yaml``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from stargraph.errors import CapabilityError, StargraphRuntimeError
from stargraph.runtime.tool_exec import RunContext, execute_tool
from stargraph.tools.std.graph_invoke import graph_invoke

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]

_AUTHORED_LOOP = """\
id: invoked-loop
state:
  rounds: int
  message: str
  verdict: {type: str, route: true}
nodes:
  work: {kind: "tests.fixtures.authored_nodes:WorkNode"}
  finish: {kind: "tests.fixtures.authored_nodes:FinishNode"}
routes:
  work: {refine: work, sufficient: finish}
  finish: done
"""


async def test_graph_invoke_denied_by_default() -> None:
    ctx = RunContext(run_id="r1")  # no capabilities wired
    with pytest.raises(CapabilityError):
        await execute_tool(cast("Any", graph_invoke), {"graph": "x.yaml"}, run_ctx=ctx)


async def test_runs_authoring_format_child_to_done(tmp_path: Path) -> None:
    child = tmp_path / "loop.yaml"
    child.write_text(_AUTHORED_LOOP, encoding="utf-8")

    result = await graph_invoke(str(child))

    assert result["status"] == "done"
    assert result["final_state"]["rounds"] == 2
    assert result["final_state"]["message"] == "finished after 2 rounds"
    assert result["run_id"]
    assert result["graph_hash"]


async def test_runs_full_ir_child_to_done() -> None:
    result = await graph_invoke(
        str(_REPO_ROOT / "examples" / "hello.yaml"), inputs={"message": "hello"}
    )

    assert result["status"] == "done"
    assert result["final_state"]["message"] == "hello"


async def test_missing_graph_file_raises() -> None:
    with pytest.raises(StargraphRuntimeError, match="not found"):
        await graph_invoke("no-such-graph.yaml")
