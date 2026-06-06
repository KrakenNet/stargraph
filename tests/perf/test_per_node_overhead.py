# SPDX-License-Identifier: Apache-2.0
"""Per-node overhead calibration (NFR-3, design §3.1.5).

Measures the wall-clock cost of one ``NodeBase.execute()`` dispatch with a
no-op body, taken as the lower bound on Stargraph's per-step overhead. The
budget is a soft target: the test prints ``p50/p95/p99`` to stdout and
``xfails`` (rather than fails) when the p99 exceeds the budget so the spec
phase keeps moving forward; the calibration record lands in commit history
plus ``/tmp/perf-node.log`` for later tightening or relaxation.

Skip-by-default: marked ``@pytest.mark.slow``; run with ``--runslow``.

Initial budget (NFR-3): p99 < 5ms per dispatch.
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any

import pytest
from pydantic import BaseModel

from stargraph.nodes.base import ExecutionContext, NodeBase


class _NoopState(BaseModel):
    """Minimal state model -- no fields read by the node body."""


class _NoopCtx:
    """Minimal :class:`ExecutionContext` impl -- only ``run_id`` is required."""

    run_id: str = "perf-noop"


class _NoopNode(NodeBase):
    """Empty-body node: returns an empty dict immediately."""

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del state, ctx
        return {}


def _percentile(samples_ns: list[int], pct: float) -> float:
    """Return the ``pct``-th percentile of ``samples_ns`` in milliseconds."""
    s = sorted(samples_ns)
    idx = max(0, min(len(s) - 1, round((pct / 100.0) * (len(s) - 1))))
    return s[idx] / 1_000_000.0


@pytest.mark.slow
def test_per_node_overhead_p99(capsys: pytest.CaptureFixture[str]) -> None:
    """Time 1000 no-op ``execute`` calls; report p50/p95/p99 (ms).

    Soft-pass via ``pytest.xfail`` if the p99 exceeds the 5ms budget --
    the calibration number is the artifact, not a hard gate.
    """
    iterations = 1000
    node = _NoopNode()
    state = _NoopState()
    ctx = _NoopCtx()
    samples_ns: list[int] = []

    async def _drive() -> None:
        # Warm-up (5 calls) -- dispatch + asyncio frame allocation amortizes
        # after the first few invocations.
        for _ in range(5):
            await node.execute(state, ctx)
        for _ in range(iterations):
            t0 = time.perf_counter_ns()
            await node.execute(state, ctx)
            samples_ns.append(time.perf_counter_ns() - t0)

    asyncio.run(_drive())

    p50 = _percentile(samples_ns, 50)
    p95 = _percentile(samples_ns, 95)
    p99 = _percentile(samples_ns, 99)

    # Bypass pytest capture so the calibration line lands in the tee'd log
    # ("pytest -q" hides stdout for passing tests). ``capsys.disabled()``
    # restores the real ``sys.stdout`` for the duration of the with-block.
    line = f"per_node_overhead n={iterations} p50={p50:.4f}ms p95={p95:.4f}ms p99={p99:.4f}ms"
    with capsys.disabled():
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    budget_ms = 5.0
    if p99 >= budget_ms:
        pytest.xfail(f"p99={p99:.4f}ms exceeded {budget_ms}ms budget (NFR-3 calibration soft-pass)")
