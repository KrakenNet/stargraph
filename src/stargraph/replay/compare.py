# SPDX-License-Identifier: Apache-2.0
"""``RunDiff`` + :func:`compare` -- per-step JSONPatch diff (FR-27, AC-4.4).

Per design §3.8.6, ``stargraph.compare(orig, cf)`` returns a typed
:class:`RunDiff` describing how a counterfactual run diverged from its
original. State diffs use **JSONPatch RFC 6902** (via the ``jsonpatch``
package) so any consumer with an off-the-shelf RFC 6902 applier can
reconstruct one state from the other.

The :class:`StepDiff.diverged_at` literal categorizes the *first* axis
of divergence at that step (state mutation, replaced node output,
different routing edge, or differing recorded side-effect). Per design
§3.8.5, side-effect tools are must-stub during cf replay -- so a
``side_effect`` divergence indicates either an explicit
``node_output_overrides`` entry or a recording-substrate issue.

This module is the read-only "diff" half of the cf API surface; the
write half (``run.counterfactual(...)``) lives in
:mod:`stargraph.replay.counterfactual`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import jsonpatch  # pyright: ignore[reportMissingTypeStubs]
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from stargraph.replay.history import RunHistory

__all__ = ["RunDiff", "StepDiff", "compare"]


DivergedAt = Literal["state", "node_output", "edge_taken", "side_effect"]


class StepDiff(BaseModel):
    """Per-step divergence record (design §3.8.6).

    Attributes:
        step: The step index at which both runs were compared.
        branch_id: Parallel-branch identity (``None`` = main branch).
            Sourced from the cf-side checkpoint when present, else from
            the original-side checkpoint.
        diverged_at: First-axis divergence category. ``state`` when the
            state dicts differ; ``node_output`` when ``last_node`` /
            ``next_action`` differ; ``edge_taken`` when ``next_action``
            routing differs while state matches; ``side_effect`` when
            ``side_effects_hash`` differs (implies recording mismatch
            or explicit ``node_output_overrides`` at this step).
        state_diff: JSONPatch RFC 6902 ops transforming
            ``orig.state`` -> ``cf.state``. Empty list when state
            matches.
        output_diff: ``{"orig": last_node, "cf": last_node}`` when
            recorded node outputs differ; ``None`` otherwise.
        side_effect_diff: ``{"orig": hash, "cf": hash}`` when
            ``side_effects_hash`` differs; ``None`` otherwise.
    """

    model_config = ConfigDict(extra="forbid")

    step: int
    branch_id: str | None
    diverged_at: DivergedAt
    state_diff: list[dict[str, Any]]
    output_diff: dict[str, Any] | None
    side_effect_diff: dict[str, Any] | None


class RunDiff(BaseModel):
    """Aggregate diff for an original run vs its counterfactual (design §3.8.6).

    Attributes:
        original_run_id: ``RunHistory.run_id`` of the original run.
        counterfactual_run_id: ``RunHistory.run_id`` of the cf run.
        derived_hash: cf-prefix ``graph_hash`` of the counterfactual
            (read off the first cf-side checkpoint). Empty string when
            the cf-side has no checkpoints (degenerate case kept loud,
            not silently skipped).
        steps: Per-step :class:`StepDiff` entries, in step order. Steps
            where both runs match are *omitted* (the caller cares about
            divergences, not coincidences).
        final_state_diff: JSONPatch RFC 6902 ops transforming the last
            ``orig`` state into the last ``cf`` state. Empty list when
            the final states match (or one side is empty).
        final_status_diff: ``(orig_last_node, cf_last_node)`` tuple
            when the final ``last_node`` differs; ``None`` otherwise.
            Used as a coarse "did the run end at the same place?"
            signal without requiring callers to walk ``steps``.
    """

    model_config = ConfigDict(extra="forbid")

    original_run_id: str
    counterfactual_run_id: str
    derived_hash: str
    steps: list[StepDiff]
    final_state_diff: list[dict[str, Any]]
    final_status_diff: tuple[str, str] | None


def _patch_ops(src: dict[str, Any], dst: dict[str, Any]) -> list[dict[str, Any]]:
    """Return RFC 6902 ops from ``src`` to ``dst`` (empty list when equal)."""
    patch = jsonpatch.make_patch(src, dst)  # pyright: ignore[reportUnknownMemberType]
    # ``patch.patch`` is the underlying list[dict] of RFC 6902 ops.
    ops: list[dict[str, Any]] = list(patch.patch)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportUnknownArgumentType]
    return ops


def compare(orig: RunHistory, cf: RunHistory) -> RunDiff:
    """Walk both histories step-by-step and return a :class:`RunDiff`.

    Per design §3.8.6, comparison is positional on ``checkpoints``
    (which :class:`RunHistory.load` populates in ascending step order).
    Steps present in only one run are emitted as a divergence at that
    step using the missing side as ``{}`` -- the JSONPatch ops then
    describe how to reach the present side from empty.

    Args:
        orig: The original run's :class:`RunHistory` snapshot.
        cf: The counterfactual run's :class:`RunHistory` snapshot.

    Returns:
        A :class:`RunDiff` whose ``steps`` lists *only* divergent
        steps (matching steps are omitted); ``final_state_diff`` is
        the JSONPatch from the last orig state to the last cf state.
    """
    derived_hash = cf.checkpoints[0].graph_hash if cf.checkpoints else ""

    steps: list[StepDiff] = []
    n = max(len(orig.checkpoints), len(cf.checkpoints))
    for i in range(n):
        o = orig.checkpoints[i] if i < len(orig.checkpoints) else None
        c = cf.checkpoints[i] if i < len(cf.checkpoints) else None

        o_state: dict[str, Any] = o.state if o is not None else {}
        c_state: dict[str, Any] = c.state if c is not None else {}
        ops = _patch_ops(o_state, c_state)

        o_node = o.last_node if o is not None else ""
        c_node = c.last_node if c is not None else ""
        o_next = o.next_action if o is not None else None
        c_next = c.next_action if c is not None else None
        o_se = o.side_effects_hash if o is not None else ""
        c_se = c.side_effects_hash if c is not None else ""

        node_differs = o_node != c_node
        edge_differs = o_next != c_next
        se_differs = o_se != c_se
        state_differs = bool(ops)

        if not (state_differs or node_differs or edge_differs or se_differs):
            continue

        diverged_at: DivergedAt
        # First-axis precedence: state > node_output > edge_taken > side_effect
        if state_differs:
            diverged_at = "state"
        elif node_differs:
            diverged_at = "node_output"
        elif edge_differs:
            diverged_at = "edge_taken"
        else:
            diverged_at = "side_effect"

        if o is not None:
            step_idx = o.step
            branch_id = c.branch_id if c is not None else o.branch_id
        elif c is not None:
            step_idx = c.step
            branch_id = c.branch_id
        else:
            step_idx = i
            branch_id = None

        steps.append(
            StepDiff(
                step=step_idx,
                branch_id=branch_id,
                diverged_at=diverged_at,
                state_diff=ops,
                output_diff=({"orig": o_node, "cf": c_node} if node_differs else None),
                side_effect_diff=({"orig": o_se, "cf": c_se} if se_differs else None),
            )
        )

    final_orig_state = orig.checkpoints[-1].state if orig.checkpoints else {}
    final_cf_state = cf.checkpoints[-1].state if cf.checkpoints else {}
    final_state_diff = _patch_ops(final_orig_state, final_cf_state)

    final_orig_node = orig.checkpoints[-1].last_node if orig.checkpoints else ""
    final_cf_node = cf.checkpoints[-1].last_node if cf.checkpoints else ""
    final_status_diff: tuple[str, str] | None = (
        (final_orig_node, final_cf_node) if final_orig_node != final_cf_node else None
    )

    return RunDiff(
        original_run_id=orig.run_id,
        counterfactual_run_id=cf.run_id,
        derived_hash=derived_hash,
        steps=steps,
        final_state_diff=final_state_diff,
        final_status_diff=final_status_diff,
    )
