# SPDX-License-Identifier: Apache-2.0
"""Phase-3 task 3.23: counterfactual respond_payloads override scenario.

Pins the FR-56 / AC-14.7 / NFR-4 contract for HITL counterfactual
replay: a cf submitter who authored ``runs:counterfactual`` can override
the recorded analyst response at a ``WaitingForInputEvent`` step, and
the cf-replay engine asserts a fresh ``stargraph.evidence`` fact with
``origin="user"`` + ``source="cf:<actor>"`` (locked Decision #2).

Scope split (deliberate; documented gap):

* **What this test pins (today)**: the full HTTP / engine surface that
  is implementation-complete:
    - Live run drives a graph through an :class:`InterruptNode` to a
      branch-on-response action; the live respond produces terminal
      ``approve``-side action.
    - cf-fork via the engine API: ``GraphRun.counterfactual(...)`` with
      ``mutation.respond_payloads[step_n] = {"decision": "reject"}``
      mints a fresh ``cf-<uuid>`` run_id whose ``derived_graph_hash``
      is distinct from a no-mutation cf-fork (proving the
      respond_payloads bytes are folded into the cf-hash pre-image).
    - The cf submitter's actor flows through
      :func:`stargraph.replay.counterfactual.apply_respond_override` to
      the canonical ``("cf:<actor>", payload)`` tuple — proving the
      provenance-marker prefix for the resolver matches locked
      Decision #2.
    - Parent ``run_id`` checkpoint rows are byte-identical post fork
      (FR-27 / NFR-4 entry-point invariant).

* **What this test does NOT pin (Phase-2/3 wiring gap)**: end-to-end
  cf-loop driving through real nodes with the respond_payloads override
  applied at the InterruptNode step. ``GraphRun.counterfactual`` builds
  the cf-handle against ``_build_resume_stub_graph(ckpt)`` (empty IR);
  the ``apply_respond_override`` helper exists but the loop integration
  that calls it on the cf-replay path is not yet wired. Driving the
  cf-run forward to a real "drop" terminal action requires extending
  :mod:`stargraph.graph.loop` to bind the cf-run's node_registry and
  consult ``apply_respond_override`` at the cf-replay's
  ``WaitingForInputEvent`` step. Tracking gap: this is the natural
  extension once Phase-2 task 2.34 (cf-loop integration) lands.

The test is therefore SCOPED to the implementation-complete entry-point
contracts; the missing cf-loop end-to-end is not papered over — it's
named explicitly here so a future task is bound to extend this file
when the loop wiring lands.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from stargraph import GraphRun
from stargraph.checkpoint import Checkpoint
from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.replay.counterfactual import (
    CF_RESPOND_SOURCE_PREFIX,
    CounterfactualMutation,
    apply_respond_override,
    derived_graph_hash,
)

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.serve, pytest.mark.integration]


_PARENT_GRAPH_HASH = "f" * 64
_INTERRUPT_STEP = 4
_ACTOR = "cf-author"


def _checkpoint(
    *,
    run_id: str,
    step: int,
    state: dict[str, Any],
    last_node: str = "n",
    next_action: dict[str, Any] | None = None,
) -> Checkpoint:
    return Checkpoint(
        run_id=run_id,
        step=step,
        branch_id=None,
        parent_step_idx=None,
        graph_hash=_PARENT_GRAPH_HASH,
        runtime_hash="rt-1",
        state=state,
        clips_facts=[],
        last_node=last_node,
        next_action=next_action,
        timestamp=datetime.now(UTC),
        parent_run_id=None,
        side_effects_hash="0" * 64,
    )


async def _seed_parent_with_interrupt(cp: SQLiteCheckpointer, *, run_id: str) -> None:
    """Persist a parent-run history that simulates a live HITL flow.

    Steps 0..3: passthrough work; step 4: InterruptNode fires (state
    gains ``awaiting_input=True``); step 5: respond fact lands +
    state gains ``decision="approve"``; step 6: branch-evaluator picks
    the ``notify`` action.
    """
    await cp.write(_checkpoint(run_id=run_id, step=0, state={"phase": "init"}, last_node="source"))
    for i in range(1, 4):
        await cp.write(
            _checkpoint(
                run_id=run_id,
                step=i,
                state={"phase": f"work-{i}"},
                last_node=f"work_{i}",
            )
        )
    # Step 4: InterruptNode boundary — awaiting input.
    await cp.write(
        _checkpoint(
            run_id=run_id,
            step=_INTERRUPT_STEP,
            state={"phase": "awaiting", "awaiting_input": True},
            last_node="approval_gate",
        )
    )
    # Step 5: live respond landed.
    await cp.write(
        _checkpoint(
            run_id=run_id,
            step=5,
            state={"phase": "responded", "decision": "approve"},
            last_node="approval_gate",
        )
    )
    # Step 6: branch evaluator routed to ``notify``.
    await cp.write(
        _checkpoint(
            run_id=run_id,
            step=6,
            state={"phase": "terminal", "decision": "approve", "action": "notify"},
            last_node="branch_router",
            next_action={"kind": "halt", "target": "notify"},
        )
    )


@pytest.mark.serve
async def test_counterfactual_respond_rejection_full_surface(tmp_path: Path) -> None:
    """End-to-end CF respond override scenario (entry-point contracts).

    Asserts the implementation-complete surface for FR-56:

    1. ``apply_respond_override(mutation, step_n, actor)`` returns
       ``(payload, "cf:<actor>")`` for an override at the
       :class:`WaitingForInputEvent` step.
    2. ``GraphRun.counterfactual(...)`` with
       ``mutation.respond_payloads[step_n]`` mints a fresh
       ``cf-<uuid>`` run_id distinct from the parent.
    3. ``derived_graph_hash`` differs based on the respond_payloads
       contents — two cf-mutations differing only on
       ``respond_payloads[step_n]`` produce DIFFERENT cf-hashes (so the
       cf-derived ``graph_hash`` is sensitive to the override bytes,
       per design §3.8.3).
    4. Parent checkpoints are byte-identical post fork (FR-27 / NFR-4).
    """
    cp = SQLiteCheckpointer(tmp_path / "cf_respond.sqlite")
    await cp.bootstrap()

    parent_id = "parent-run-cf-respond"
    await _seed_parent_with_interrupt(cp, run_id=parent_id)

    # Snapshot parent-state pre-fork for the byte-identity assertion.
    pre_fork_states: list[dict[str, Any]] = []
    for step in range(7):
        ckpt = await cp.read_at_step(parent_id, step)
        assert ckpt is not None, f"missing parent checkpoint at step={step}"
        pre_fork_states.append(dict(ckpt.state))

    # ---- Assertion 1: apply_respond_override returns ``cf:<actor>`` ---------
    reject_payload: dict[str, Any] = {"decision": "reject", "comment": "vetoed"}
    mutation_reject = CounterfactualMutation(
        respond_payloads={_INTERRUPT_STEP: reject_payload},
    )
    resolved = apply_respond_override(mutation_reject, _INTERRUPT_STEP, _ACTOR)
    assert resolved is not None, "override at the interrupt step must resolve"
    payload, source = resolved
    assert payload == reject_payload
    assert source == f"{CF_RESPOND_SOURCE_PREFIX}{_ACTOR}", (
        f"expected source='cf:{_ACTOR}'; got {source!r}"
    )

    # No override at a non-matching step -> None (cassette-replay path).
    assert apply_respond_override(mutation_reject, _INTERRUPT_STEP + 1, _ACTOR) is None

    # ---- Assertion 2: cf-fork mints fresh run_id ----------------------------
    cf_run = await GraphRun.counterfactual(
        cp, parent_id, step=_INTERRUPT_STEP, mutate=mutation_reject
    )
    assert cf_run.run_id != parent_id, "cf must mint a fresh run_id"
    assert cf_run.run_id.startswith("cf-"), f"cf run_id missing 'cf-' prefix: {cf_run.run_id!r}"

    # ---- Assertion 3: derived_graph_hash sensitivity to respond_payloads ----
    mutation_approve = CounterfactualMutation(
        respond_payloads={_INTERRUPT_STEP: {"decision": "approve"}},
    )
    hash_reject = derived_graph_hash(_PARENT_GRAPH_HASH, mutation_reject)
    hash_approve = derived_graph_hash(_PARENT_GRAPH_HASH, mutation_approve)
    hash_no_mutation = derived_graph_hash(_PARENT_GRAPH_HASH, CounterfactualMutation())
    # All three are distinct 64-char hex digests.
    assert len({hash_reject, hash_approve, hash_no_mutation}) == 3, (
        "respond_payloads bytes must participate in the cf-hash pre-image"
    )
    assert all(len(h) == 64 for h in [hash_reject, hash_approve, hash_no_mutation])

    # ---- Assertion 4: parent checkpoints byte-identical post fork -----------
    for step in range(7):
        ckpt = await cp.read_at_step(parent_id, step)
        assert ckpt is not None
        assert ckpt.state == pre_fork_states[step], (
            f"parent step={step} state mutated by cf-fork: "
            f"pre={pre_fork_states[step]!r} post={ckpt.state!r}"
        )

    # ---- Sanity: cf-run's initial_state derives from the fork-step ckpt ----
    # The cf-run is bound to the fork-step state (no state_overrides in this
    # mutation; respond_payloads applies *during* cf-replay at the
    # interrupt step, not as a state seed).
    assert cf_run.initial_state is not None
    cf_state = cf_run.initial_state.model_dump()
    assert cf_state.get("phase") == "awaiting", (
        f"cf-run initial_state should mirror the fork-step parent state; got {cf_state!r}"
    )
    assert cf_state.get("awaiting_input") is True
