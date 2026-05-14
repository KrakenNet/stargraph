# SPDX-License-Identifier: Apache-2.0
"""CRITERIA Fancy #12: error-budget halt-new on rollback-rate threshold.

Inject rollback-rate=6%/24h via PG ``cve_rem_run_outcomes``; the
collector queries the rate; the ``cve_rem.kill_switches`` Fathom
pack's ``rollback-rate-exceeded`` rule must fire halt-new within 5
minutes (Fathom rule eval cadence).

Three scenarios:

* **Positive (under threshold)** — synthesize 100 runs, 3 with
  ``rollback_triggered=True`` (3% < 5% threshold). Fathom engine runs
  the metric fact; no violations.
* **Negative (over threshold)** — synthesize 100 runs, 6 with
  ``rollback_triggered=True`` (6%). Engine emits exactly one
  ``rollback-rate-exceeded`` violation with ``severity=halt``.
* **Halt-new persistence** — write the violation to a halt-new ledger
  table so a downstream pipeline-start gate (or external listener)
  can read it. Verifier reads the row back.

Run::

    set -a; source demos/cve_remediation/.env; set +a
    uv run --no-project python -m demos.cve_remediation.scripts.verify_F12_error_budget
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from typing import Any

import asyncpg
from fathom import Engine

from types import SimpleNamespace

from demos.cve_remediation.graph.real_nodes import (
    HaltNewGateNode,
    RunOutcomePersistNode,
)
from demos.cve_remediation.graph.state import CveRemState
from demos.cve_remediation.graph.tests._pack_helpers import (
    load_pack_rules,
    violations,
)


_TABLE = "cve_rem_run_outcomes"
_HALT_TABLE = "cve_rem_halt_new_ledger"
_THRESHOLD = float(os.environ.get("F12_ROLLBACK_THRESHOLD", "0.05"))
_WINDOW_HOURS = int(os.environ.get("F12_WINDOW_HOURS", "24"))


async def _conn() -> asyncpg.Connection:
    dsn = os.environ.get("POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeError("POSTGRES_DSN unset")
    return await asyncpg.connect(dsn)


async def _ensure_tables() -> None:
    conn = await _conn()
    try:
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
                id SERIAL PRIMARY KEY,
                run_id TEXT NOT NULL,
                cve_id TEXT NOT NULL,
                plan_hash TEXT,
                rollback_triggered BOOLEAN NOT NULL DEFAULT FALSE,
                verify_outcome TEXT,
                recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_HALT_TABLE} (
                id SERIAL PRIMARY KEY,
                kind TEXT NOT NULL,
                severity TEXT NOT NULL,
                run_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                rate NUMERIC,
                threshold NUMERIC,
                window_hours INTEGER,
                fired_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    finally:
        await conn.close()


async def _seed_run_outcomes(
    *,
    label: str,
    total: int,
    rollback_count: int,
) -> None:
    conn = await _conn()
    try:
        # Wipe prior rows for this label window so reruns are honest.
        await conn.execute(
            f"DELETE FROM {_TABLE} WHERE run_id LIKE $1",
            f"F12-{label}-%",
        )
        for i in range(total):
            run_id = f"F12-{label}-{i:03d}-{uuid.uuid4().hex[:6]}"
            rollback = i < rollback_count
            await conn.execute(
                f"""
                INSERT INTO {_TABLE}
                  (run_id, cve_id, plan_hash, rollback_triggered,
                   verify_outcome)
                VALUES ($1, $2, $3, $4, $5)
                """,
                run_id, f"F12-{label}-CVE", f"F12-{label}-plan",
                rollback, "rollback" if rollback else "patched",
            )
    finally:
        await conn.close()


async def _compute_rollback_rate(label: str) -> tuple[float, int, int]:
    """Return (rate, rollback_count, total) for the synthesized window."""
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            f"""
            SELECT
              count(*) AS total,
              count(*) FILTER (WHERE rollback_triggered) AS rollbacks
            FROM {_TABLE}
            WHERE run_id LIKE $1
              AND recorded_at > NOW() - ($2 || ' hours')::interval
            """,
            f"F12-{label}-%",
            str(_WINDOW_HOURS),
        )
        total = int(row["total"]) if row else 0
        rollbacks = int(row["rollbacks"]) if row else 0
        rate = (rollbacks / total) if total else 0.0
        return rate, rollbacks, total
    finally:
        await conn.close()


def _emit_metric_and_run(
    *, kind: str, value: float, threshold: float, window_hours: int,
    run_id: str,
) -> list[dict[str, Any]]:
    eng = Engine(default_decision="allow")
    load_pack_rules(eng, "cve_rem.kill_switches")
    fact = (
        f'(cve_rem.metric (kind "{kind}") (window_hours {window_hours}) '
        f'(value {value:.4f}) (threshold {threshold:.4f}) '
        f'(run_id "{run_id}") (computed_at "F12-test"))'
    )
    eng._env.assert_string(fact)  # pyright: ignore[reportPrivateUsage]
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    return violations(eng)


async def _persist_halt_new(
    *, viol: dict[str, Any], rate: float, threshold: float,
    window_hours: int,
) -> int:
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            f"""
            INSERT INTO {_HALT_TABLE}
              (kind, severity, run_id, reason, rate, threshold, window_hours)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            str(viol.get("kind", "")),
            str(viol.get("severity", "")),
            str(viol.get("run_id", "fleet")),
            str(viol.get("reason", "")),
            rate, threshold, window_hours,
        )
        return int(row["id"]) if row else 0
    finally:
        await conn.close()


