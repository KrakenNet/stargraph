# SPDX-License-Identifier: Apache-2.0
"""Anchor gap-watch daemon (CRITERIA fancy #10).

Companion to ``anchor_daily.py``. Runs on a separate cadence
(default every hour) checking the freshness of the most recent
audit-anchor file under ``$HARBOR_ARTIFACTS_ROOT/anchors/``:

* gap < 24h -> ok, no-op.
* 24h <= gap < 72h -> page (logs WARN; in production pages on-call).
* gap >= 72h -> emit halt-new ledger row keyed
  ``audit-anchor-gap-72h`` so the in-pipeline ``HaltNewGateNode``
  freezes the fleet on the next run start.

This is the runtime that backs CRITERIA #10's "external verifier
confirms today's chain head". The verifier (run from a separate
machine) reads the anchor file, replays the JWS, and asserts
freshness; this daemon is the proactive complement that surfaces
staleness before the external verifier polls.

Run::

    set -a; source demos/cve_remediation/.env; set +a
    uv run --no-project python -m demos.cve_remediation.scripts.anchor_gap_watch
    # loop mode:
    uv run --no-project python -m demos.cve_remediation.scripts.anchor_gap_watch \
        --loop --interval 3600
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import asyncpg


_ARTIFACTS_ROOT = Path(
    os.environ.get("HARBOR_ARTIFACTS_ROOT", ".harbor/artifacts")
)
_ANCHOR_DIR = _ARTIFACTS_ROOT / "anchors"


def _latest_anchor() -> Path | None:
    if not _ANCHOR_DIR.exists():
        return None
    candidates = sorted(_ANCHOR_DIR.glob("*.anchor.json"),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True)
    return candidates[0] if candidates else None


def _hours_since(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 3600.0


async def _emit_halt(*, hours_gap: float, anchor_path: Path) -> int:
    dsn = os.environ.get("POSTGRES_DSN", "").strip()
    if not dsn:
        return 0
    conn = await asyncpg.connect(dsn)
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
        row = await conn.fetchrow(
            """
            INSERT INTO cve_rem_halt_new_ledger
              (kind, severity, run_id, reason, rate, threshold, window_hours)
            VALUES ('audit-anchor-gap-72h', 'halt', 'fleet', $1, $2, 72.0, 1)
            RETURNING id
            """,
            f"audit chain anchor stale: {anchor_path.name} is "
            f"{hours_gap:.1f}h old (>72h)",
            float(hours_gap),
        )
        return int(row["id"]) if row else 0
    finally:
        await conn.close()


async def run_once() -> dict[str, object]:
    now_iso = datetime.now(UTC).isoformat()
    anchor = _latest_anchor()
    if anchor is None:
        msg = (f"[{now_iso}] no anchor files in {_ANCHOR_DIR} -- "
               "anchor_daily has never run; treating as 72h gap.")
        print(msg)
        ledger_id = await _emit_halt(
            hours_gap=float("inf"),
            anchor_path=Path(".harbor/artifacts/anchors/(none)"),
        )
        return {"status": "missing", "halt_ledger_id": ledger_id}
    gap = _hours_since(anchor)
    print(f"[{now_iso}] latest={anchor.name} gap={gap:.1f}h")
    if gap < 24:
        return {"status": "fresh", "gap_hours": gap}
    if gap < 72:
        print(
            f"[{now_iso}] WARN: anchor 24h+; on-call should be paged. "
            "(production: PagerDuty/Slack hook.)"
        )
        return {"status": "page-24h", "gap_hours": gap}
    ledger_id = await _emit_halt(hours_gap=gap, anchor_path=anchor)
    print(
        f"[{now_iso}] HALT: anchor 72h+; halt-new ledger row id="
        f"{ledger_id}"
    )
    return {"status": "halt-72h", "gap_hours": gap,
            "halt_ledger_id": ledger_id}


async def main_async(args: argparse.Namespace) -> int:
    if args.loop:
        while True:
            try:
                await run_once()
            except Exception as exc:  # noqa: BLE001
                print(f"  ! pass failed: {type(exc).__name__}: {exc}",
                      flush=True)
            await asyncio.sleep(args.interval)
    else:
        await run_once()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="harbor anchor-gap-watch",
        description="Watches anchor freshness; pages at 24h, halts at 72h.",
    )
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=3600,
                        help="Seconds between checks in --loop "
                             "(default 3600 = hourly).")
    args = parser.parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
