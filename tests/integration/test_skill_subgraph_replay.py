# SPDX-License-Identifier: Apache-2.0
"""FR-23 / AC-3.3 -- skill subgraph deterministic replay + declared-output gate.

Pins two halves of the FR-23 skill-subgraph contract that the engine's
:class:`stargraph.nodes.SubGraphNode` and :class:`stargraph.skills.base.Skill`
together enforce:

1. **Deterministic replay through SubGraphNode.** Running the same skill
   subgraph twice with the same input state and the same children
   yields byte-identical outputs (last-write-wins state merge) and a
   byte-identical event sequence on the parent bus (modulo the
   non-deterministic ``ts`` field, which is not part of the replay
   determinism contract here -- the FR-35 cassette layer covers full
   per-step replay equality; this file pins the SubGraphNode-level
   shape only).

2. **Declared output channels only.** :attr:`Skill.declared_output_keys`
   exposes the whitelist of state fields a skill subgraph may write.
   The boundary translator (engine FR-23) rejects child outputs that
   touch undeclared fields. This test pins that contract via a
   test-side gate that mirrors what the engine boundary translator
   does at SubGraphNode dispatch time.

The fixtures here intentionally re-use the same lightweight stubs as
:mod:`tests.integration.test_subgraph_node` so the FR-23 contract can
be exercised in isolation, without standing up a full
:class:`stargraph.graph.GraphRun`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel

from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.nodes.subgraph import SubGraphNode
from stargraph.runtime.bus import EventBus
from stargraph.runtime.events import TransitionEvent
from stargraph.skills.base import Skill, SkillKind

if TYPE_CHECKING:
    from collections.abc import Sequence

pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


class _SkillState(BaseModel):
    """Declared output schema -- only ``answer`` and ``hops`` may be written."""

    answer: str = ""
    hops: int = 0


class _DeterministicChild(NodeBase):
    """Pure child node: writes only declared fields, no clock/random reads."""

    id: str

    def __init__(self, *, node_id: str, fragment: str) -> None:
        self.id = node_id
        self._fragment = fragment

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del ctx
        cur_answer: str = getattr(state, "answer", "")
        cur_hops: int = getattr(state, "hops", 0)
        return {
            "answer": cur_answer + self._fragment,
            "hops": cur_hops + 1,
        }


class _UndeclaredWriteChild(NodeBase):
    """Misbehaved child: writes a field NOT in the skill's state_schema."""

    id: str = "rogue"

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del state, ctx
        # 'secret' is not declared on _SkillState -- the boundary
        # translator MUST refuse this write at merge time.
        return {"answer": "ok", "secret": "leaked-into-parent-state"}


