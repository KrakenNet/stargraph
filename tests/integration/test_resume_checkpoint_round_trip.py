# SPDX-License-Identifier: Apache-2.0
"""Integration: ``GraphRun.checkpoint()`` round-trips through the Checkpointer (T01, INV-2).

Pins the checkpoint write/read cycle: the :class:`Checkpoint` produced by
:meth:`GraphRun.checkpoint` must round-trip through
:class:`SQLiteCheckpointer.write` → :meth:`read_latest` with all required
fields per ``checkpoint/protocol.py:34-63`` preserved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harbor.checkpoint.protocol import Checkpoint
from harbor.checkpoint.sqlite import SQLiteCheckpointer
from harbor.graph import Graph, GraphRun
from harbor.ir import IRDocument, NodeSpec


def _graph() -> Graph:
    return Graph(
        IRDocument(
            ir_version="1.0.0",
            id="run:resume-round-trip",
            nodes=[NodeSpec(id="a", kind="echo")],
        ),
    )


@pytest.mark.integration
async def test_checkpoint_round_trips_through_sqlite_checkpointer(
    tmp_path: Path,
) -> None:
    """``GraphRun.checkpoint()`` → ``checkpointer.write`` → ``read_latest`` returns
    a :class:`Checkpoint` equal to the input by ``model_dump`` (INV-2)."""
    db_path = tmp_path / "ckpt.db"
    cp = SQLiteCheckpointer(db_path=db_path)
    await cp.bootstrap()

    run = GraphRun(run_id="run-round-trip", graph=_graph(), checkpointer=cp)
    snapshot = run.checkpoint()
    assert isinstance(snapshot, Checkpoint)
    await cp.write(snapshot)

    reloaded = await cp.read_latest("run-round-trip")
    assert reloaded is not None
    # Pin equality on the canonical (sorted) dict view; raw model equality
    # would fail on any non-deterministic field rendering.
    assert reloaded.model_dump() == snapshot.model_dump()
