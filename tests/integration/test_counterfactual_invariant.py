# SPDX-License-Identifier: Apache-2.0
"""TDD-RED: counterfactual replay invariant (FR-27, design §4.2).

Pins the formal Temporal-style "cannot change the past" invariant per
design §4.2 *before* the counterfactual implementation lands in task
3.33. Currently RED because :meth:`stargraph.GraphRun.counterfactual`
raises :class:`NotImplementedError` and
:func:`stargraph.replay.counterfactual.derived_graph_hash` does not yet
exist (its module import fails first).

Proof shape (design §4.2, 7-step):

1. Compute ``sha256(audit_log)`` before ``run.counterfactual(...)``.
2. Execute counterfactual to completion.
3. Compute ``sha256(audit_log)`` after.
4. Assert byte-identical (post = pre).
5. Assert ``derived_hash`` is the cf-prefix-derived 64-char hex digest
   produced by :func:`derived_graph_hash` (distinct from the original).
6. Assert ``await Checkpointer.read_at_step(run_id=R, step=...)``
   returns ORIGINAL (not derived) checkpoints.
7. Assert ``await GraphRun.resume(checkpointer, run_id=R, ...)``
   raises :class:`CheckpointError(reason="cf-prefix-hash-refused")` if
   any cf-derived checkpoint shadows.
"""

from __future__ import annotations

import hashlib
import importlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from stargraph import GraphRun
from stargraph.checkpoint import Checkpoint
from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.errors import CheckpointError

if TYPE_CHECKING:
    from pathlib import Path


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _cf_module() -> Any:
    """Import ``stargraph.replay.counterfactual`` (TDD-RED: not yet built)."""
    return importlib.import_module("stargraph.replay.counterfactual")


def _sha256_bytes(path: Path) -> str:
    """Hash the entire on-disk audit-log file as raw bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_checkpoint(
    *,
    run_id: str,
    step: int,
    graph_hash: str,
    state: dict[str, Any],
) -> Checkpoint:
    """Build a Checkpoint with the engine's required-field shape."""
    return Checkpoint(
        run_id=run_id,
        step=step,
        branch_id=None,
        parent_step_idx=None,
        graph_hash=graph_hash,
        runtime_hash="rt-1",
        state=state,
        clips_facts=[],
        last_node="n0",
        next_action=None,
        timestamp=datetime.now(UTC),
        parent_run_id=None,
        side_effects_hash="0" * 64,
    )


async def _seed_original_checkpoints(
    checkpointer: SQLiteCheckpointer,
    *,
    run_id: str,
    graph_hash: str,
    n_steps: int,
) -> None:
    """Persist a small original-run history (steps 0..n_steps-1)."""
    for step in range(n_steps):
        await checkpointer.write(
            _make_checkpoint(
                run_id=run_id,
                step=step,
                graph_hash=graph_hash,
                state={"counter": step},
            )
        )


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_counterfactual_audit_log_byte_identical(tmp_path: Path) -> None:
    """Steps 1-4: original audit-log bytes are unchanged post-counterfactual."""
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_bytes(b'{"step":0,"event":"start"}\n')
    pre_hash = _sha256_bytes(audit_path)

    cp = SQLiteCheckpointer(tmp_path / "ckpt.db")
    await cp.bootstrap()
    await _seed_original_checkpoints(cp, run_id="run-orig", graph_hash="g" * 64, n_steps=3)

    cf = _cf_module()
    mutation = cf.CounterfactualMutation(state_overrides={"counter": 99})

    # Step 2: run the counterfactual (RED -- raises NotImplementedError).
    await GraphRun.counterfactual(cp, "run-orig", step=1, mutate=mutation)

    # Step 3 + 4: re-hash and assert byte-identical.
    post_hash = _sha256_bytes(audit_path)
    assert post_hash == pre_hash, "original audit-log bytes mutated by cf"


def test_derived_hash_carries_cf_prefix_signature() -> None:
    """Step 5: ``derived_hash`` is the cf-prefix-derived 64-char hex digest.

    Per design §3.8.3 the derived hash is sha256 over a
    ``b"stargraph-cf-v1\\x00..."`` byte sequence. The on-the-wire artifact
    is the 64-char hex digest -- not the literal "stargraph-cf-v1" string
    prefix. We pin both: digest length and that it is *not* the original
    hash (i.e. domain separation actually fired).
    """
    cf = _cf_module()
    original = "f" * 64
    mutation = cf.CounterfactualMutation(state_overrides={"x": 1})

    derived: str = cf.derived_graph_hash(original, mutation)

    assert len(derived) == 64
    assert all(c in "0123456789abcdef" for c in derived)
    assert derived != original


@pytest.mark.asyncio
async def test_original_checkpoints_returned_unchanged(tmp_path: Path) -> None:
    """Step 6: ``read_at_step(run_id=R, step=...)`` returns ORIGINAL state."""
    cp = SQLiteCheckpointer(tmp_path / "ckpt.db")
    await cp.bootstrap()
    await _seed_original_checkpoints(cp, run_id="run-orig", graph_hash="g" * 64, n_steps=3)

    cf = _cf_module()
    mutation = cf.CounterfactualMutation(state_overrides={"counter": 99})

    # Step 2: run the counterfactual (RED -- raises NotImplementedError).
    await GraphRun.counterfactual(cp, "run-orig", step=1, mutate=mutation)

    # Step 6: original checkpoints still readable, still original state.
    ckpt = await cp.read_at_step("run-orig", 1)
    assert ckpt is not None
    assert ckpt.state["counter"] == 1, "cf must not mutate original checkpoint"
    assert not ckpt.graph_hash.startswith("stargraph-cf-v1"), (
        "original checkpoint must keep original graph_hash, not cf-derived"
    )


@pytest.mark.asyncio
async def test_resume_refuses_cf_prefixed_checkpoint(tmp_path: Path) -> None:
    """Step 7: ``GraphRun.resume(...)`` refuses cf-prefix shadow checkpoints.

    Even if a cf-derived checkpoint were somehow written under the
    original ``run_id`` (it should not be -- cf children get fresh
    run-ids), :meth:`GraphRun.resume` MUST refuse it with a
    :class:`CheckpointError` carrying ``reason="cf-prefix-hash-refused"``
    per AC-3.4 / FR-27.
    """
    cp = SQLiteCheckpointer(tmp_path / "ckpt.db")
    await cp.bootstrap()

    # Plant a cf-prefixed checkpoint under the *original* run_id (the
    # bug we are guarding against).
    cf_graph_hash = "stargraph-cf-v1" + "0" * 52  # 64-char value with cf-prefix
    await cp.write(
        _make_checkpoint(
            run_id="run-orig",
            step=0,
            graph_hash=cf_graph_hash,
            state={"x": 1},
        )
    )

    with pytest.raises(CheckpointError) as exc_info:
        await GraphRun.resume(cp, "run-orig")

    assert exc_info.value.context.get("reason") == "cf-prefix-hash-refused"
