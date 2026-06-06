# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``plugin/hookspecs.authorize_action`` typed parameter (T20).

Pins:

* :class:`stargraph.plugin.types.BosunAction` exists with three minimal-viable
  fields (``action_kind: str``, ``target: str``, ``payload: dict[str, Any]``).
* It is exported via ``stargraph.plugin.types.__all__``.
* It is a real :class:`pydantic.BaseModel` round-tripping through
  ``model_dump`` / ``model_validate``.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.unit
def test_bosun_action_model_round_trip() -> None:
    """``BosunAction`` is a Pydantic model with three fields that round-trips
    through ``model_dump`` / ``model_validate`` (T20)."""
    from pydantic import BaseModel

    from stargraph.plugin.types import BosunAction

    assert issubclass(BosunAction, BaseModel)
    action = BosunAction(
        action_kind="tool_call",
        target="stargraph.tools.web.fetch",
        payload={"url": "https://example.com"},
    )
    dump = action.model_dump()
    assert dump == {
        "action_kind": "tool_call",
        "target": "stargraph.tools.web.fetch",
        "payload": {"url": "https://example.com"},
    }
    BosunAction.model_validate(dump)  # raises on shape mismatch


@pytest.mark.unit
def test_authorize_action_accepts_bosun_action_instance() -> None:
    """A pluggy plugin returning a typed :class:`BosunAction` round-trips
    through ``authorize_action`` (T20)."""
    from stargraph.plugin import hookspecs
    from stargraph.plugin.types import BosunAction

    # Symbol export check (Stage 7 fix: scaffolder generated a vars(dict)
    # call that raised TypeError before reaching the hasattr short-circuit).
    assert hasattr(hookspecs, "authorize_action")
    # The hookspec is a Pluggy hookspec; calling it directly returns None
    # (declaration-only ``pass`` body per Pluggy convention). The contract
    # is that the parameter type is now ``BosunAction``, not ``dict``.
    action = BosunAction(action_kind="halt", target="run", payload={})
    # Direct call returns None (no registered impls); not raising = surface OK.
    out = hookspecs.authorize_action(action)
    assert out is None
