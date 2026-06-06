# SPDX-License-Identifier: Apache-2.0
"""Unit: ``stargraph inspect <run_id>`` timeline + state + fact-diff (task 4.7).

Per design §3.1 (``inspect.py`` row), the CLI exposes three read-only
views over a fixtured Checkpointer + JSONL audit log:

* default -- timeline (``step transition node tool_calls rules`` per row)
* ``--step N`` -- IR-canonical state dict at step N
* ``--diff N M`` -- CLIPS fact delta between steps N and M

Read-only invariant (FR-26): after ``inspect`` runs, the Checkpointer
SQLite file and the JSONL audit log MUST be byte-identical to their
pre-invocation state.

Task 4.7 RED tests (TDD):

1. ``test_timeline_view_renders_steps`` -- timeline output contains
   expected ``step=N node=...`` for each fixtured checkpoint.
2. ``test_inspect_is_read_only`` -- bytes-of-disk are byte-identical
   pre/post invocation.
3. ``test_step_view_returns_state_at_step`` -- ``--step 1`` prints the
   IR-canonical state dict at step 1.
4. ``test_diff_view_returns_fact_delta`` -- ``--diff 0 1`` prints
   ``added``/``removed`` keys with the expected fact lists.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003 -- runtime use by pytest fixture type

import pytest
from typer.testing import CliRunner

from stargraph.checkpoint.protocol import Checkpoint
from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.cli import app

_runner = CliRunner()


def _digest(p: Path) -> str:
    """sha256 of the file contents (or empty string when missing)."""
    if not p.exists():
        return ""
    return hashlib.sha256(p.read_bytes()).hexdigest()


async def _seed_checkpoints(db_path: Path, run_id: str) -> None:
    """Write three minimal checkpoints (steps 0/1/2) for ``run_id``."""
    cp = SQLiteCheckpointer(db_path)
    await cp.bootstrap()
    base_ts = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)
    fact_first = '(stargraph.evidence (text "first"))'
    fact_second = '(stargraph.evidence (text "second"))'
    for step, node, facts in (
        (0, "node_a", [fact_first]),
        (1, "node_b", [fact_first, fact_second]),
        (2, "node_b", [fact_second]),
    ):
        await cp.write(
            Checkpoint(
                run_id=run_id,
                step=step,
                branch_id=None,
                parent_step_idx=step - 1 if step > 0 else None,
                graph_hash="g_hash_unit",
                runtime_hash="rt_hash_unit",
                state={"step_count": step, "last": node},
                clips_facts=facts,
                last_node=node,
                next_action=None,
                timestamp=base_ts,
                parent_run_id=None,
                side_effects_hash="se_hash",
            )
        )
    await cp.close() if hasattr(cp, "close") else None


def _seed_audit_log(jsonl_path: Path, run_id: str) -> None:
    """Write a small JSONL audit log with two events per step (tool_call + transition)."""
    lines: list[str] = []
    for step in (0, 1, 2):
        lines.append(
            json.dumps(
                {
                    "type": "tool_call",
                    "run_id": run_id,
                    "step": step,
                    "ts": "2026-04-30T12:00:00+00:00",
                    "tool_name": f"tool_step_{step}",
                    "namespace": "test",
                    "args": {},
                    "call_id": f"c{step}",
                }
            )
        )
        lines.append(
            json.dumps(
                {
                    "type": "transition",
                    "run_id": run_id,
                    "step": step,
                    "ts": "2026-04-30T12:00:00+00:00",
                    "from_node": "prev",
                    "to_node": f"node_{step}",
                    "rule_id": f"rule_step_{step}",
                    "reason": "rule:fired",
                }
            )
        )
    jsonl_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def fixtured_run(tmp_path: Path) -> tuple[Path, Path, str]:
    """Write a fresh SQLite checkpointer + JSONL audit log; return (db, jsonl, run_id)."""
    run_id = "run-unit-inspect-001"
    db_path = tmp_path / "run.sqlite"
    jsonl_path = tmp_path / "audit.jsonl"
    asyncio.run(_seed_checkpoints(db_path, run_id))
    _seed_audit_log(jsonl_path, run_id)
    return db_path, jsonl_path, run_id


@pytest.mark.unit
def test_timeline_view_renders_steps(fixtured_run: tuple[Path, Path, str]) -> None:
    """Default ``stargraph inspect <run_id>`` renders one timeline row per checkpoint."""
    db_path, jsonl_path, run_id = fixtured_run
    result = _runner.invoke(
        app,
        [
            "inspect",
            run_id,
            "--db",
            str(db_path),
            "--log-file",
            str(jsonl_path),
        ],
    )
    assert result.exit_code == 0, result.output + str(result.exception)
    out = result.output
    # Three checkpointed steps -> three timeline lines.
    assert "step=0" in out
    assert "step=1" in out
    assert "step=2" in out
    # Node ids preserved.
    assert "node=node_a" in out
    # Tool calls extracted from JSONL events.
    assert "tool_step_0" in out
    # Rule firings extracted from JSONL events.
    assert "rule_step_1" in out


@pytest.mark.unit
def test_inspect_is_read_only(fixtured_run: tuple[Path, Path, str]) -> None:
    """SQLite file + JSONL audit log are byte-identical post-inspect (FR-26)."""
    db_path, jsonl_path, run_id = fixtured_run
    db_before = _digest(db_path)
    jsonl_before = _digest(jsonl_path)
    result = _runner.invoke(
        app,
        [
            "inspect",
            run_id,
            "--db",
            str(db_path),
            "--log-file",
            str(jsonl_path),
        ],
    )
    assert result.exit_code == 0, result.output + str(result.exception)
    assert _digest(db_path) == db_before, "checkpointer DB mutated"
    # SQLite WAL/SHM sidecar files may be created by a *read*; the
    # canonical DB file's content must not change. The JSONL audit log
    # is only opened in ``"rb"`` mode by inspect so it must be exactly
    # the pre-invocation bytes.
    assert _digest(jsonl_path) == jsonl_before, "audit JSONL mutated"


@pytest.mark.unit
def test_step_view_returns_state_at_step(fixtured_run: tuple[Path, Path, str]) -> None:
    """``inspect <run_id> --step 1`` prints the state dict at step 1."""
    db_path, _, run_id = fixtured_run
    result = _runner.invoke(
        app,
        [
            "inspect",
            run_id,
            "--db",
            str(db_path),
            "--step",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output + str(result.exception)
    # The state dict at step 1 was {"step_count": 1, "last": "node_b"}.
    payload = json.loads(result.output.strip())
    assert payload == {"step_count": 1, "last": "node_b"}


@pytest.mark.unit
def test_diff_view_returns_fact_delta(fixtured_run: tuple[Path, Path, str]) -> None:
    """``inspect <run_id> --diff 0 1`` prints CLIPS fact additions / removals."""
    db_path, _, run_id = fixtured_run
    result = _runner.invoke(
        app,
        [
            "inspect",
            run_id,
            "--db",
            str(db_path),
            "--diff",
            "0",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output + str(result.exception)
    payload = json.loads(result.output.strip())
    # Step 0 had {first}; step 1 had {first, second} -> only "second" added.
    assert payload["added"] == ['(stargraph.evidence (text "second"))']
    assert payload["removed"] == []


@pytest.mark.unit
def test_inspect_help_mentions_timeline() -> None:
    """``stargraph inspect --help`` mentions the timeline view (verify spec)."""
    result = _runner.invoke(app, ["inspect", "--help"])
    assert result.exit_code == 0, result.output
    assert "timeline" in result.output.lower()
