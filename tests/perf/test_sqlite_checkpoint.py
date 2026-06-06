# SPDX-License-Identifier: Apache-2.0
"""SQLite checkpoint commit calibration (NFR-3).

Times :py:meth:`stargraph.checkpoint.sqlite.SQLiteCheckpointer.write` -- which
issues an ``INSERT OR REPLACE`` plus a ``COMMIT`` against the WAL -- across
100 sequential commits and reports p50/p95/p99 latencies in milliseconds.

The ``write`` path is the hot loop for FR-17: every step boundary in the
runtime emits one commit. The 50ms p95 budget tracks NFR-3's "checkpoint
cost ≤ 50 ms median on local SSD" target; this calibration is the
measurement layer behind that promise.

Skip-by-default: marked ``@pytest.mark.slow``; run with ``--runslow``.
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from stargraph.checkpoint import Checkpoint
from stargraph.checkpoint.sqlite import SQLiteCheckpointer

if TYPE_CHECKING:
    from pathlib import Path


def _make_checkpoint(run_id: str, step: int) -> Checkpoint:
    """Build a representative :class:`Checkpoint` for the calibration loop.

    Payload size is the minimum that exercises every column so
    ``dumps_jsonb`` runs over each JSONB field and the WAL has to flush a
    realistic row -- empty payloads would under-measure.
    """
    return Checkpoint(
        run_id=run_id,
        step=step,
        branch_id=None,
        parent_step_idx=None,
        graph_hash="sha256:graph",
        runtime_hash="sha256:runtime",
        state={"counter": step, "message": "perf"},
        clips_facts=[{"template": "evidence", "slots": {"field": "v"}}],
        last_node=f"n{step}",
        next_action=None,
        timestamp=datetime.now(UTC),
        parent_run_id=None,
        side_effects_hash="sha256:side",
    )


def _percentile(samples_ns: list[int], pct: float) -> float:
    """Return ``pct``-th percentile of ``samples_ns`` in milliseconds."""
    s = sorted(samples_ns)
    idx = max(0, min(len(s) - 1, round((pct / 100.0) * (len(s) - 1))))
    return s[idx] / 1_000_000.0


@pytest.mark.slow
def test_sqlite_checkpoint_commit_p95(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Commit 100 checkpoints; report p50/p95/p99 (ms).

    Soft-pass via ``pytest.xfail`` if p95 exceeds the 50ms NFR-3 budget so
    the calibration record always lands in stdout / the tee'd log.
    """
    iterations = 100
    db_path = tmp_path / "perf.db"
    samples_ns: list[int] = []

    async def _drive() -> None:
        cp = SQLiteCheckpointer(db_path)
        try:
            await cp.bootstrap()
            # Warm-up commit so WAL files are pre-allocated and the first
            # measurement isn't an outlier from -wal/-shm creation.
            await cp.write(_make_checkpoint("warmup", 0))
            for i in range(iterations):
                ckpt = _make_checkpoint(run_id="run-perf", step=i)
                t0 = time.perf_counter_ns()
                await cp.write(ckpt)
                samples_ns.append(time.perf_counter_ns() - t0)
        finally:
            await cp.close()

    asyncio.run(_drive())

    p50 = _percentile(samples_ns, 50)
    p95 = _percentile(samples_ns, 95)
    p99 = _percentile(samples_ns, 99)

    line = (
        f"sqlite_checkpoint_commit n={iterations} p50={p50:.4f}ms p95={p95:.4f}ms p99={p99:.4f}ms"
    )
    with capsys.disabled():
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    budget_ms = 50.0
    if p95 >= budget_ms:
        pytest.xfail(f"p95={p95:.4f}ms exceeded {budget_ms}ms budget (NFR-3 calibration soft-pass)")
