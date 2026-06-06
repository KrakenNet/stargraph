# SPDX-License-Identifier: Apache-2.0
"""stargraph.runtime -- engine runtime primitives (events, parallel/join, action vocab).

Phase 1 ships the streaming event vocabulary (FR-14) -- a 10-type
Pydantic discriminated union at :mod:`stargraph.runtime.events` -- and the
bounded event bus with back-pressure (FR-15) at
:mod:`stargraph.runtime.bus`. The action-vocabulary translator
(Learning F) and parallel/join coordinator (Learnings B, C) land in
subsequent tasks.
"""

from __future__ import annotations

from stargraph.runtime.bus import EventBus
from stargraph.runtime.events import (
    BranchCancelledEvent,
    BranchCompletedEvent,
    BranchStartedEvent,
    CheckpointEvent,
    ErrorEvent,
    Event,
    EventBase,
    ResultEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    TransitionEvent,
)

__all__ = [
    "BranchCancelledEvent",
    "BranchCompletedEvent",
    "BranchStartedEvent",
    "CheckpointEvent",
    "ErrorEvent",
    "Event",
    "EventBase",
    "EventBus",
    "ResultEvent",
    "TokenEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "TransitionEvent",
]
