# SPDX-License-Identifier: Apache-2.0
"""POC milestone (task 1.29): end-to-end ``stargraph run sample-graph.yaml``.

Spawns the installed ``stargraph`` console-script via :mod:`subprocess`, points it
at ``tests/fixtures/sample-graph.yaml`` with both a JSONL audit sink and a
SQLite checkpointer wired through ``--log-file`` / ``--checkpoint``, then
asserts the four POC-gate conditions (FR-1, FR-8, FR-17, US-1):

1. exit code is ``0``;
2. the JSONL log file exists and contains at least 2 events;
3. the events include at least one ``TransitionEvent`` and one ``ResultEvent``;
4. the SQLite database has at least 1 row in the ``checkpoints`` table.

Uses the pytest ``tmp_path`` fixture (not ``/tmp`` directly) for CI hygiene.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from fathom.chained_log import GENESIS_RECORD_TYPE

from stargraph.audit.jsonl import unwrap_audit_record

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
SAMPLE_GRAPH: Path = REPO_ROOT / "tests" / "fixtures" / "sample-graph.yaml"


def test_poc_milestone(tmp_path: Path) -> None:
    """The four-assertion POC gate (FR-1, FR-8, FR-17, US-1)."""
    assert SAMPLE_GRAPH.exists(), f"missing fixture: {SAMPLE_GRAPH}"

    log_file = tmp_path / "run.jsonl"
    checkpoint_db = tmp_path / "poc.sqlite"

    # 1. Invoke ``stargraph run ...`` via subprocess. ``check=True`` makes
    #    assertion 1 (exit code 0) implicit -- a nonzero exit raises
    #    CalledProcessError before the rest of the assertions run.
    result = subprocess.run(
        [
            "stargraph",
            "run",
            str(SAMPLE_GRAPH),
            "--log-file",
            str(log_file),
            "--checkpoint",
            str(checkpoint_db),
        ],
        capture_output=True,
        check=True,
        cwd=REPO_ROOT,
        text=True,
    )

    # 1. Exit code 0 (already enforced by ``check=True``; explicit for clarity).
    assert result.returncode == 0, (
        f"stargraph run exited {result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )

    # 2. JSONL file exists and has >=2 events. Each line is a chained-log
    #    envelope; ``unwrap_audit_record`` dual-reads all on-disk shapes.
    #    The genesis record (seq 0) is chain bookkeeping, not a run event.
    assert log_file.exists(), f"log file missing: {log_file}"
    records = [
        unwrap_audit_record(json.loads(line))
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    events = [r for r in records if r.get("type") != GENESIS_RECORD_TYPE]
    assert len(events) >= 2, f"expected >=2 events, got {len(events)}: {events!r}"

    # 3. At least one TransitionEvent + one ResultEvent (by ``type`` discriminator).
    types = [ev.get("type") for ev in events]
    assert "transition" in types, f"no TransitionEvent in {types!r}"
    assert "result" in types, f"no ResultEvent in {types!r}"

    # 4. SQLite checkpoints table has >=1 row. Connection is read-only via
    #    URI mode so the assertion never mutates the on-disk database.
    assert checkpoint_db.exists(), f"checkpoint db missing: {checkpoint_db}"
    uri = f"file:{checkpoint_db}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        (rows,) = conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()
    finally:
        conn.close()
    assert rows >= 1, f"checkpoints table empty: rows={rows}"

    # Reassure noisy CI logs that the gate fired -- mirrors the smoke test's
    # all-green sentinel pattern.
    sys.stdout.write("POC MILESTONE PASS\n")