async def _fetch_latest_halt() -> dict[str, Any]:
    conn = await _conn()
    try:
        row = await conn.fetchrow(
            f"""
            SELECT id, kind, severity, run_id, reason, rate,
                   threshold, window_hours, fired_at
            FROM {_HALT_TABLE}
            WHERE kind = 'rollback-rate-exceeded'
            ORDER BY fired_at DESC
            LIMIT 1
            """,
        )
        return dict(row) if row else {}
    finally:
        await conn.close()


def _grade_under(viols: list[dict[str, Any]]) -> bool:
    print(f"  [under] violations: {len(viols)}")
    for v in viols:
        print(f"    - {dict(v)}")
    if any(v.get("kind") == "rollback-rate-exceeded" for v in viols):
        print("  [under] ! rollback-rate-exceeded fired below threshold")
        return False
    return True


def _grade_over(viols: list[dict[str, Any]]) -> bool:
    print(f"  [over]  violations: {len(viols)}")
    for v in viols:
        print(f"    - {dict(v)}")
    halt = [v for v in viols if v.get("kind") == "rollback-rate-exceeded"]
    if not halt:
        print("  [over]  ! rollback-rate-exceeded did NOT fire above threshold")
        return False
    if str(halt[0].get("severity", "")) != "halt":
        print(f"  [over]  ! severity {halt[0].get('severity')!r} != 'halt'")
        return False
    return True


