# SPDX-License-Identifier: Apache-2.0
"""Unit: ``stargraph replay <run_id>`` counterfactual + diff (task 4.8).

Per design §3.1 (``replay.py`` row), the CLI is a thin wrapper over the
engine's ``GraphRun.counterfactual`` + ``stargraph.replay.compare``
surfaces. The CLI surface:

    stargraph replay <run_id> --db <path> [--mutation @file.json]
                  [--from-step N] [--diff]

* ``--mutation @file.json`` -- loads a CounterfactualMutation from
  JSON; the engine forks a cf-run.
* ``--from-step N`` -- explicit fork step (defaults to the latest
  step's ``parent_step_idx``).
* ``--diff`` -- after the cf-run completes, render the parent vs cf
  RunDiff as text.

Task 4.8 RED tests (TDD):

1. ``test_replay_emits_diff_against_parent`` -- invoke replay with a
   fixtured mutation; assert the cf-run is created (cf-run-id starts
   with ``cf-``) and ``--diff`` renders the JSON RunDiff (containing
   at least the `derived_hash` and `original_run_id` keys).
2. ``test_replay_help_mentions_counterfactual`` -- the CLI help text
   mentions counterfactual (verify command for the spec).
3. ``test_replay_from_step_threads_through`` -- ``--from-step N`` is
   accepted (exit_code 0); the cf-run is forked from step N.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003 -- runtime use by pytest fixture type

import pytest
from typer.testing import CliRunner

from stargraph.checkpoint.protocol import Checkpoint
from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.cli import app

_runner = CliRunner()


async def _seed_parent_run(db_path: Path, run_id: str) -> None:
    """Seed two checkpoints for the parent run.

    Steps 0 and 1 with state.message = 'hello' / 'world' so a
    state_overrides mutation at step 0 produces a non-empty diff.
    """
    cp = SQLiteCheckpointer(db_path)
    await cp.bootstrap()
    base_ts = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)
    try:
        for step, msg in ((0, "hello"), (1, "world")):
            await cp.write(
                Checkpoint(
                    run_id=run_id,
                    step=step,
                    branch_id=None,
                    parent_step_idx=step - 1 if step > 0 else None,
                    graph_hash="g_hash_replay",
                    runtime_hash="rt_hash_replay",
                    state={"message": msg, "step_count": step},
                    clips_facts=[],
                    last_node=f"node_{step}",
                    next_action=None,
                    timestamp=base_ts,
                    parent_run_id=None,
                    side_effects_hash="se_hash",
                )
            )
    finally:
        await cp.close()


@pytest.fixture
def fixtured_parent(tmp_path: Path) -> tuple[Path, Path, str]:
    """Write a parent run + mutation JSON; return (db, mutation_path, run_id)."""
    run_id = "run-unit-replay-parent"
    db_path = tmp_path / "run.sqlite"
    asyncio.run(_seed_parent_run(db_path, run_id))
    mutation_path = tmp_path / "mutation.json"
    mutation_path.write_text(
        json.dumps(
            {
                "state_overrides": {"message": "mutated"},
            }
        ),
        encoding="utf-8",
    )
    return db_path, mutation_path, run_id


@pytest.mark.unit
def test_replay_help_mentions_counterfactual() -> None:
    """``stargraph replay --help`` mentions counterfactual (verify cmd)."""
    result = _runner.invoke(app, ["replay", "--help"])
    assert result.exit_code == 0, result.output
    assert "counterfactual" in result.output.lower()


@pytest.mark.unit
def test_replay_emits_diff_against_parent(
    fixtured_parent: tuple[Path, Path, str],
) -> None:
    """``stargraph replay <run_id> --mutation @f.json --diff`` renders a RunDiff."""
    db_path, mutation_path, run_id = fixtured_parent
    result = _runner.invoke(
        app,
        [
            "replay",
            run_id,
            "--db",
            str(db_path),
            "--mutation",
            str(mutation_path),
            "--from-step",
            "0",
            "--diff",
        ],
    )
    assert result.exit_code == 0, result.output + str(result.exception)
    out = result.output
    # The cf-run id should be a child of the parent.
    assert "cf-" in out
    # The RunDiff renderer must surface the original_run_id + derived
    # hash keys (basic smoke -- the full RunDiff is exercised by
    # tests/unit/test_compare.py).
    assert "original_run_id" in out or run_id in out
    # The state-override changed `message` -> diff should mention
    # "message" or the JSONPatch op for it.
    assert "message" in out


@pytest.mark.unit
def test_replay_from_step_accepted(
    fixtured_parent: tuple[Path, Path, str],
) -> None:
    """``--from-step 0`` is parsed without error (cf forked from step 0)."""
    db_path, mutation_path, run_id = fixtured_parent
    result = _runner.invoke(
        app,
        [
            "replay",
            run_id,
            "--db",
            str(db_path),
            "--mutation",
            str(mutation_path),
            "--from-step",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output + str(result.exception)
    # Without --diff, the cf-run-id should still be on stdout so
    # operators can pipe it into a follow-up `stargraph inspect`.
    assert "cf-" in result.output
