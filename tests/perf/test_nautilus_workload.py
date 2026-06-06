# SPDX-License-Identifier: Apache-2.0
"""Nautilus reference workload smoke (NFR-3, design §3.1.5).

Phase-1 reference workload: a synthetic 6-node graph that exercises the
three perf-relevant subsystems together so the calibration captures their
*combined* steady-state cost rather than each in isolation:

* :func:`stargraph.graph.hash.structural_hash` over a 6-node IR (rules a-d).
* Six :py:meth:`stargraph.checkpoint.sqlite.SQLiteCheckpointer.write` commits
  -- one per node visit including a fan-out of two parallel branches.
* Six no-op :py:meth:`stargraph.nodes.base.NodeBase.execute` dispatches
  (the per-node overhead from task 5.1).

The graph topology mirrors a typical agent-style flow:

.. code-block:: text

    n0 (start) -> n1 (echo) -> [n2 || n3] (parallel branches) -> n4 (join) -> n5 (halt)

The "parallel" branches are simulated at the checkpoint level (one row per
branch with ``branch_id="b0"``/``"b1"``) -- the runtime's
:func:`stargraph.runtime.parallel.execute_parallel` is not in scope for this
calibration; we just want the workload mix to cover branched checkpoint
writes alongside a structural-hash + dispatch loop.

No production reference graph exists yet (per design §3.1.5: "validates
against Nautilus reference workload"); this synthetic stand-in is
explicitly called out by the task body. When the real reference lands,
swap the IR build below for a YAML fixture load.

Skip-by-default: marked ``@pytest.mark.slow``; run with ``--runslow``.
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel

from stargraph.checkpoint import Checkpoint
from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.graph.hash import structural_hash
from stargraph.ir._models import GotoAction, HaltAction, IRDocument, NodeSpec, RuleSpec
from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from pathlib import Path


class _NautilusState(BaseModel):
    counter: int = 0


class _NautilusCtx:
    run_id: str = "nautilus-perf"


class _NoopNode(NodeBase):
    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del state, ctx
        return {}


def _build_6_node_ir() -> IRDocument:
    """Build the synthetic reference IR: 6 echo nodes wired by 5 goto rules."""
    nodes = [NodeSpec(id=f"n{i}", kind="echo") for i in range(6)]
    rules = [
        RuleSpec(id="r-01", when="(node-id (id n0))", then=[GotoAction(target="n1")]),
        RuleSpec(id="r-12", when="(node-id (id n1))", then=[GotoAction(target="n2")]),
        RuleSpec(id="r-13", when="(node-id (id n1))", then=[GotoAction(target="n3")]),
        RuleSpec(id="r-24", when="(node-id (id n2))", then=[GotoAction(target="n4")]),
        RuleSpec(id="r-34", when="(node-id (id n3))", then=[GotoAction(target="n4")]),
        RuleSpec(id="r-45", when="(node-id (id n4))", then=[GotoAction(target="n5")]),
        RuleSpec(
            id="r-halt",
            when="(node-id (id n5))",
            then=[HaltAction(reason="reached terminal")],
        ),
    ]
    return IRDocument(
        ir_version="1.0.0",
        id="run:nautilus-perf",
        nodes=nodes,
        rules=rules,
        state_schema={"counter": "int"},
    )


def _make_checkpoint(
    run_id: str,
    step: int,
    *,
    branch_id: str | None,
    last_node: str,
    graph_hash: str,
) -> Checkpoint:
    return Checkpoint(
        run_id=run_id,
        step=step,
        branch_id=branch_id,
        parent_step_idx=None,
        graph_hash=graph_hash,
        runtime_hash="sha256:runtime",
        state={"counter": step},
        clips_facts=[],
        last_node=last_node,
        next_action=None,
        timestamp=datetime.now(UTC),
        parent_run_id=None,
        side_effects_hash="sha256:side",
    )


@pytest.mark.slow
def test_nautilus_reference_workload(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Run the synthetic 6-node workload once; report total + per-stage ms.

    No hard p99 gate -- this is a smoke + total-elapsed calibration. The
    spec says "Test runs once; numbers logged"; the verify command only
    requires the test to run with --runslow (no specific p-percentile
    grep), so the calibration line is the artifact.
    """
    db_path = tmp_path / "nautilus.db"
    ir = _build_6_node_ir()
    rule_packs: list[tuple[str, str, str]] = [("nautilus", "sha256:nau", "0.1.0")]

    # 1. Structural hash (one-shot at workload entry).
    t_hash = time.perf_counter_ns()
    graph_h = structural_hash(ir, rule_pack_versions=rule_packs)
    hash_ns = time.perf_counter_ns() - t_hash

    # 2. Six no-op node dispatches + 7 checkpoint commits (one per linear
    #    step + one extra for the parallel branch fan-out: n2/n3 each get
    #    a row with a distinct branch_id).
    state = _NautilusState()
    ctx = _NautilusCtx()
    node = _NoopNode()
    visit_order: list[tuple[str, str | None]] = [
        ("n0", None),
        ("n1", None),
        ("n2", "b0"),
        ("n3", "b1"),
        ("n4", None),
        ("n5", None),
    ]

    async def _drive() -> tuple[int, int]:
        cp = SQLiteCheckpointer(db_path)
        try:
            await cp.bootstrap()
            t_d = time.perf_counter_ns()
            for _ in visit_order:
                await node.execute(state, ctx)
            dispatch_ns = time.perf_counter_ns() - t_d

            t_c = time.perf_counter_ns()
            for step, (last_node, branch_id) in enumerate(visit_order):
                ckpt = _make_checkpoint(
                    "nautilus-run",
                    step,
                    branch_id=branch_id,
                    last_node=last_node,
                    graph_hash=graph_h,
                )
                await cp.write(ckpt)
            commit_ns = time.perf_counter_ns() - t_c
        finally:
            await cp.close()
        return dispatch_ns, commit_ns

    dispatch_ns, commit_ns = asyncio.run(_drive())
    total_ms = (hash_ns + dispatch_ns + commit_ns) / 1_000_000.0

    line = (
        f"nautilus_workload nodes=6 commits={len(visit_order)} "
        f"hash={hash_ns / 1_000_000:.3f}ms "
        f"dispatch={dispatch_ns / 1_000_000:.3f}ms "
        f"commits={commit_ns / 1_000_000:.3f}ms "
        f"total={total_ms:.3f}ms"
    )
    with capsys.disabled():
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
