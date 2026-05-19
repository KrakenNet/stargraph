# SPDX-License-Identifier: Apache-2.0
"""CRITERIA Fancy #7: GEPA accepts/rejects with margin.

Holdout test: candidate vs current artifact, weighted GEPA score,
epsilon margin = 0.02 (200 bp).

* Reject path: candidate=0.78 vs current=0.79  -> strictly_better=False,
  no ship event.
* Accept path: candidate=0.82 vs current=0.79  -> strictly_better=True,
  ship event posted.
* Tie path: candidate=0.79 vs current=0.79     -> strictly_better=False,
  no ship event.
* Below-margin path: candidate=0.80 vs current=0.79 (delta=0.01) ->
  strictly_better=False (margin requires >= 0.02 / 200 bp).
* Exact-margin path: candidate=0.81 vs current=0.79 (delta=0.02) ->
  strictly_better=True (>= margin passes the threshold).

We then persist accept events to a ``cve_rem_ship_events`` table so
downstream rolling-restart logic (Fancy #9) has a real ledger to walk.

Run::

    set -a; source demos/cve_remediation/.env; set +a
    uv run --no-project python -m demos.cve_remediation.scripts.verify_F7_gepa_margin
"""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from typing import Any

import asyncpg

from demos.cve_remediation.graph.real_nodes import GepaScoreComputerNode
from demos.cve_remediation.graph.state import CveRemState


def _components_for_score_bp(score_bp: int) -> dict[str, int]:
    """Return a 5-component dict whose weighted sum yields ``score_bp``.

    The node's weights total 100 (35+25+15+15+10). Setting every
    component to ``score_bp`` makes the weighted average exactly
    ``score_bp`` -- closed-form, no float rounding artifacts.
    """
    keys = ("validation", "sandbox", "cr_approved", "no_drift_7d",
            "no_rollback_30d")
    return {k: score_bp for k in keys}


async def _ensure_ship_events_table() -> None:
    dsn = os.environ.get("POSTGRES_DSN", "").strip()
    if not dsn:
        return
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cve_rem_ship_events (
                id SERIAL PRIMARY KEY,
                run_id TEXT NOT NULL,
                candidate_score_bp INTEGER NOT NULL,
                current_score_bp INTEGER NOT NULL,
                epsilon_margin_bp INTEGER NOT NULL,
                strictly_better BOOLEAN NOT NULL,
                shipped_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    finally:
        await conn.close()


async def _persist_ship_event(state: CveRemState) -> int:
    dsn = os.environ.get("POSTGRES_DSN", "").strip()
    if not dsn:
        return 0
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO cve_rem_ship_events
              (run_id, candidate_score_bp, current_score_bp,
               epsilon_margin_bp, strictly_better)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            state.run_id or "F7",
            state.candidate_score_bp,
            state.current_score_bp,
            state.epsilon_margin_bp,
            state.strictly_better,
        )
        return int(row["id"]) if row else 0
    finally:
        await conn.close()


async def _drive(
    *, label: str, current_bp: int, candidate_bp: int,
    epsilon_bp: int = 200,
) -> CveRemState:
    state = CveRemState(
        run_id=f"F7-{label}",
        gepa_components=_components_for_score_bp(candidate_bp),
        current_score_bp=current_bp,
        epsilon_margin_bp=epsilon_bp,
    )
    delta = await GepaScoreComputerNode().execute(
        state, SimpleNamespace(run_id=state.run_id),
    )
    if delta:
        state = state.model_copy(update=delta)
    return state


def _grade(
    label: str, state: CveRemState,
    *, expect_score_bp: int, expect_better: bool,
) -> bool:
    ok = True
    print(f"  [{label}] candidate_score_bp={state.candidate_score_bp} "
          f"current={state.current_score_bp} eps={state.epsilon_margin_bp} "
          f"-> strictly_better={state.strictly_better}")
    if state.candidate_score_bp != expect_score_bp:
        print(f"  [{label}] ! candidate_score_bp mismatch "
              f"(got {state.candidate_score_bp}, expected {expect_score_bp})")
        ok = False
    if state.strictly_better is not expect_better:
        print(f"  [{label}] ! strictly_better mismatch "
              f"(got {state.strictly_better}, expected {expect_better})")
        ok = False
    return ok


async def main() -> int:
    overall = True
    print("=== F7 VERIFICATION (GEPA accept/reject with margin) ===\n")
    if os.environ.get("POSTGRES_DSN"):
        await _ensure_ship_events_table()

    # CRITERIA scenarios.
    # 1. Reject: 0.78 vs 0.79
    s1 = await _drive(label="reject-0.78", current_bp=7900, candidate_bp=7800)
    if not _grade("reject-0.78", s1, expect_score_bp=7800,
                  expect_better=False):
        overall = False

    # 2. Accept: 0.82 vs 0.79
    s2 = await _drive(label="accept-0.82", current_bp=7900, candidate_bp=8200)
    if not _grade("accept-0.82", s2, expect_score_bp=8200,
                  expect_better=True):
        overall = False
    if s2.strictly_better and os.environ.get("POSTGRES_DSN"):
        ship_id = await _persist_ship_event(s2)
        print(f"  [accept-0.82] ship event id: {ship_id}")
        if ship_id <= 0:
            print("  [accept-0.82] ! ship event row not inserted")
            overall = False

    # 3. Tie: 0.79 vs 0.79
    s3 = await _drive(label="tie-0.79", current_bp=7900, candidate_bp=7900)
    if not _grade("tie-0.79", s3, expect_score_bp=7900, expect_better=False):
        overall = False

    # 4. Below-margin: 0.80 vs 0.79, delta = 100bp < 200bp eps.
    s4 = await _drive(label="below-margin", current_bp=7900,
                      candidate_bp=8000)
    if not _grade("below-margin", s4, expect_score_bp=8000,
                  expect_better=False):
        overall = False

    # 5. Exact-margin: 0.81 vs 0.79, delta = 200bp = eps -> accept.
    s5 = await _drive(label="exact-margin", current_bp=7900,
                      candidate_bp=8100)
    if not _grade("exact-margin", s5, expect_score_bp=8100,
                  expect_better=True):
        overall = False

    # 6. Custom epsilon: tighten margin to 50bp; the "below-margin"
    #    case (delta=100bp) now passes. Demonstrates eps is tunable.
    s6 = await _drive(label="custom-eps", current_bp=7900,
                      candidate_bp=8000, epsilon_bp=50)
    if not _grade("custom-eps", s6, expect_score_bp=8000,
                  expect_better=True):
        overall = False

    print()
    if overall:
        print("=== OVERALL: PASS ===")
    else:
        print("=== OVERALL: FAIL ===")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