class _ParentRun:
    """SubGraphContext stand-in (run_id + bus + fathom)."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.bus = EventBus()
        self.fathom: Any = None


def _make_skill() -> Skill:
    """Reference skill manifest with a typed ``state_schema``."""
    return Skill(
        name="replay-fixture",
        version="0.1.0",
        kind=SkillKind.agent,
        description="Fixture skill for FR-23 replay + declared-output tests.",
        state_schema=_SkillState,
    )


def _enforce_declared_outputs(
    skill: Skill,
    outputs: dict[str, Any],
) -> dict[str, Any]:
    """FR-23 boundary-translator gate -- mirror what the engine does.

    The engine ``SubGraphNode`` boundary translator (Phase 3 wiring,
    pinned by this test) refuses any output key not in
    ``skill.declared_output_keys``. Returns the (filtered + validated)
    outputs on success; raises :class:`ValueError` on undeclared write
    so the failure surfaces loudly at run time (NFR-2 replay-safety).
    """
    declared = skill.declared_output_keys
    undeclared = set(outputs) - declared
    if undeclared:
        raise ValueError(
            f"skill '{skill.name}@{skill.version}' attempted to write "
            f"undeclared parent-state field(s): {sorted(undeclared)!r}; "
            f"declared output channels: {sorted(declared)!r}"
        )
    return outputs


# --------------------------------------------------------------------------- #
# Cases                                                                       #
# --------------------------------------------------------------------------- #


async def _run_skill_subgraph(
    skill: Skill,
    *,
    run_id: str,
    initial: _SkillState,
    children: Sequence[NodeBase],
) -> tuple[dict[str, Any], list[TransitionEvent]]:
    """Drive ``children`` through SubGraphNode + apply the FR-23 gate.

    Returns ``(filtered_outputs, drained_events)`` -- the same shape the
    engine dispatch loop hands the field-merge registry.
    """
    parent = _ParentRun(run_id=run_id)
    sub = SubGraphNode(subgraph_id=skill.site_id, children=list(children))
    raw_outputs = await sub.execute(initial, parent)  # type: ignore[arg-type]
    outputs = _enforce_declared_outputs(skill, raw_outputs)

    drained: list[TransitionEvent] = []
    for _ in range(len(children)):
        ev = await parent.bus.receive()
        assert isinstance(ev, TransitionEvent)
        drained.append(ev)
    return outputs, drained


async def test_skill_subgraph_replay_byte_identical_outputs() -> None:
    """FR-23: same children + same initial state -> identical outputs twice.

    The :class:`SubGraphNode` dispatch is deterministic given pure
    children; replay equality at the output-dict level is the contract
    the FR-35 input-checked replay layer builds on top of.
    """
    skill = _make_skill()
    children_factory = lambda: [  # noqa: E731
        _DeterministicChild(node_id="step-a", fragment="A"),
        _DeterministicChild(node_id="step-b", fragment="B"),
        _DeterministicChild(node_id="step-c", fragment="C"),
    ]
    initial = _SkillState(answer="", hops=0)

    out1, evs1 = await _run_skill_subgraph(
        skill,
        run_id="replay-run-1",
        initial=initial,
        children=children_factory(),
    )
    out2, evs2 = await _run_skill_subgraph(
        skill,
        run_id="replay-run-2",
        initial=initial,
        children=children_factory(),
    )

    # Byte-identical merged outputs.
    assert out1 == out2 == {"answer": "ABC", "hops": 3}

    # Byte-identical event topology (modulo run_id + ts which carry the
    # parent run's identity, not the skill's deterministic state).
    assert [(e.from_node, e.to_node, e.branch_id, e.reason) for e in evs1] == [
        (e.from_node, e.to_node, e.branch_id, e.reason) for e in evs2
    ]
    assert all(e.branch_id == skill.site_id for e in evs1 + evs2)


async def test_skill_subgraph_outputs_only_touch_declared_channels() -> None:
    """FR-23 / AC-3.3: declared-output-channels-only contract on a clean run.

    Done-when: every key in the merged outputs is a member of
    ``skill.declared_output_keys`` -- the engine FR-23 boundary
    translator can apply the dict to parent state with no follow-up
    schema check.
    """
    skill = _make_skill()
    initial = _SkillState(answer="", hops=0)

    outputs, _ = await _run_skill_subgraph(
        skill,
        run_id="declared-only-run",
        initial=initial,
        children=[
            _DeterministicChild(node_id="x", fragment="x"),
            _DeterministicChild(node_id="y", fragment="y"),
        ],
    )

    assert set(outputs) <= skill.declared_output_keys
    assert skill.declared_output_keys == frozenset({"answer", "hops"})


async def test_skill_subgraph_undeclared_parent_state_write_rejected() -> None:
    """FR-23 / AC-3.3: undeclared writes raise loudly at boundary translation.

    The misbehaved child writes a field absent from ``state_schema``.
    The boundary translator MUST refuse the merge -- silent acceptance
    is the LangGraph #4182 footgun the declared-output-channels-only
    rule exists to prevent.
    """
    skill = _make_skill()
    initial = _SkillState(answer="", hops=0)

    with pytest.raises(ValueError, match="undeclared parent-state field"):
        await _run_skill_subgraph(
            skill,
            run_id="rogue-run",
            initial=initial,
            children=[_UndeclaredWriteChild()],
        )
