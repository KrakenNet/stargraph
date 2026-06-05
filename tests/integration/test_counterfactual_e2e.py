# SPDX-License-Identifier: Apache-2.0
"""Counterfactual replay end-to-end smoke (FR-27, US-4, design §3.8.4).

Phase 5 task 5.7. Drives the full counterfactual surface:

1. Run the 6-node ``tests/fixtures/sample-graph-phase5.yaml`` graph
   end-to-end via ``harbor run`` (parallel/ML kinds scoped down -- see
   the fixture header comment for the linear ``echo + dspy + halt``
   topology).
2. Load the resulting :class:`~harbor.replay.history.RunHistory` from
   the on-disk SQLite checkpoint store; assert 6 checkpoints, one per
   node tick.
3. Build a :class:`~harbor.replay.counterfactual.CounterfactualMutation`
   and exercise the typed surface
   (:func:`~harbor.replay.counterfactual.derived_graph_hash`).
4. Construct a synthetic counterfactual :class:`RunHistory` that diverges
   from the original at step ``N`` (state mutation), then call
   :func:`harbor.replay.compare.compare` and assert the resulting
   :class:`RunDiff` has the expected step diffs.
5. Re-run ``harbor run`` against a fresh checkpoint DB and assert the
   modeled event log is byte-identical to the first run after stripping
   wall-clock fields the FR-28 shims do not cover (Temporal "cannot
   change the past" invariant -- the recorded original is durable).
6. Smoke ``harbor counterfactual --help`` and the full
   ``harbor counterfactual <graph> --step N --mutate mutation.yaml``
   subcommand against the same fixture; assert it prints the original
   and derived graph hashes and exits 0.

Per the task's CRITICAL CONSTRAINT 6, ``harbor.load_run()`` is not yet
a public surface; this test wires :class:`RunHistory` and
:mod:`harbor.replay.counterfactual` directly. When ``load_run`` lands
the test should swap to it without changing the assertions.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from fathom.chained_log import GENESIS_RECORD_TYPE

from harbor.audit.jsonl import unwrap_audit_record
from harbor.checkpoint import Checkpoint
from harbor.checkpoint.sqlite import SQLiteCheckpointer
from harbor.replay.compare import RunDiff, compare
from harbor.replay.counterfactual import CounterfactualMutation, derived_graph_hash
from harbor.replay.history import RunHistory

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
SAMPLE_GRAPH: Path = REPO_ROOT / "tests" / "fixtures" / "sample-graph-phase5.yaml"
MUTATION_YAML: Path = REPO_ROOT / "tests" / "fixtures" / "mutation.yaml"

_RUN_ID_RE: re.Pattern[str] = re.compile(r"run_id=(\S+)\s+status=(\S+)")


def _run_harbor(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["harbor", *args],
        capture_output=True,
        check=check,
        cwd=REPO_ROOT,
        text=True,
    )


def _harbor_run_to_artifacts(
    *,
    log_file: Path,
    checkpoint_db: Path,
) -> tuple[str, str]:
    """Invoke ``harbor run`` against the phase-5 fixture; return ``(run_id, status)``."""
    result = _run_harbor(
        "run",
        str(SAMPLE_GRAPH),
        "--log-file",
        str(log_file),
        "--checkpoint",
        str(checkpoint_db),
    )
    assert result.returncode == 0, result.stderr or result.stdout
    match = _RUN_ID_RE.search(result.stdout)
    assert match is not None, f"could not parse run_id from stdout: {result.stdout!r}"
    return match.group(1), match.group(2)


def _read_events(log_file: Path) -> list[dict[str, Any]]:
    """Unwrap chained-log envelopes into the modeled run events.

    ``unwrap_audit_record`` dual-reads all on-disk audit line shapes;
    the genesis record (seq 0) is chain bookkeeping, not a run event,
    so it is dropped.
    """
    records = [
        unwrap_audit_record(json.loads(line))
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [r for r in records if r.get("type") != GENESIS_RECORD_TYPE]


def _normalize_event(ev: dict[str, Any]) -> dict[str, Any]:
    """Strip wall-clock fields the FR-28 determinism shims do not cover.

    ``ts`` (event envelope) and ``run_duration_ms`` (terminal
    :class:`~harbor.runtime.events.ResultEvent`) come from
    :func:`datetime.now` / monotonic time inside the loop driver, not
    through the determinism shims; ``run_id``, ``call_id``, and the
    bus-side timestamps differ run-to-run because the engine assigns
    fresh UUIDv7s per invocation. Everything else (modeled payload,
    routing decisions, projected outputs) is durable.
    """
    ev = dict(ev)
    ev.pop("ts", None)
    ev.pop("run_id", None)
    ev.pop("call_id", None)
    if ev.get("type") == "result":
        ev.pop("run_duration_ms", None)
    return ev


def _load_history(run_id: str, checkpoint_db: Path) -> RunHistory:
    """Open the SQLite checkpointer and load the run's full :class:`RunHistory`."""

    async def _go() -> RunHistory:
        cp = SQLiteCheckpointer(checkpoint_db)
        await cp.bootstrap()
        try:
            return await RunHistory.load(run_id, checkpointer=cp)
        finally:
            await cp.close()

    return asyncio.run(_go())


