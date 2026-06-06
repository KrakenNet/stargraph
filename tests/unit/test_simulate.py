# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :meth:`stargraph.graph.Graph.simulate` (FR-9, AC-12.1).

Covers the offline-simulator contract:

* ``simulate(fixtures)`` returns a :class:`SimulationResult` with the
  per-rule firing trace in IR declaration order.
* No tool, no LLM, no checkpointer is touched -- the method walks only
  in-memory IR state and the caller-supplied fixtures dict.
* A node declared in the IR with no fixture entry raises
  :class:`stargraph.errors.SimulationError` with structured
  ``violation="missing-fixture"`` context.

The "no I/O" assertion is enforced two ways: (a) by stubbing every
runtime/checkpoint hook with a sentinel that fails the test if invoked,
and (b) by a static-import guard that scans the module source for
forbidden side-effect patterns (``checkpointer.write`` / ``tool.acall``).
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from stargraph.errors import SimulationError
from stargraph.graph import Graph
from stargraph.graph.definition import RuleFiring, SimulationResult
from stargraph.ir._models import (
    GotoAction,
    HaltAction,
    IRDocument,
    NodeSpec,
    RuleSpec,
)


def _build_ir() -> IRDocument:
    """Two-node, two-rule IR -- one rule fires, the other does not."""
    return IRDocument(
        ir_version="1.0.0",
        id="run:test-simulate",
        nodes=[
            NodeSpec(id="node_a", kind="echo"),
            NodeSpec(id="node_b", kind="echo"),
        ],
        rules=[
            RuleSpec(
                id="rule_a_fires",
                when="node_a produced output",
                then=[GotoAction(target="node_b"), HaltAction(reason="done")],
            ),
            RuleSpec(
                id="rule_no_match",
                when="some unrelated condition",
                then=[HaltAction(reason="never")],
            ),
        ],
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_simulate_returns_rule_firing_trace() -> None:
    """``simulate`` returns a :class:`SimulationResult` with per-rule firings in IR order."""
    graph = Graph(_build_ir())
    fixtures: dict[str, Any] = {"node_a": "alpha", "node_b": "beta"}

    result = await graph.simulate(fixtures)

    assert isinstance(result, SimulationResult)
    # Rule firings preserve IR declaration order.
    assert [rf.rule_id for rf in result.rule_firings] == [
        "rule_a_fires",
        "rule_no_match",
    ]
    # First rule fires (node_a appears in its ``when``); second does not.
    fired = {rf.rule_id: rf for rf in result.rule_firings}
    assert fired["rule_a_fires"].fired is True
    assert fired["rule_a_fires"].matched_nodes == ("node_a",)
    # Action kinds are recorded for every rule, fired or not.
    assert fired["rule_a_fires"].action_kinds == ("goto", "halt")
    assert fired["rule_no_match"].fired is False
    assert fired["rule_no_match"].matched_nodes == ()
    assert fired["rule_no_match"].action_kinds == ("halt",)
    # node_outputs is a shallow copy of the caller's fixtures.
    assert result.node_outputs == fixtures
    assert result.node_outputs is not fixtures


@pytest.mark.unit
@pytest.mark.asyncio
async def test_simulate_missing_fixture_raises_structured_error() -> None:
    """A node declared in the IR with no fixture entry raises :class:`SimulationError`."""
    graph = Graph(_build_ir())

    with pytest.raises(SimulationError) as excinfo:
        await graph.simulate({"node_a": "alpha"})  # node_b missing

    err = excinfo.value
    assert err.context["node_id"] == "node_b"
    assert err.context["violation"] == "missing-fixture"
    assert "node_b" in err.message


@pytest.mark.unit
@pytest.mark.asyncio
async def test_simulate_returns_frozen_result() -> None:
    """``SimulationResult`` is frozen so callers cannot mutate the trace."""
    graph = Graph(_build_ir())
    result = await graph.simulate({"node_a": 1, "node_b": 2})

    with pytest.raises((AttributeError, TypeError)):
        # frozen=True dataclass -- assignment must be refused.
        result.rule_firings = ()  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_simulate_handles_empty_rules() -> None:
    """An IR with nodes but no rules yields an empty firing trace (no error)."""
    ir = IRDocument(
        ir_version="1.0.0",
        id="run:test-simulate-norules",
        nodes=[NodeSpec(id="only_node", kind="echo")],
    )
    graph = Graph(ir)

    result = await graph.simulate({"only_node": "x"})

    assert result.rule_firings == ()
    assert result.node_outputs == {"only_node": "x"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_simulate_does_not_invoke_tools_llms_or_checkpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``simulate`` must not touch the runtime checkpoint / tool / LLM seams.

    Patches :mod:`stargraph.checkpoint`, :mod:`stargraph.runtime.tool_exec`, and
    :mod:`stargraph.runtime.dispatch` with sentinel callables that fail the
    test if invoked. ``simulate`` must complete without tripping any.
    """
    sentinel_calls: list[str] = []

    def _trip(name: str) -> Any:
        def _fn(*args: object, **kwargs: object) -> None:
            del args, kwargs
            sentinel_calls.append(name)
            pytest.fail(f"simulate() must not invoke {name}()")

        return _fn

    # Patch the concrete checkpoint writer modules directly. ``simulate``
    # must never reach a write coroutine, so swapping the SQLite/Postgres
    # write methods with the sentinel is the strongest local guard.
    import stargraph.checkpoint.postgres as _ck_pg
    import stargraph.checkpoint.sqlite as _ck_sqlite

    monkeypatch.setattr(
        _ck_sqlite.SQLiteCheckpointer,
        "write",
        _trip("SQLiteCheckpointer.write"),
        raising=False,
    )
    monkeypatch.setattr(
        _ck_pg.PostgresCheckpointer,
        "write",
        _trip("PostgresCheckpointer.write"),
        raising=False,
    )

    # Patch the tool / dispatch entry points (every async surface).
    import stargraph.runtime.dispatch as _dp
    import stargraph.runtime.tool_exec as _te

    for name, _member in inspect.getmembers(_te, inspect.iscoroutinefunction):
        monkeypatch.setattr(_te, name, _trip(f"runtime.tool_exec.{name}"))
    for name, _member in inspect.getmembers(_dp, inspect.iscoroutinefunction):
        monkeypatch.setattr(_dp, name, _trip(f"runtime.dispatch.{name}"))

    graph = Graph(_build_ir())
    result = await graph.simulate({"node_a": "x", "node_b": "y"})

    assert isinstance(result, SimulationResult)
    assert sentinel_calls == []  # nothing should have been tripped


@pytest.mark.unit
def test_simulate_source_does_not_reference_io_seams() -> None:
    """Static guard: simulate's module source must not call the I/O seams.

    Belt-and-braces with the runtime-mock test above. Greps the source of
    :mod:`stargraph.graph.definition` for the patterns the task ``Verify``
    line forbids (``checkpointer.write`` / ``tool.acall``). Catches future
    refactors that route simulate through a write/dispatch path.
    """
    import stargraph.graph.definition as _gd

    src = inspect.getsource(_gd)
    forbidden = ("checkpointer.write", "tool.acall", ".acall(", "llm.forward(")
    found = [pat for pat in forbidden if pat in src]
    assert not found, f"definition.py references forbidden I/O seam(s): {found}"


@pytest.mark.unit
def test_rule_firing_is_frozen() -> None:
    """:class:`RuleFiring` is a frozen dataclass (replay-stability invariant)."""
    rf = RuleFiring(
        rule_id="r",
        fired=True,
        matched_nodes=("n",),
        action_kinds=("goto",),
    )

    with pytest.raises((AttributeError, TypeError)):
        rf.fired = False  # pyright: ignore[reportAttributeAccessIssue]
