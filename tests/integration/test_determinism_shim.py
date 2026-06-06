# SPDX-License-Identifier: Apache-2.0
"""TDD-RED: FR-28 determinism shims (now/random/uuid4/urandom + set rejection).

Pins the contract for ``stargraph.replay.determinism`` *before* the implementation
lands in task 3.31. Per design §3.8.5, replay-mode workflows MUST source their
non-deterministic primitives through these shims so byte-identical re-execution
is achievable on counterfactual or resume.

The five cases below are pulled verbatim from the task 3.30 ``Do`` block:

1. ``stargraph.replay.now()`` returns the recorded value during replay (not the
   real wall clock).
2. ``stargraph.replay.random()`` returns the recorded sequence.
3. ``stargraph.replay.uuid4()`` returns the recorded UUID.
4. ``stargraph.replay.urandom(n)`` returns the recorded bytes.
5. ``set`` field in state schema rejected at compile time
   (Pydantic-validator / IR-validator surface).

The shim module (``stargraph.replay.determinism``) ships in task 3.31; its
absence -- plus the missing ``set``-rejection wiring on the IR validator --
is what flips this test red.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from stargraph.errors import IRValidationError, ValidationError
from stargraph.ir._models import IRDocument, NodeSpec


def _load_determinism() -> Any:
    """Import ``stargraph.replay.determinism`` with a clear failure mode.

    Phase-3 task 3.31 ships the module; until it does, this test stays red
    via :class:`ImportError`. Using ``importlib.import_module`` here keeps
    the import expression dynamic so pyright does not try to resolve the
    not-yet-built module statically.
    """
    return importlib.import_module("stargraph.replay.determinism")


# ---------------------------------------------------------------------------
# Case 1 -- stargraph.replay.now()
# ---------------------------------------------------------------------------


def test_now_returns_recorded_value_during_replay() -> None:
    """In replay mode, ``now()`` returns the recorded float, not wall clock."""
    determinism = _load_determinism()
    recorded = 1700000000.5

    scope_cls = determinism.DeterminismScope
    scope = scope_cls(replay=True, recording={"now": [recorded]})
    with scope:
        observed = determinism.now()

    assert observed == recorded


# ---------------------------------------------------------------------------
# Case 2 -- stargraph.replay.random()
# ---------------------------------------------------------------------------


def test_random_returns_recorded_sequence() -> None:
    """In replay mode, ``random()`` yields the recorded floats in order."""
    determinism = _load_determinism()
    recorded = [0.1, 0.2, 0.3]

    scope_cls = determinism.DeterminismScope
    scope = scope_cls(replay=True, recording={"random": list(recorded)})
    with scope:
        observed = [determinism.random() for _ in range(3)]

    assert observed == recorded


# ---------------------------------------------------------------------------
# Case 3 -- stargraph.replay.uuid4()
# ---------------------------------------------------------------------------


def test_uuid4_returns_recorded_uuid() -> None:
    """In replay mode, ``uuid4()`` returns the recorded UUID."""
    import uuid

    determinism = _load_determinism()
    recorded = uuid.UUID("12345678-1234-5678-1234-567812345678")

    scope_cls = determinism.DeterminismScope
    scope = scope_cls(replay=True, recording={"uuid4": [recorded]})
    with scope:
        observed = determinism.uuid4()

    assert observed == recorded


# ---------------------------------------------------------------------------
# Case 4 -- stargraph.replay.urandom(n)
# ---------------------------------------------------------------------------


def test_urandom_returns_recorded_bytes() -> None:
    """In replay mode, ``urandom(n)`` returns the recorded byte string."""
    determinism = _load_determinism()
    recorded = b"\x00\x01\x02\x03"

    scope_cls = determinism.DeterminismScope
    scope = scope_cls(replay=True, recording={"urandom": [recorded]})
    with scope:
        observed = determinism.urandom(4)

    assert observed == recorded


# ---------------------------------------------------------------------------
# Case 5 -- ``set`` field rejected at compile time in state_schema
# ---------------------------------------------------------------------------


def test_state_schema_set_field_rejected_at_compile_time() -> None:
    """Per FR-28: ``set`` types in IR ``state_schema`` raise at validate time.

    The amendment-6 contract: ``dict`` ordering is preserved (3.7+
    insertion-order), but Pydantic ``set`` fields are explicitly forbidden in
    state schema -- callers must use ``frozenset`` with declared sort or
    ``list[str]``. Task 3.31 wires the rejection into the IR validator /
    :func:`stargraph.graph.definition._compile_state_schema`. Until then, ``set``
    flows through the legacy ``ValidationError`` "unsupported type" branch
    rather than the targeted FR-28 ``IRValidationError`` path -- so this test
    stays red.
    """
    doc = IRDocument(
        ir_version="1.0.0",
        id="run:cf-det-shim-test",
        nodes=[NodeSpec(id="n1", kind="task")],
        state_schema={"members": "set"},
    )

    import stargraph

    with pytest.raises((IRValidationError, ValidationError)) as excinfo:
        stargraph.Graph(doc)

    # The post-3.31 contract: rejection cites FR-28 / "set" forbidden, not the
    # generic "unsupported type" fallback. This assertion keeps the test red
    # under the legacy path *and* pins the post-GREEN error message shape.
    message = str(excinfo.value).lower()
    assert "set" in message
    assert "forbidden" in message or "fr-28" in message or "frozenset" in message