def test_counterfactual_e2e_smoke(tmp_path: Path) -> None:
    """End-to-end smoke: original run + cf RunDiff + CLI counterfactual.

    Pins the FR-27 / US-4 contract end-to-end without depending on
    ``harbor.load_run`` (not yet a public surface; CONSTRAINT 6).
    """
    assert SAMPLE_GRAPH.exists(), f"missing fixture: {SAMPLE_GRAPH}"
    assert MUTATION_YAML.exists(), f"missing fixture: {MUTATION_YAML}"

    # ------------------------------------------------------------------ #
    # 1. Original run                                                    #
    # ------------------------------------------------------------------ #
    orig_log = tmp_path / "orig.jsonl"
    orig_db = tmp_path / "orig.sqlite"
    orig_run_id, orig_status = _harbor_run_to_artifacts(
        log_file=orig_log,
        checkpoint_db=orig_db,
    )
    assert orig_status == "done", orig_status
    orig_events = _read_events(orig_log)
    # 9 modeled events: 6 transitions + tool_call + tool_result + result
    # (see fixture header for the exact stream).
    assert len(orig_events) == 9, f"expected 9 events, got {len(orig_events)}: {orig_events!r}"
    types = [ev.get("type") for ev in orig_events]
    assert types.count("transition") == 6, types
    assert "tool_call" in types
    assert "tool_result" in types
    assert types[-1] == "result", types

    # ------------------------------------------------------------------ #
    # 2. RunHistory.load                                                 #
    # ------------------------------------------------------------------ #
    orig_history = _load_history(orig_run_id, orig_db)
    assert orig_history.run_id == orig_run_id
    # 6 checkpoints, one per node tick (n0..n5 -> 6 rows).
    assert len(orig_history.checkpoints) == 6, len(orig_history.checkpoints)
    last_nodes = [ckpt.last_node for ckpt in orig_history.checkpoints]
    assert last_nodes == ["node_a", "node_b", "node_c", "node_d", "node_e", "node_f"], last_nodes

    # ------------------------------------------------------------------ #
    # 3. Build a CounterfactualMutation + derived hash                   #
    # ------------------------------------------------------------------ #
    mutation = CounterfactualMutation(
        state_overrides={"message": "counterfactual-message"},
    )
    orig_graph_hash = orig_history.checkpoints[0].graph_hash
    derived = derived_graph_hash(orig_graph_hash, mutation)
    assert derived != orig_graph_hash, (
        "derived hash collided with original -- domain separation broken"
    )
    assert len(derived) == 64, derived

    # ------------------------------------------------------------------ #
    # 4. Synthetic cf RunHistory + RunDiff                               #
    # ------------------------------------------------------------------ #
    # Fork at step N=3 (node_d): every checkpoint at or after step 3
    # carries the mutation; steps 0..2 stay byte-identical with the
    # original (Temporal "cannot change the past").
    cf_step = 3
    # Per design §3.8.4, the cf is a separate run with its own (derived)
    # graph_hash on every checkpoint; only state is shared with the
    # original at steps < cf_step. After cf_step the mutation overlay
    # is applied to state. Side-effects hash + last_node are preserved
    # from the original (FR-27 cf replay re-executes the same node IDs).
    cf_checkpoints: list[Checkpoint] = []
    for ckpt in orig_history.checkpoints:
        overlay = mutation if ckpt.step >= cf_step else CounterfactualMutation()
        new_state = dict(ckpt.state)
        if overlay.state_overrides:
            new_state.update(overlay.state_overrides)
        cf_checkpoints.append(
            Checkpoint(
                run_id="cf-" + orig_run_id,
                step=ckpt.step,
                branch_id=ckpt.branch_id,
                parent_step_idx=ckpt.parent_step_idx,
                graph_hash=derived_graph_hash(ckpt.graph_hash, mutation),
                runtime_hash=ckpt.runtime_hash,
                state=new_state,
                clips_facts=list(ckpt.clips_facts),
                last_node=ckpt.last_node,
                next_action=ckpt.next_action,
                timestamp=ckpt.timestamp,
                parent_run_id=ckpt.run_id,
                side_effects_hash=ckpt.side_effects_hash,
            )
        )

    cf_history = RunHistory(
        run_id="cf-" + orig_run_id,
        checkpoints=cf_checkpoints,
        audit_path=None,
    )

    diff: RunDiff = compare(orig_history, cf_history)
    # The cf branch diverges at step 3, 4, 5 (state.message overridden),
    # so the RunDiff should list exactly those three steps.
    diverged_steps = sorted(s.step for s in diff.steps)
    assert diverged_steps == [3, 4, 5], (
        f"expected divergences at steps 3,4,5; got {diverged_steps!r} diff={diff.model_dump()!r}"
    )
    # Every divergence is on state (per StepDiff.diverged_at precedence
    # in compare.py: state > node_output > edge_taken > side_effect).
    for step_diff in diff.steps:
        assert step_diff.diverged_at == "state", step_diff.model_dump()
        # The JSONPatch op-list rewrites ``message`` -- one op per diff.
        assert any(op.get("path") == "/message" for op in step_diff.state_diff), (
            step_diff.state_diff
        )
    # Final-state diff also carries the message override.
    assert any(op.get("path") == "/message" for op in diff.final_state_diff), diff.final_state_diff
    # cf-derived graph hash propagates into the RunDiff envelope.
    assert diff.derived_hash == derived

    # ------------------------------------------------------------------ #
    # 5. Original event log byte-identical across runs                   #
    # ------------------------------------------------------------------ #
    # Re-run the same fixture; modeled events (timestamps + run_id
    # stripped) must be byte-identical -- the durable contract is the
    # *modeled* event payload, not the per-run UUIDs/timestamps.
    rerun_log = tmp_path / "rerun.jsonl"
    rerun_db = tmp_path / "rerun.sqlite"
    _, rerun_status = _harbor_run_to_artifacts(
        log_file=rerun_log,
        checkpoint_db=rerun_db,
    )
    assert rerun_status == "done"
    rerun_events = _read_events(rerun_log)
    assert len(rerun_events) == len(orig_events), (
        f"event count mismatch: orig={len(orig_events)} rerun={len(rerun_events)}"
    )
    orig_normalized = [_normalize_event(ev) for ev in orig_events]
    rerun_normalized = [_normalize_event(ev) for ev in rerun_events]
    assert orig_normalized == rerun_normalized, (
        "event payloads diverged between runs (post-strip): "
        f"orig={orig_normalized!r}\nrerun={rerun_normalized!r}"
    )

    # ------------------------------------------------------------------ #
    # 6. CLI ``harbor counterfactual``                                   #
    # ------------------------------------------------------------------ #
    help_result = _run_harbor("counterfactual", "--help")
    assert help_result.returncode == 0, help_result.stderr
    assert "--step" in help_result.stdout
    assert "--mutate" in help_result.stdout

    cli_cf = _run_harbor(
        "counterfactual",
        str(SAMPLE_GRAPH),
        "--step",
        str(cf_step),
        "--mutate",
        str(MUTATION_YAML),
    )
    assert cli_cf.returncode == 0, cli_cf.stderr or cli_cf.stdout
    out = cli_cf.stdout
    assert f"original_graph_hash={orig_graph_hash}" in out, out
    assert f"cf_step={cf_step}" in out, out
    # Domain-separated derived hash differs from the original.
    parsed: dict[str, str] = {
        k: v for k, v in (line.split("=", 1) for line in out.strip().splitlines() if "=" in line)
    }
    assert parsed["original_graph_hash"] != parsed["derived_graph_hash"]
    assert len(parsed["derived_graph_hash"]) == 64
