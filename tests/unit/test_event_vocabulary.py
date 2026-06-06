# SPDX-License-Identifier: Apache-2.0
"""Event vocabulary discriminated-union exhaustiveness (FR-14, design §3.7.1).

Validates:
1. All 10 typed events round-trip through the Pydantic discriminated union
   keyed on the ``type`` Literal.
2. Every variant carries the standard envelope (run_id, step, branch_id,
   ts, payload).
3. An unknown ``type`` discriminator fails validation (closed union).
4. An exhaustive ``match`` over :data:`Event` raises on a synthetic
   unknown variant -- mirrors the runtime guarantee that consumers cannot
   silently drop new event types.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from stargraph.runtime.events import (
    BranchCancelledEvent,
    BranchCompletedEvent,
    BranchStartedEvent,
    CheckpointEvent,
    ErrorEvent,
    Event,
    ResultEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    TransitionEvent,
)

EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)

_TS = datetime(2026, 4, 29, 12, 0, 0, tzinfo=UTC)
_ENVELOPE: dict[str, Any] = {
    "run_id": "run-1",
    "step": 7,
    "branch_id": "branch-a",
    "ts": _TS.isoformat(),
    "payload": {"k": "v"},
}


def _with(extra: dict[str, Any]) -> dict[str, Any]:
    return {**_ENVELOPE, **extra}


# ---- 10 typed event fixtures (one per discriminator) -----------------------

EVENT_CASES: list[tuple[str, dict[str, Any], type]] = [
    (
        "token",
        _with({"type": "token", "model": "gpt-4", "token": "hi", "index": 0}),
        TokenEvent,
    ),
    (
        "tool_call",
        _with(
            {
                "type": "tool_call",
                "tool_name": "search",
                "namespace": "web",
                "args": {"q": "x"},
                "call_id": "c1",
            }
        ),
        ToolCallEvent,
    ),
    (
        "tool_result",
        _with(
            {
                "type": "tool_result",
                "call_id": "c1",
                "ok": True,
                "result": {"r": 1},
                "error": None,
            }
        ),
        ToolResultEvent,
    ),
    (
        "transition",
        _with(
            {
                "type": "transition",
                "from_node": "a",
                "to_node": "b",
                "rule_id": "r1",
                "reason": "matched",
            }
        ),
        TransitionEvent,
    ),
    (
        "checkpoint",
        _with({"type": "checkpoint", "checkpoint_id": "ck-1"}),
        CheckpointEvent,
    ),
    (
        "error",
        _with(
            {
                "type": "error",
                "scope": "node",
                "message": "boom",
                "recoverable": False,
            }
        ),
        ErrorEvent,
    ),
    (
        "result",
        _with(
            {
                "type": "result",
                "status": "done",
                "final_state": {"out": 1},
                "run_duration_ms": 42,
            }
        ),
        ResultEvent,
    ),
    (
        "branch_started",
        _with({"type": "branch_started", "target": "n1", "strategy": "race"}),
        BranchStartedEvent,
    ),
    (
        "branch_completed",
        _with({"type": "branch_completed", "result": {"x": 1}}),
        BranchCompletedEvent,
    ),
    (
        "branch_cancelled",
        _with({"type": "branch_cancelled", "reason": "race-loser"}),
        BranchCancelledEvent,
    ),
]


@pytest.mark.parametrize(("name", "data", "cls"), EVENT_CASES, ids=[c[0] for c in EVENT_CASES])
def test_event_dispatch(name: str, data: dict[str, Any], cls: type) -> None:
    """Each of the 10 type literals dispatches to the correct subclass and preserves envelope."""
    evt = EVENT_ADAPTER.validate_python(data)

    # Discriminated union dispatched to the right concrete subclass.
    assert isinstance(evt, cls), f"{name!r} -> {type(evt).__name__}, expected {cls.__name__}"

    # Envelope shape: every variant carries the full envelope.
    assert evt.run_id == "run-1"
    assert evt.step == 7
    assert evt.branch_id == "branch-a"
    assert evt.ts == _TS
    assert evt.payload == {"k": "v"}
    assert evt.type == name


def test_event_count_is_ten() -> None:
    """Sanity check: the discriminated union has exactly 10 variants (FR-14)."""
    assert len(EVENT_CASES) == 10
    assert len({name for name, _, _ in EVENT_CASES}) == 10


def test_unknown_discriminator_rejected() -> None:
    """Closed discriminated union: unknown ``type`` literal must fail validation."""
    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_python(_with({"type": "not_a_real_event"}))


def _exhaustive_describe(evt: Event) -> str:
    """Exhaustive match over Event -- the wildcard MUST be unreachable for known types.

    A plain ``assert_never`` requires a static-only hook; a runtime ``raise`` on
    the wildcard is the dynamic equivalent that this test exercises.
    """
    match evt:
        case TokenEvent():
            return "token"
        case ToolCallEvent():
            return "tool_call"
        case ToolResultEvent():
            return "tool_result"
        case TransitionEvent():
            return "transition"
        case CheckpointEvent():
            return "checkpoint"
        case ErrorEvent():
            return "error"
        case ResultEvent():
            return "result"
        case BranchStartedEvent():
            return "branch_started"
        case BranchCompletedEvent():
            return "branch_completed"
        case BranchCancelledEvent():
            return "branch_cancelled"
        case _:  # pragma: no cover -- defensive guard
            raise AssertionError(f"non-exhaustive match: {type(evt).__name__}")


def test_match_is_exhaustive_over_known_variants() -> None:
    """All 10 known variants flow through the match without hitting the wildcard."""
    for name, data, _ in EVENT_CASES:
        evt = EVENT_ADAPTER.validate_python(data)
        assert _exhaustive_describe(evt) == name


def test_match_wildcard_raises_on_unknown_event() -> None:
    """A synthetic non-Event object trips the match wildcard guard."""

    class _AlienEvent:
        type = "alien"

    alien: Any = _AlienEvent()
    with pytest.raises(AssertionError, match="non-exhaustive match"):
        _exhaustive_describe(alien)
