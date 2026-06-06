# SPDX-License-Identifier: Apache-2.0
"""``POST /v1/runs`` drives a real :class:`GraphRun` end-to-end.

Resolves TODO #14 (Findings from docs build, 2026-05-04): the route
no longer returns the synthetic ``poc-{graph_id}`` id and the wired
:class:`Scheduler` actually loads the requested :class:`Graph` from
``deps["graphs"]``, builds a real :class:`GraphRun`, registers the
handle in ``deps["runs"]`` (and an :class:`EventBroadcaster` in
``deps["broadcasters"]``), and drives it through
:meth:`GraphRun.start` to terminal state.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import anyio
import httpx
import pytest

from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.graph import Graph
from stargraph.ir import IRDocument, NodeSpec
from stargraph.nodes.base import NodeBase
from stargraph.serve.api import create_app
from stargraph.serve.profiles import OssDefaultProfile
from stargraph.serve.scheduler import Scheduler

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.serve, pytest.mark.api]


class _NoOpNode(NodeBase):
    """No-op node — returns an empty state patch (one tick passes)."""

    async def execute(self, state: Any, ctx: Any) -> dict[str, Any]:
        del state, ctx
        return {}


@pytest.mark.asyncio
async def test_post_runs_returns_canonical_id_and_drives_real_graph(
    tmp_path: Path,
) -> None:
    """End-to-end: POST returns Scheduler-derived id; dispatcher actually runs."""
    checkpointer = SQLiteCheckpointer(tmp_path / "ckpt.sqlite")
    await checkpointer.bootstrap()

    ir = IRDocument(
        ir_version="1.0.0",
        id="run:post-runs-real",
        nodes=[NodeSpec(id="alpha", kind="noop"), NodeSpec(id="beta", kind="noop")],
        state_schema={"counter": "int"},
    )
    graph = Graph(ir)

    scheduler = Scheduler()
    deps: dict[str, Any] = {
        "scheduler": scheduler,
        "runs": {},
        "broadcasters": {},
        "graphs": {ir.id: graph},
        "node_registry": {ir.id: {"alpha": _NoOpNode(), "beta": _NoOpNode()}},
        "checkpointer": checkpointer,
    }
    scheduler.set_deps(deps)
    await scheduler.start()
    try:
        app = create_app(OssDefaultProfile(), deps=deps)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/runs",
                json={"graph_id": ir.id, "params": {"counter": 0}},
            )
            assert resp.status_code == 202, resp.text
            body = resp.json()
            run_id = body["run_id"]
            # Canonical Scheduler-derived id, not the legacy stub.
            assert isinstance(run_id, str) and run_id
            assert not run_id.startswith("poc-"), (
                f"POST /v1/runs returned legacy poc-* stub: {run_id!r}"
            )
            assert body["status"] == "pending"

            # Wait for the dispatcher to register the run handle.
            with anyio.fail_after(2.0):
                while run_id not in deps["runs"]:  # noqa: ASYNC110 -- polling external run state, no event available
                    await asyncio.sleep(0.02)

            # Wait for the run to terminate.
            with anyio.fail_after(5.0):
                run = deps["runs"][run_id]
                while run.state not in {"done", "failed", "cancelled"}:  # noqa: ASYNC110 -- polling external run state, no event available
                    await asyncio.sleep(0.02)
            assert run.state == "done", f"run terminated in unexpected state: {run.state!r}"

            # Broadcaster registered for the WS stream route.
            assert run_id in deps["broadcasters"]

            # GET /v1/runs/{id} resolves to the same handle.
            get_resp = await client.get(f"/v1/runs/{run_id}")
            assert get_resp.status_code == 200, get_resp.text
            assert get_resp.json()["run_id"] == run_id
    finally:
        await scheduler.stop()
        await checkpointer.close()