async def main() -> int:
    overall = True
    print("=== F12 VERIFICATION (error-budget halt-new on rollback-rate) ===\n")
    if not os.environ.get("POSTGRES_DSN"):
        print("! POSTGRES_DSN unset; FAIL")
        return 1
    print(f"  threshold = {_THRESHOLD}")
    print(f"  window    = {_WINDOW_HOURS} hours\n")

    await _ensure_tables()

    # Scenario 1: under threshold (3 / 100 = 3%).
    print("--- Scenario 1: under threshold (3%) ---")
    await _seed_run_outcomes(label="under", total=100, rollback_count=3)
    rate, rb, total = await _compute_rollback_rate("under")
    print(f"  computed rate: {rate:.4f} ({rb}/{total} rollbacks)")
    if rate >= _THRESHOLD:
        print("  ! synthesized rate >= threshold; check seed math")
        overall = False
    viols_under = _emit_metric_and_run(
        kind="rollback-rate", value=rate, threshold=_THRESHOLD,
        window_hours=_WINDOW_HOURS, run_id="fleet",
    )
    if not _grade_under(viols_under):
        overall = False

    # Scenario 2: over threshold (6 / 100 = 6%).
    print("\n--- Scenario 2: over threshold (6%) ---")
    await _seed_run_outcomes(label="over", total=100, rollback_count=6)
    rate, rb, total = await _compute_rollback_rate("over")
    print(f"  computed rate: {rate:.4f} ({rb}/{total} rollbacks)")
    if rate <= _THRESHOLD:
        print("  ! synthesized rate <= threshold; check seed math")
        overall = False
    viols_over = _emit_metric_and_run(
        kind="rollback-rate", value=rate, threshold=_THRESHOLD,
        window_hours=_WINDOW_HOURS, run_id="fleet",
    )
    if not _grade_over(viols_over):
        overall = False

    # Scenario 3: halt-new persists into ledger; downstream gate can read it.
    print("\n--- Scenario 3: persist halt-new ledger entry ---")
    halt_viol = next(
        (v for v in viols_over if v.get("kind") == "rollback-rate-exceeded"),
        None,
    )
    if halt_viol:
        ledger_id = await _persist_halt_new(
            viol=halt_viol, rate=rate, threshold=_THRESHOLD,
            window_hours=_WINDOW_HOURS,
        )
        print(f"  inserted halt-new ledger row id: {ledger_id}")
    latest = await _fetch_latest_halt()
    print(f"  latest halt-new row: {latest}")
    if not latest:
        print("  ! no halt-new ledger row found")
        overall = False
    elif latest.get("severity") != "halt":
        print(f"  ! latest severity {latest.get('severity')!r} != halt")
        overall = False

    # Scenario 4: RunOutcomePersistNode integration — drive the node
    # directly with a synthetic state and confirm a row lands in
    # cve_rem_run_outcomes (closes the F12-1 gap: pipeline now writes
    # to the table the metric collector reads from).
    print("\n--- Scenario 4: RunOutcomePersistNode writes outcomes row ---")
    sample_run_id = f"F12-int-{uuid.uuid4().hex[:8]}"
    synth = CveRemState(
        run_id=sample_run_id,
        cve_id="F12-INT-CVE",
        plan_hash=f"F12-INT-{uuid.uuid4().hex[:8]}",
        rollback_triggered=True,
        verify_outcome="vulnerable",
    )
    delta = await RunOutcomePersistNode().execute(
        synth, SimpleNamespace(run_id=sample_run_id),
    )
    if delta:
        synth = synth.model_copy(update=delta)
    print(f"  state.run_outcome_written : {synth.run_outcome_written}")
    print(f"  last_run_outcome_error    : {synth.last_run_outcome_error!r}")
    if not synth.run_outcome_written:
        print("  ! RunOutcomePersistNode did not land a row")
        overall = False
    # Read back to confirm.
    conn = await _conn()
    try:
        readback = await conn.fetchrow(
            f"SELECT run_id, rollback_triggered, verify_outcome "
            f"FROM {_TABLE} WHERE run_id = $1",
            sample_run_id,
        )
    finally:
        await conn.close()
    print(f"  PG readback               : {dict(readback) if readback else '<none>'}")
    if not readback:
        print("  ! row missing on readback")
        overall = False
    elif not readback["rollback_triggered"]:
        print("  ! rollback_triggered did not persist")
        overall = False

    # Scenario 5: HaltNewGateNode integration — with the over-threshold
    # halt-new ledger row already inserted (Scenario 3), the gate must
    # set halt_new_active=True + halt_reason on a fresh state.
    print("\n--- Scenario 5: HaltNewGateNode reads ledger + halts run ---")
    fresh = CveRemState(run_id="F12-gate", cve_id="F12-INT-CVE")
    delta = await HaltNewGateNode().execute(
        fresh, SimpleNamespace(run_id="F12-gate"),
    )
    if delta:
        fresh = fresh.model_copy(update=delta)
    print(f"  halt_new_active : {fresh.halt_new_active}")
    print(f"  halt_reason     : {fresh.halt_reason!r}")
    if not fresh.halt_new_active:
        print("  ! HaltNewGateNode did not flag halt-new active")
        overall = False
    if "halt-new" not in (fresh.halt_reason or "").lower():
        print(f"  ! halt_reason {fresh.halt_reason!r} does not mention halt-new")
        overall = False

    # Scenario 6: TTL window — older ledger rows must NOT trigger.
    # Set TTL to 0 minutes (any halt is stale) and confirm the gate
    # ignores it.
    print("\n--- Scenario 6: HaltNewGateNode TTL = 0 ignores any halt ---")
    os.environ["CVE_REM_HALT_NEW_TTL_MINUTES"] = "0"
    try:
        fresh2 = CveRemState(run_id="F12-ttl", cve_id="F12-INT-CVE")
        delta = await HaltNewGateNode().execute(
            fresh2, SimpleNamespace(run_id="F12-ttl"),
        )
        if delta:
            fresh2 = fresh2.model_copy(update=delta)
        print(f"  halt_new_active (TTL=0): {fresh2.halt_new_active}")
        if fresh2.halt_new_active:
            print("  ! TTL=0 should match nothing; gate should not fire")
            overall = False
    finally:
        os.environ.pop("CVE_REM_HALT_NEW_TTL_MINUTES", None)

    print()
    if overall:
        print("=== OVERALL: PASS ===")
    else:
        print("=== OVERALL: FAIL ===")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
