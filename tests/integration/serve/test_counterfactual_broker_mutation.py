# SPDX-License-Identifier: Apache-2.0
"""Phase-3 task 3.24: counterfactual broker-mutation scenario.

Pins the FR-56 / AC-11.3 / NFR-4 contract for cf-replay with a
node-output override at a Nautilus-broker step: the override changes
the broker's recorded output; downstream re-rank → re-summarize →
CLIPS-route nodes pick a different terminal action.

Scope split (deliberate; documented gap, mirrors task 3.23):

* **What this test pins (today)**: the implementation-complete cf-fork
  + derived-hash + parent-isolation surface for a Broker-style
  mutation scenario:
    - cf-fork via :meth:`GraphRun.counterfactual` with
      ``mutation.node_output_overrides[node_id]`` mints a fresh
      ``cf-<uuid>`` run_id distinct from the parent.
    - ``derived_graph_hash`` is sensitive to ``node_output_overrides``
      bytes — two cf-mutations differing only on the broker-output
      override produce DIFFERENT cf-hashes (per design §3.8.3 the
      override participates in the JCS-canonicalized pre-image).
    - Parent ``run_id`` checkpoint rows are byte-identical post fork
      (FR-27 / NFR-4 entry-point invariant).
    - The cf-run's initial_state derives from the fork-step parent
      checkpoint (so upstream nodes are bit-identical to the parent
      at the moment cf-replay forks, NFR-4 upstream invariant).

* **What this test does NOT pin (Phase-2/3 wiring gap)**: end-to-end
  cf-loop driving through real ML/DSPy/CLIPS nodes with the
  ``node_output_overrides`` consumed at the BrokerNode step on
  cf-replay. ``GraphRun.counterfactual`` builds the cf-handle against
  ``_build_resume_stub_graph(ckpt)`` (empty IR) — driving the real
  fork forward to a re-ranked → re-summarized → CLIPS-routed
  terminal action requires the cf-loop integration that lands in
  Phase-2 task 2.34. Tracking gap: this is the same surface as task
  3.23 (HITL respond override) — both block on the same loop wiring.

Spec-vs-implementation drift (logged as a learning): the task spec
references ``mutation.tool_outputs`` but the implemented field on
:class:`stargraph.replay.counterfactual.CounterfactualMutation` is
``node_output_overrides``. The implementation field is the source of
truth (the mutation model is shipped + validated; the spec wording is
a holdover from an earlier draft).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from stargraph import GraphRun
from stargraph.checkpoint import Checkpoint
from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.replay.counterfactual import (
    CounterfactualMutation,
    derived_graph_hash,
)

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.serve, pytest.mark.integration]


_PARENT_GRAPH_HASH = "a" * 64
_BROKER_NODE_ID = "broker_request"
_BROKER_STEP = 2


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


async def _seed_parent_with_broker(cp: SQLiteCheckpointer, *, run_id: str) -> None:
    """Persist a parent-run history that simulates the CVE-triage broker pipeline.

    Steps 0..1: pre-broker setup. Step 2: BrokerNode emits canned
    ``BrokerResponse(sources_queried=["A","B"], data={...})``. Step 3:
    ML-rank. Step 4: DSPy-summarize. Step 5: CLIPS-route picks
    ``action=X``.
    """
    await cp.write(_checkpoint(run_id=run_id, step=0, state={"phase": "init"}, last_node="source"))
    await cp.write(
        _checkpoint(
            run_id=run_id,
            step=1,
            state={"phase": "preflight"},
            last_node="enrich",
        )
    )
    # Step 2: BrokerNode boundary.
    await cp.write(
        _checkpoint(
            run_id=run_id,
            step=_BROKER_STEP,
            state={
                "phase": "broker",
                "sources_queried": ["A", "B"],
                "broker_data": {"signals": ["s-A", "s-B"]},
            },
            last_node=_BROKER_NODE_ID,
        )
    )
    # Step 3: ML re-rank consumes broker data.
    await cp.write(
        _checkpoint(
            run_id=run_id,
            step=3,
            state={"phase": "ranked", "ranking": ["s-A", "s-B"]},
            last_node="ml_score",
        )
    )
    # Step 4: DSPy summary.
    await cp.write(
        _checkpoint(
            run_id=run_id,
            step=4,
            state={"phase": "summarized", "summary": "based on A+B signals"},
            last_node="summarize",
        )
    )
    # Step 5: CLIPS route picks action X.
    await cp.write(
        _checkpoint(
            run_id=run_id,
            step=5,
            state={
                "phase": "terminal",
                "action": "X",
                "summary": "based on A+B signals",
            },
            last_node="clips_route",
            next_action={"kind": "halt", "target": "action_X"},
        )
    )


@pytest.mark.serve
async def test_counterfactual_broker_mutation_full_surface(tmp_path: Path) -> None:
    """Cf-fork with node_output_overrides at BrokerNode step (entry-point contracts).

    Asserts the implementation-complete surface:

    1. cf-fork via :meth:`GraphRun.counterfactual` with
       ``mutation.node_output_overrides[BROKER_NODE_ID]`` mints a fresh
       ``cf-<uuid>`` run_id.
    2. ``derived_graph_hash`` differs based on ``node_output_overrides``
       bytes — proving the override participates in the cf-hash
       pre-image (design §3.8.3, NFR-4 determinism).
    3. Two cf-forks with DIFFERENT broker overrides produce DIFFERENT
       cf-hashes (the cf-derived ``graph_hash`` is sensitive to the
       override payload, not just to "an override is present").
    4. Parent checkpoints at every step are byte-identical post fork.
    5. The cf-run's ``initial_state`` mirrors the fork-step parent
       checkpoint state (NFR-4 upstream-bit-identity entry point).
    """
    cp = SQLiteCheckpointer(tmp_path / "cf_broker.sqlite")
    await cp.bootstrap()

    parent_id = "parent-run-cf-broker"
    await _seed_parent_with_broker(cp, run_id=parent_id)

    # Snapshot parent state pre-fork for byte-identity assertions.
    pre_fork_states: list[dict[str, Any]] = []
    for step in range(6):
        ckpt = await cp.read_at_step(parent_id, step)
        assert ckpt is not None, f"missing parent checkpoint at step={step}"
        pre_fork_states.append(dict(ckpt.state))

    # Two distinct broker overrides — sources_queried differs in each.
    override_cd: dict[str, Any] = {
        _BROKER_NODE_ID: {
            "sources_queried": ["C", "D"],
            "data": {"signals": ["s-C", "s-D"]},
        }
    }
    override_ef: dict[str, Any] = {
        _BROKER_NODE_ID: {
            "sources_queried": ["E", "F"],
            "data": {"signals": ["s-E", "s-F"]},
        }
    }
    mutation_cd = CounterfactualMutation(node_output_overrides=override_cd)
    mutation_ef = CounterfactualMutation(node_output_overrides=override_ef)

    # ---- Assertion 1: cf-fork mints fresh run_id ---------------------------
    cf_run_cd = await GraphRun.counterfactual(cp, parent_id, step=_BROKER_STEP, mutate=mutation_cd)
    assert cf_run_cd.run_id != parent_id
    assert cf_run_cd.run_id.startswith("cf-"), (
        f"cf run_id missing 'cf-' prefix: {cf_run_cd.run_id!r}"
    )

    # ---- Assertion 2 + 3: derived_graph_hash sensitivity --------------------
    hash_cd = derived_graph_hash(_PARENT_GRAPH_HASH, mutation_cd)
    hash_ef = derived_graph_hash(_PARENT_GRAPH_HASH, mutation_ef)
    hash_no_mutation = derived_graph_hash(_PARENT_GRAPH_HASH, CounterfactualMutation())

    # Three distinct 64-char hex digests.
    assert len({hash_cd, hash_ef, hash_no_mutation}) == 3, (
        "node_output_overrides bytes must participate in the cf-hash pre-image"
    )
    assert all(len(h) == 64 for h in [hash_cd, hash_ef, hash_no_mutation])

    # ---- Assertion 4: parent checkpoints byte-identical post fork ----------
    for step in range(6):
        ckpt = await cp.read_at_step(parent_id, step)
        assert ckpt is not None
        assert ckpt.state == pre_fork_states[step], (
            f"parent step={step} state mutated by cf-fork: "
            f"pre={pre_fork_states[step]!r} post={ckpt.state!r}"
        )

    # ---- Assertion 5: cf-run initial_state mirrors fork-step state ---------
    assert cf_run_cd.initial_state is not None
    cf_state = cf_run_cd.initial_state.model_dump()
    # The fork-step (step=2) parent state has phase="broker" + the original
    # ["A","B"] sources_queried. node_output_overrides applies *during*
    # cf-replay, not as a state seed — so the cf-handle's initial_state
    # mirrors the parent's recorded fork-step state byte-for-byte (NFR-4
    # upstream-identity entry point).
    fork_step_parent_state = pre_fork_states[_BROKER_STEP]
    assert cf_state == fork_step_parent_state, (
        f"cf initial_state should mirror parent fork-step state; "
        f"cf={cf_state!r} parent={fork_step_parent_state!r}"
    )
