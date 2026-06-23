# SPDX-License-Identifier: Apache-2.0
"""Verify — actually RUN the assembled graph to a terminal ``done`` state.

The un-cheatable floor for the whole build: reuse the same run contract every
graph-running smith uses (:data:`stargraph.skills._smith.gate.RUN_GRAPH_PRELUDE` —
load the IR into a real ``Graph``, build the node registry, run to a terminal
``ResultEvent``), driven in a subprocess over a clean copy of the assembled graph
(so the deliverable dir is never polluted, and generic ``state`` / ``nodes`` module
names can't collide with the foundry's own process). The spine is run against the
fixture inputs captured when its smith built it.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from stargraph.nodes.base import NodeBase
from stargraph.skills._smith.gate import RUN_GRAPH_PRELUDE, run_driver

if TYPE_CHECKING:
    from pydantic import BaseModel

    from stargraph.nodes.base import ExecutionContext

__all__ = ["Verify"]

# The prelude leaves ``ev`` (the terminal ResultEvent) in scope; report its status.
_VERIFY_DRIVER = RUN_GRAPH_PRELUDE + 'print(json.dumps({"ok": True, "status": ev.status}))\n'


def _run_assembled(graph_path: Path, fixture: dict[str, Any]) -> tuple[str, str]:
    """Run the assembled graph in a clean subprocess; return ``(status, detail)``."""
    with tempfile.TemporaryDirectory(prefix="foundry-verify-") as d:
        work = Path(d)
        for f in graph_path.parent.iterdir():  # graph.yaml + state.py + nodes.py only
            if f.is_file():
                shutil.copy2(f, work / f.name)
        try:
            _, verdict, out = run_driver(
                work,
                driver_src=_VERIFY_DRIVER,
                driver_name="_verify_driver.py",
                payload={"fixture": fixture, "meta": {"run_id": "foundry-verify", "noun": "graph"}},
                payload_name="contract.json",
                timeout_s=60,
            )
        except subprocess.TimeoutExpired:
            return "timeout", "assembled graph run timed out after 60s"
    if verdict and verdict.get("ok"):
        return str(verdict.get("status", "done")), ""
    return "failed", str((verdict or {}).get("msg") or out[-600:])


class Verify(NodeBase):
    """Run the assembled spine to ``done``; set ``run_status`` / ``verified``."""

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        graph_path = Path(str(getattr(state, "graph_path", "") or ""))
        if not await asyncio.to_thread(graph_path.is_file):
            return {
                "run_status": "no-graph",
                "verified": False,
                "verify_detail": f"no assembled graph at {graph_path}",
            }
        built: list[dict[str, Any]] = getattr(state, "built", []) or []
        spine = next((b for b in built if b.get("kind") == "graph"), cast("dict[str, Any]", {}))
        fixture = dict(cast("dict[str, Any]", spine.get("fixture", {}) or {}))
        status, detail = await asyncio.to_thread(_run_assembled, graph_path, fixture)
        return {"run_status": status, "verified": status == "done", "verify_detail": detail}
