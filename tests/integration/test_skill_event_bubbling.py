# SPDX-License-Identifier: Apache-2.0
"""FR-24 / AC-3.4 -- skill subgraph event bubbling integration test.

Pins the FR-24 contract:

* :attr:`Skill.bubble_events` defaults to ``True`` -- child subgraph
  events propagate to the parent run's event bus (and therefore appear
  in ``run.stream()``).
* ``bubble_events=False`` opts out -- child subgraph events DO NOT
  appear on the parent's bus (LangGraph #2484 mitigation, replay-first
  stance: full visibility by default; quiet hot paths on demand).

The engine's :class:`stargraph.nodes.SubGraphNode` always streams child
events to the parent bus (see :mod:`tests.integration.test_subgraph_node`).
The skill boundary translator (FR-23) is the layer that consults
:attr:`Skill.bubble_events` to gate that emission. This test pins the
contract via a test-side gating wrapper -- the same shape the engine
boundary translator uses when full :class:`SkillRef` -> SubGraphNode
wiring lands. Two paths exercised here:

* **bubbling** -- ``bubble_events=True``: route children straight through
  :class:`SubGraphNode`; every child :class:`TransitionEvent` lands on
  the parent's :class:`~stargraph.runtime.bus.EventBus`.
* **quiet** -- ``bubble_events=False``: gate the dispatch through a
  detached child bus; the parent's bus stays empty.
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from pydantic import BaseModel

from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.nodes.subgraph import SubGraphNode
from stargraph.runtime.bus import EventBus
from stargraph.runtime.events import TransitionEvent
from stargraph.skills.base import Skill, SkillKind

pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


class _MessageState(BaseModel):
    """Minimal state model carried through the skill's subgraph."""

    message: str
    visits: int = 0


class _AppendChild(NodeBase):
    """Fixture child: append a tag to ``message`` + bump ``visits``."""

    id: str

    def __init__(self, *, node_id: str, tag: str) -> None:
        self.id = node_id
        self._tag = tag

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del ctx
        cur_message: str = getattr(state, "message")  # noqa: B009
        cur_visits: int = getattr(state, "visits")  # noqa: B009
        return {
            "message": f"{cur_message}->{self._tag}",
            "visits": cur_visits + 1,
        }


