# SPDX-License-Identifier: Apache-2.0
"""Cross-reference smoke for the FR-26 default policy.

Pinpoints the single invariant that gives FR-21 / NFR-8 their teeth: when the
``@tool`` decorator is invoked with ``side_effects in {write, external}`` and
no explicit ``replay_policy``, the resolved policy must be ``must_stub``.

Lives in ``tests/replay/`` so the must-stub default sits alongside the
cassette / determinism tests task 3.30+ adds.

Fails before 3.28: ``stargraph.replay.cassettes`` is the surface task 3.28 ships;
its absence trips the third test (and proves the cassette layer round-trip
is what 3.28 must deliver).
"""

from __future__ import annotations

import importlib
from typing import Any

from stargraph.tools.decorator import tool
from stargraph.tools.spec import ReplayPolicy, SideEffects


def test_write_default_policy_is_must_stub() -> None:
    """write side-effects + no explicit policy -> must_stub (FR-26 default)."""

    @tool(
        name="default_writer",
        namespace="replay",
        version="1",
        side_effects=SideEffects.write,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    def default_writer() -> dict[str, Any]:
        return {}

    spec: Any = default_writer.spec  # type: ignore[attr-defined]
    assert spec.replay_policy == ReplayPolicy.must_stub


def test_external_default_policy_is_must_stub() -> None:
    """external side-effects + no explicit policy -> must_stub (FR-26 default)."""

    @tool(
        name="default_external",
        namespace="replay",
        version="1",
        side_effects=SideEffects.external,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    def default_external() -> dict[str, Any]:
        return {}

    spec: Any = default_external.spec  # type: ignore[attr-defined]
    assert spec.replay_policy == ReplayPolicy.must_stub


def test_cassette_layer_round_trip() -> None:
    """The cassette surface task 3.28 ships round-trips a tool-call entry."""
    mod = importlib.import_module(
        "stargraph.replay.cassettes",  # pyright: ignore[reportMissingImports]
    )
    cassette: Any = mod.ToolCallCassette()
    cassette.record("ns.tool", {"x": 1}, {"y": 2})
    assert cassette.get("ns.tool", {"x": 1}) == {"y": 2}
    assert cassette.get("ns.tool", {"x": 2}) is None
