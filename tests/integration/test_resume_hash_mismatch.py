# SPDX-License-Identifier: Apache-2.0
"""TDD-RED: ``resume()`` ``graph_hash`` mismatch refusal + IR migrate (FR-20, NFR-9, AC-3.1).

Pins the contract for hash-mismatch handling on resume *before* the
implementation lands in task 3.26. Currently RED because
:meth:`stargraph.GraphRun.resume` raises :class:`NotImplementedError`.

Cases (FR-20, NFR-9, AC-3.1):

1. ``test_resume_refuses_on_graph_hash_mismatch`` -- when the persisted
   checkpoint's ``graph_hash`` does not match the parent ``Graph``'s
   current ``graph_hash``, ``resume`` raises
   :class:`stargraph.errors.CheckpointError` with ``context`` containing
   ``expected_hash``, ``actual_hash``, and ``migrate_available=False``.
2. ``test_resume_hash_mismatch_error_carries_structured_context`` --
   the error's ``context`` dict surfaces the three structured keys so
   CLI / inspect surfaces can render the mismatch precisely (FR-20:
   "Error is structured ... with ``expected_hash``, ``actual_hash``,
   ``migrate_available: bool``").
3. ``test_resume_passes_when_ir_migrate_block_applies`` -- when the
   parent ``Graph``'s IR carries a :class:`~stargraph.ir.MigrateBlock`
   whose ``from_hash`` matches the checkpoint's stored ``graph_hash``
   and ``to_hash`` matches the current graph hash, ``resume`` succeeds
   (the migrate block "applies"; FR-20 + NFR-9).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from stargraph import Graph, GraphRun
from stargraph.checkpoint import Checkpoint
from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.errors import CheckpointError
from stargraph.ir import IRDocument, MigrateBlock, NodeSpec

if TYPE_CHECKING:
    from pathlib import Path


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _make_checkpoint(
    *,
    run_id: str,
    step: int,
    graph_hash: str,
) -> Checkpoint:
    """Build a populated :class:`Checkpoint` with a caller-pinned ``graph_hash``."""
    return Checkpoint(
        run_id=run_id,
        step=step,
        branch_id=None,
        parent_step_idx=None,
        graph_hash=graph_hash,
        runtime_hash="sha256:runtime-v1",
        state={"x": step},
        clips_facts=[],
        last_node="n0",
        next_action=None,
        timestamp=datetime.now(UTC),
        parent_run_id=None,
        side_effects_hash="sha256:side-v1",
    )


def _build_graph_v1() -> Graph:
    """Construct a minimal :class:`Graph` (no migrate block)."""
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:resume-hash-mismatch-v1",
        nodes=[NodeSpec(id="entry", kind="rule")],
    )
    return Graph(ir)


def _build_graph_v2_with_migrate(*, from_hash: str, to_hash: str) -> Graph:
    """Construct a :class:`Graph` whose IR carries a matching migrate block."""
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:resume-hash-mismatch-v2",
        nodes=[NodeSpec(id="entry", kind="rule")],
        migrate=[MigrateBlock(from_hash=from_hash, to_hash=to_hash)],
    )
    return Graph(ir)


# --------------------------------------------------------------------------- #
# Cases                                                                       #
# --------------------------------------------------------------------------- #


async def test_resume_refuses_on_graph_hash_mismatch(tmp_path: Path) -> None:
    """``resume`` raises :class:`CheckpointError` when ``graph_hash`` mismatches.

    The persisted checkpoint pins ``graph_hash="sha256:stale-hash"`` -- a
    value that cannot match any newly-constructed ``Graph``'s structural
    hash. ``resume`` MUST refuse rather than silently rebinding.
    """
    cp = SQLiteCheckpointer(tmp_path / "ckpt.db")
    await cp.bootstrap()
    try:
        run_id = "run-hash-mismatch-001"
        stale_hash = "sha256:stale-hash-from-prior-version"
        await cp.write(_make_checkpoint(run_id=run_id, step=0, graph_hash=stale_hash))
        graph = _build_graph_v1()
        assert graph.graph_hash != stale_hash, (
            "test setup invariant: synthetic stale hash must differ from current"
        )

        with pytest.raises(CheckpointError):
            await GraphRun.resume(cp, run_id, graph=graph)  # pyright: ignore[reportCallIssue]
    finally:
        await cp.close()


async def test_resume_hash_mismatch_error_carries_structured_context(
    tmp_path: Path,
) -> None:
    """The :class:`CheckpointError` carries ``expected_hash``/``actual_hash``/``migrate_available``.

    FR-20: "Error is structured (CheckpointError with ``expected_hash``,
    ``actual_hash``, ``migrate_available: bool``)". CLI / inspect surfaces
    consume these keys from the error's ``context`` dict.
    """
    cp = SQLiteCheckpointer(tmp_path / "ckpt.db")
    await cp.bootstrap()
    try:
        run_id = "run-hash-mismatch-002"
        stale_hash = "sha256:stale-hash-no-migrate"
        await cp.write(_make_checkpoint(run_id=run_id, step=0, graph_hash=stale_hash))
        graph = _build_graph_v1()  # no migrate block

        with pytest.raises(CheckpointError) as excinfo:
            await GraphRun.resume(cp, run_id, graph=graph)  # pyright: ignore[reportCallIssue]

        ctx = excinfo.value.context
        assert ctx.get("expected_hash") == graph.graph_hash, (
            f"expected_hash must be the parent Graph's current graph_hash; got context={ctx!r}"
        )
        assert ctx.get("actual_hash") == stale_hash, (
            f"actual_hash must be the checkpoint's persisted graph_hash; got context={ctx!r}"
        )
        assert ctx.get("migrate_available") is False, (
            f"no migrate block in IR -> migrate_available must be False; got context={ctx!r}"
        )
    finally:
        await cp.close()


async def test_resume_passes_when_ir_migrate_block_applies(tmp_path: Path) -> None:
    """An IR ``migrate`` block whose ``from_hash`` matches the checkpoint succeeds.

    FR-20 + NFR-9: when the parent graph carries a matching
    :class:`MigrateBlock`, ``resume`` accepts the prior-hash checkpoint
    rather than refusing it. Returns a usable :class:`GraphRun`.
    """
    cp = SQLiteCheckpointer(tmp_path / "ckpt.db")
    await cp.bootstrap()
    try:
        run_id = "run-hash-mismatch-003"
        old_hash = "sha256:old-graph-hash-for-migrate"
        await cp.write(_make_checkpoint(run_id=run_id, step=0, graph_hash=old_hash))

        # Build a v2 Graph whose IR migrate block bridges old_hash → current.
        # ``to_hash`` is unknown until the Graph is constructed, so we build
        # it twice: once to read ``graph_hash``, then again with the matching
        # migrate block. Both Graphs use the same minimal IR, so the second
        # build's hash is deterministic given the migrate block presence is
        # accounted for in structural_hash component (a) -- the migrate IR
        # shape is part of the canonical doc.
        probe = _build_graph_v2_with_migrate(from_hash=old_hash, to_hash="placeholder")
        # Re-build with the actual to_hash now that we know it.
        graph = _build_graph_v2_with_migrate(from_hash=old_hash, to_hash=probe.graph_hash)

        # Hash mismatch on its own would refuse; the migrate block must rescue.
        run = await GraphRun.resume(cp, run_id, graph=graph)  # pyright: ignore[reportCallIssue]

        assert isinstance(run, GraphRun)
        assert run.run_id == run_id
    finally:
        await cp.close()
