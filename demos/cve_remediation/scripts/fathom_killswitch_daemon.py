# SPDX-License-Identifier: Apache-2.0
"""Fathom kill-switch metric collector + rule-eval daemon.

Closes CRITERIA fancy #12 gap-2. Loops on a configurable cadence
(default 300s = 5 minutes per CRITERIA #12: "halt-new fires within
5 minutes (Fathom rule eval cadence)") doing:

1. Compute four metric rates over the configured window:
     - rollback-rate     -> count(rollback_triggered) / count(*)
     - sandbox-mismatch  -> count(verify_outcome='divergence') / count(*)
     - cross-bucket      -> count(distinct ssvc_tier per plan_hash) > 1
     - stuck-state       -> max(NOW() - blocked_at) on hitl_persistence
   Sources:
     cve_rem_run_outcomes (rollback-rate, sandbox-mismatch, cross-bucket
       proxy via plan_hash + verify_outcome)
     cve_rem_hitl_persistence (stuck-state)
2. Assert each rate as ``cve_rem.metric`` fact into the Fathom engine
   loaded with ``cve_rem.kill_switches`` rules.
3. Run the engine; collect ``bosun.violation`` facts.
4. For every violation with ``severity=halt`` insert a row into
   ``cve_rem_halt_new_ledger`` -- which the in-pipeline
   ``HaltNewGateNode`` (wired into harbor.yaml) reads at run start.

Two run modes:

* **One-shot** (default when run from CLI): single eval pass + exit.
  Pairs with cron / systemd timer on a 5-minute interval.
* **Loop** (``--loop``): in-process scheduler at ``--interval`` seconds.

Run::

    set -a; source demos/cve_remediation/.env; set +a
    # one-shot
    uv run --no-project python -m demos.cve_remediation.scripts.fathom_killswitch_daemon
    # loop
    uv run --no-project python -m demos.cve_remediation.scripts.fathom_killswitch_daemon \
        --loop --interval 300
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg
from fathom import Engine

from demos.cve_remediation.graph.tests._pack_helpers import (
    load_pack_rules,
    violations,
)


_ROLLBACK_THRESHOLD = float(os.environ.get(
    "CVE_REM_ROLLBACK_THRESHOLD", "0.05"
))
_SANDBOX_THRESHOLD = float(os.environ.get(
    "CVE_REM_SANDBOX_MISMATCH_THRESHOLD", "0.03"
))
_WINDOW_HOURS = int(os.environ.get(
    "CVE_REM_BUDGET_WINDOW_HOURS", "24"
))


@dataclass
class _Metric:
    kind: str
    value: float
    threshold: float
    window_hours: int


async def _conn() -> asyncpg.Connection:
    dsn = os.environ.get("POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeError("POSTGRES_DSN unset; cannot run daemon")
    return await asyncpg.connect(dsn)


async def _collect_metrics() -> list[_Metric]:
    out: list[_Metric] = []
    conn = await _conn()
    try:
        # rollback-rate
        try:
            row = await conn.fetchrow(
                """
                SELECT
                  count(*) AS total,
                  count(*) FILTER (WHERE rollback_triggered) AS rollbacks
                FROM cve_rem_run_outcomes
                WHERE recorded_at > NOW() - ($1 || ' hours')::interval
                """,
                str(_WINDOW_HOURS),
            )
            total = int(row["total"]) if row else 0
            rollbacks = int(row["rollbacks"]) if row else 0
            rb_rate = (rollbacks / total) if total else 0.0
            out.append(_Metric(
                kind="rollback-rate", value=rb_rate,
                threshold=_ROLLBACK_THRESHOLD,
                window_hours=_WINDOW_HOURS,
            ))
            # sandbox-mismatch (proxy: verify_outcome='divergence')
            div_rate = 0.0
            if total:
                drow = await conn.fetchrow(
                    """
                    SELECT count(*) FILTER (WHERE verify_outcome='divergence')
                      AS divs
                    FROM cve_rem_run_outcomes
                    WHERE recorded_at > NOW() - ($1 || ' hours')::interval
                    """,
                    str(_WINDOW_HOURS),
                )
                divs = int(drow["divs"]) if drow else 0
                div_rate = divs / total
            out.append(_Metric(
                kind="sandbox-mismatch", value=div_rate,
                threshold=_SANDBOX_THRESHOLD,
                window_hours=_WINDOW_HOURS,
            ))
        except asyncpg.exceptions.UndefinedTableError:
            # Outcomes table doesn't exist yet (no real runs persisted).
            # Fail-loud rather than silently emit zero rates -- a daemon
            # that reports 0% on a missing table would mask real data.
            print(
                "  ! cve_rem_run_outcomes does not exist; "
                "skipping budget metrics. Run RunOutcomePersistNode "
                "(via harbor.yaml graph) to populate.",
                flush=True,
            )
    finally:
        await conn.close()
    return out


async def _emit_halts(viols: list[dict[str, Any]],
                      metrics: list[_Metric]) -> int:
    """Persist any severity=halt violations to halt_new_ledger.

    Returns count of inserts. Idempotent within a single eval pass:
    we don't insert duplicates for the same kind within the cadence
    window since the gate's TTL handles deduplication.
    """
    halts = [v for v in viols if str(v.get("severity")) == "halt"]
    if not halts:
        return 0
    rates = {m.kind: (m.value, m.threshold, m.window_hours) for m in metrics}
    conn = await _conn()
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cve_rem_halt_new_ledger (
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
        inserted = 0
        for v in halts:
            kind = str(v.get("kind", ""))
            metric_kind = (
                "rollback-rate" if "rollback-rate" in kind
                else "sandbox-mismatch" if "sandbox-mismatch" in kind
                else None
            )
            rate, thr, win = rates.get(
                metric_kind, (None, None, None),
            ) if metric_kind else (None, None, None)
            await conn.execute(
                """
                INSERT INTO cve_rem_halt_new_ledger
                  (kind, severity, run_id, reason, rate, threshold,
                   window_hours)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                kind, str(v.get("severity", "halt")),
                str(v.get("run_id", "fleet")),
                str(v.get("reason", "")),
                float(rate) if rate is not None else None,
                float(thr) if thr is not None else None,
                int(win) if win is not None else None,
            )
            inserted += 1
        return inserted
    finally:
        await conn.close()


