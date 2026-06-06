# SPDX-License-Identifier: Apache-2.0
"""Skill topology change refuses replay (FR-35, AC-10.3).

Mirrors :mod:`tests.integration.test_resume_hash_mismatch` at the *skill
subgraph* level: the engine's FR-20 hash-mismatch refusal mechanism is
reused for skill topology changes (design §3.7 + §3.9).

Scenario:

1. Build a v1 :class:`Graph` whose IR carries a skill subgraph (one
   :class:`SkillRef` + the matching ``kind="skill"`` :class:`NodeSpec`
   for the subgraph entry node) and capture its ``graph_hash``.
2. Persist a checkpoint pinned to the v1 ``graph_hash``.
3. Build a v2 :class:`Graph` with an extra node added inside the skill
   subgraph (topology change). The structural hash component covering
   ``IRDocument.nodes`` shifts -> ``graph_hash`` changes.
4. ``GraphRun.resume(cp, run_id, graph=v2)`` MUST raise
   :class:`CheckpointError` with ``context`` carrying ``expected_hash``
   / ``actual_hash`` / ``migrate_available`` -- the same structured
   refusal contract pinned by :mod:`test_resume_hash_mismatch` for the
   engine-level case.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from stargraph import Graph, GraphRun
from stargraph.checkpoint import Checkpoint
from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.errors import CheckpointError
from stargraph.ir import IRDocument, NodeSpec, SkillRef

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _make_checkpoint(*, run_id: str, graph_hash: str) -> Checkpoint:
    """Build a populated :class:`Checkpoint` pinned to ``graph_hash``."""
    return Checkpoint(
        run_id=run_id,
        step=0,
        branch_id=None,
        parent_step_idx=None,
        graph_hash=graph_hash,
        runtime_hash="sha256:runtime-skill-v1",
        state={"step_index": 0},
        clips_facts=[],
        last_node="react_entry",
        next_action=None,
        timestamp=datetime.now(UTC),
        parent_run_id=None,
        side_effects_hash="sha256:side-skill-v1",
    )


def _build_skill_graph_v1() -> Graph:
    """v1 IR: one skill subgraph entry node + matching :class:`SkillRef`."""
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:skill-topology-v1",
        nodes=[NodeSpec(id="react_entry", kind="skill")],
        skills=[SkillRef(id="react", version="0.1.0")],
    )
    return Graph(ir)


def _build_skill_graph_v2_added_node() -> Graph:
    """v2 IR: same skill ref, plus an additional node inside the subgraph.

    Adds a second ``kind="skill"`` node (``react_observe``) representing a
    topology change inside the skill's subgraph -- the structural-hash
    component covering ``IRDocument.nodes`` shifts, so ``graph_hash``
    differs from v1.
    """
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:skill-topology-v1",  # same id; only topology changes
        nodes=[
            NodeSpec(id="react_entry", kind="skill"),
            NodeSpec(id="react_observe", kind="skill"),
        ],
        skills=[SkillRef(id="react", version="0.1.0")],
    )
    return Graph(ir)


# --------------------------------------------------------------------------- #
# Cases                                                                       #
# --------------------------------------------------------------------------- #


async def test_skill_subgraph_node_added_changes_graph_hash() -> None:
    """Adding a node to the skill subgraph shifts the parent ``graph_hash``.

    Pre-condition for the refusal contract: replay refusal is only
    meaningful if the topology change actually moves the hash. Pinned as
    a separate test so a hash-stability regression surfaces with a
    targeted failure rather than as a side effect of the refusal test.
    """
    v1 = _build_skill_graph_v1()
    v2 = _build_skill_graph_v2_added_node()

    assert v1.graph_hash != v2.graph_hash, (
        "adding a node to the skill subgraph must change graph_hash "
        "(FR-4 structural-hash component (a) covers IRDocument.nodes); "
        f"got identical hashes: {v1.graph_hash!r}"
    )


async def test_skill_topology_change_refuses_replay(tmp_path: Path) -> None:
    """``resume`` raises :class:`CheckpointError` on skill topology change.

    Engine-side FR-20 mechanism reused for skills (FR-35, AC-10.3): the
    persisted checkpoint's ``graph_hash`` was pinned to v1, and the
    parent :class:`Graph` is now v2 (extra node inside the skill
    subgraph). Without a matching :class:`MigrateBlock` in the v2 IR,
    ``resume`` MUST refuse rather than silently rebinding.
    """
    cp = SQLiteCheckpointer(tmp_path / "ckpt.db")
    await cp.bootstrap()
    try:
        run_id = "run-skill-topology-001"
        v1 = _build_skill_graph_v1()
        await cp.write(_make_checkpoint(run_id=run_id, graph_hash=v1.graph_hash))

        v2 = _build_skill_graph_v2_added_node()
        assert v1.graph_hash != v2.graph_hash, "test setup invariant: v2 must differ from v1"

        with pytest.raises(CheckpointError):
            await GraphRun.resume(cp, run_id, graph=v2)  # pyright: ignore[reportCallIssue]
    finally:
        await cp.close()


async def test_skill_topology_refusal_carries_structured_context(
    tmp_path: Path,
) -> None:
    """Refusal :class:`CheckpointError` exposes the structured FR-20 context keys.

    Mirrors the engine-level contract pinned by
    :func:`test_resume_hash_mismatch_error_carries_structured_context` at
    the skill subgraph level: CLI / inspect surfaces consume the same
    three context keys regardless of *which* layer (top-level graph vs.
    skill subgraph) caused the topology change (FR-20 + FR-35).
    """
    cp = SQLiteCheckpointer(tmp_path / "ckpt.db")
    await cp.bootstrap()
    try:
        run_id = "run-skill-topology-002"
        v1 = _build_skill_graph_v1()
        v1_hash = v1.graph_hash
        await cp.write(_make_checkpoint(run_id=run_id, graph_hash=v1_hash))

        v2 = _build_skill_graph_v2_added_node()  # no migrate block

        with pytest.raises(CheckpointError) as excinfo:
            await GraphRun.resume(cp, run_id, graph=v2)  # pyright: ignore[reportCallIssue]

        ctx = excinfo.value.context
        assert ctx.get("expected_hash") == v2.graph_hash, (
            f"expected_hash must be the v2 Graph's current graph_hash; got context={ctx!r}"
        )
        assert ctx.get("actual_hash") == v1_hash, (
            f"actual_hash must be the checkpoint's persisted graph_hash; got context={ctx!r}"
        )
        assert ctx.get("migrate_available") is False, (
            f"no migrate block in IR -> migrate_available must be False; got context={ctx!r}"
        )
    finally:
        await cp.close()
