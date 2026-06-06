# SPDX-License-Identifier: Apache-2.0
"""FR-5/FR-25 round-trip test for the DSPy adapter seam (Task 3.55).

Verifies the full Pydantic state -> DSPy module -> Pydantic state cycle
through :class:`DSPyNode.execute`:

1. Stargraph state-field values are projected to DSPy signature input names
   per ``signature_map``.
2. The wrapped DSPy module returns a result.
3. :meth:`DSPyNode._project_outputs` hands back a dict the execution loop
   can merge into the next state via the field-merge registry (FR-11).

The wrapped "DSPy module" is an inert fixture so the test is hermetic --
no LM, no network, no DSPy adapter selection logic. The seam contract
under test is the projection plumbing in ``DSPyNode``; the loud-fail
adapter behaviour is covered by ``test_dspy_loud_fallback.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

# Skip cleanly if dspy isn't installed (matches loud-fallback test pattern).
pytest.importorskip("dspy", reason="dspy required for FR-5/FR-25 round-trip tests")

from stargraph.adapters.dspy import bind


class _RoundTripState(BaseModel):
    """Minimal stargraph run-state model used by the round-trip test."""

    user_query: str
    answer: str = ""


class _EchoModule:
    """Inert DSPy-module stand-in: maps the input field to the output field.

    Returns a plain dict keyed by the DSPy signature *output* name
    (``answer_text``), which :class:`DSPyNode._project_outputs` passes
    through unchanged. The test then confirms the returned dict is mergeable
    into the stargraph state model.
    """

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        return {"answer_text": f"echo:{kwargs['question']}"}


class _Ctx:
    """Minimal :class:`ExecutionContext` impl (Phase-1 placeholder shape)."""

    run_id: str = "test-run"


@pytest.mark.asyncio
async def test_round_trip_pydantic_state_through_dspy_module() -> None:
    """State -> module -> state round-trip via ``signature_map`` (FR-5/FR-25).

    Stargraph state field ``user_query`` projects to DSPy input ``question``;
    the module returns ``answer_text``, which the stargraph state field
    ``answer`` would receive via the field-merge registry. The test asserts
    the dict ``DSPyNode.execute`` returns is shaped for that merge path.
    """
    node = bind(
        module=_EchoModule(),
        signature_map={"user_query": "question"},
    )

    state = _RoundTripState(user_query="what is stargraph?")
    result = await node.execute(state, _Ctx())

    # The module's output dict round-trips through ``_project_outputs`` so
    # the execution loop receives a dict ready for the field-merge registry.
    assert isinstance(result, dict)
    assert result == {"answer_text": "echo:what is stargraph?"}

    # Confirm the result is mergeable into the stargraph state model -- i.e.
    # the round-trip closes the loop on a real Pydantic instance, not just
    # a bag of values. We rename the DSPy output to the stargraph field here
    # because Phase-2 ``signature_map`` only projects inputs; the
    # field-merge registry (FR-11) handles output renaming end-to-end.
    merged = state.model_copy(update={"answer": result["answer_text"]})
    assert merged.answer == "echo:what is stargraph?"
    assert merged.user_query == "what is stargraph?"