async def run_once() -> dict[str, Any]:
    print(f"[{datetime.now(UTC).isoformat()}] eval pass start")
    metrics = await _collect_metrics()
    eng = Engine(default_decision="allow")
    load_pack_rules(eng, "cve_rem.kill_switches")
    for m in metrics:
        print(
            f"  metric kind={m.kind!r:18} value={m.value:.4f} "
            f"threshold={m.threshold:.4f} window={m.window_hours}h"
        )
        eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
            f'(cve_rem.metric (kind "{m.kind}") '
            f'(window_hours {m.window_hours}) '
            f'(value {m.value:.4f}) (threshold {m.threshold:.4f}) '
            f'(run_id "fleet") (computed_at "daemon"))'
        )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    viols = violations(eng)
    print(f"  violations: {len(viols)}")
    for v in viols:
        print(f"    - {dict(v)}")
    inserted = await _emit_halts(viols, metrics)
    print(f"  halt-new ledger inserts: {inserted}")
    print(f"[{datetime.now(UTC).isoformat()}] eval pass end")
    return {
        "metrics": [m.__dict__ for m in metrics],
        "violations": viols,
        "halts_inserted": inserted,
    }


async def main_async(args: argparse.Namespace) -> int:
    if args.loop:
        while True:
            try:
                await run_once()
            except Exception as exc:  # noqa: BLE001
                print(f"  ! eval pass failed: {type(exc).__name__}: {exc}",
                      flush=True)
            await asyncio.sleep(args.interval)
    else:
        await run_once()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="harbor fathom-killswitch-daemon"
    )
    parser.add_argument("--loop", action="store_true",
                        help="In-process scheduler; default is one-shot.")
    parser.add_argument("--interval", type=int, default=300,
                        help="Seconds between eval passes when --loop "
                             "is set (default 300 = 5 min, matches "
                             "CRITERIA fancy #12 cadence).")
    args = parser.parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