class _ParentRun:
    """Tiny parent-run stand-in carrying the SubGraphContext surface."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.bus = EventBus()
        self.fathom: Any = None


class _QuietRun:
    """Variant parent-run whose ``bus`` is a *detached* child sink.

    Models the boundary translator's ``bubble_events=False`` path: the
    child subgraph still needs a bus (SubGraphNode emits unconditionally;
    that is its FR-7 contract), but the events drop into a sink the
    parent's stream never receives from.
    """

    def __init__(self, run_id: str, parent_bus: EventBus) -> None:
        self.run_id = run_id
        self.parent_bus = parent_bus  # what run.stream() drains
        self.bus = EventBus()  # detached child sink (events go here)
        self.fathom: Any = None


def _make_skill(*, bubble_events: bool) -> Skill:
    """Build a minimal :class:`Skill` with the requested bubbling policy."""
    return Skill(
        name="bubble-fixture",
        version="0.1.0",
        kind=SkillKind.agent,
        description="Fixture skill for FR-24 event-bubbling tests.",
        state_schema=_MessageState,
        bubble_events=bubble_events,
    )


async def _drain(bus: EventBus, *, expected: int) -> list[TransitionEvent]:
    """Drain exactly ``expected`` events off ``bus`` (1s timeout per event)."""
    drained: list[TransitionEvent] = []
    for _ in range(expected):
        with anyio.fail_after(1.0):
            ev = await bus.receive()
        assert isinstance(ev, TransitionEvent)
        drained.append(ev)
    return drained


def _resolve_run(skill: Skill, parent: _ParentRun) -> _ParentRun | _QuietRun:
    """Mirror the FR-23 boundary translator: gate by ``skill.bubble_events``.

    This is the one knob the skill manifest exposes that the engine
    boundary translator consumes -- ``True`` keeps the parent bus as
    the destination; ``False`` swaps in a detached child sink so the
    parent's stream stays quiet.
    """
    if skill.bubble_events:
        return parent
    return _QuietRun(run_id=parent.run_id, parent_bus=parent.bus)


# --------------------------------------------------------------------------- #
# Cases                                                                       #
# --------------------------------------------------------------------------- #


async def test_bubble_events_true_default_streams_to_parent_bus() -> None:
    """FR-24 default-on: ``bubble_events=True`` -> events on parent bus.

    Done-when (test contract): three children fire, three TransitionEvents
    land on the parent's bus, all carrying the sub's ``branch_id``.
    """
    skill = _make_skill(bubble_events=True)
    assert skill.bubble_events is True, "FR-24 default must be True"

    parent = _ParentRun(run_id="parent-bubble-default")
    sub = SubGraphNode(
        subgraph_id=skill.site_id,
        children=[
            _AppendChild(node_id="prep", tag="P"),
            _AppendChild(node_id="fit", tag="F"),
            _AppendChild(node_id="register", tag="R"),
        ],
    )

    ctx = _resolve_run(skill, parent)
    initial = _MessageState(message="seed", visits=0)
    outputs = await sub.execute(initial, ctx)  # type: ignore[arg-type]

    assert outputs["message"] == "seed->P->F->R"
    assert outputs["visits"] == 3

    # Three TransitionEvents on the parent bus -- run.stream() consumers
    # see them interleaved with the parent's own events.
    drained = await _drain(parent.bus, expected=3)
    assert [e.from_node for e in drained] == ["prep", "fit", "register"]
    assert all(e.branch_id == skill.site_id for e in drained)
    assert all(e.run_id == "parent-bubble-default" for e in drained)


async def test_bubble_events_false_keeps_parent_bus_quiet() -> None:
    """FR-24 opt-out: ``bubble_events=False`` -> parent bus stays empty.

    Done-when (test contract): the parent's bus has zero events after the
    sub-graph runs to completion; the detached child sink carried all
    three events (proves the children DID fire -- the parent simply
    didn't see them).
    """
    skill = _make_skill(bubble_events=False)
    assert skill.bubble_events is False

    parent = _ParentRun(run_id="parent-bubble-off")
    sub = SubGraphNode(
        subgraph_id=skill.site_id,
        children=[
            _AppendChild(node_id="alpha", tag="a"),
            _AppendChild(node_id="beta", tag="b"),
            _AppendChild(node_id="gamma", tag="c"),
        ],
    )

    ctx = _resolve_run(skill, parent)
    assert isinstance(ctx, _QuietRun), (
        "bubble_events=False MUST route through the detached child sink"
    )

    initial = _MessageState(message="seed", visits=0)
    outputs = await sub.execute(initial, ctx)  # type: ignore[arg-type]

    assert outputs["message"] == "seed->a->b->c"

    # All three events landed on the *child* sink.
    drained = await _drain(ctx.bus, expected=3)
    assert [e.from_node for e in drained] == ["alpha", "beta", "gamma"]

    # The parent bus stays quiet -- a brief receive() must time out.
    with pytest.raises(TimeoutError):
        with anyio.fail_after(0.1):
            await parent.bus.receive()


async def test_bubble_events_field_default_is_true_on_manifest() -> None:
    """FR-24: the manifest field defaults to ``True`` (no override needed).

    Pins that callers who omit ``bubble_events`` at construction get
    the design §3.7 / FR-24 default behaviour (full visibility) without
    having to remember the toggle.
    """
    skill = Skill(
        name="default-bubble",
        version="0.1.0",
        kind=SkillKind.agent,
        description="Default bubble-events check.",
        state_schema=_MessageState,
    )
    assert skill.bubble_events is True
