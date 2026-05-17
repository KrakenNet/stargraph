# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``graph/hash._state_schema_signature`` force-loud branch (T18).

Pins that the placeholder ``repr(state_schema)`` fallback at
``graph/hash.py:216-228`` is replaced with a force-loud
:class:`IRValidationError` (FR-6). The :class:`pydantic.BaseModel`-subclass
branch is UNCHANGED -- INV-1 reproducibility depends on it.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from harbor.errors import IRValidationError
from harbor.graph.hash import _state_schema_signature

pytestmark = pytest.mark.unit


@pytest.mark.unit
def test_state_schema_signature_raises_irvalidationerror_on_non_basemodel() -> None:
    """Non-:class:`BaseModel` input raises :class:`IRValidationError` instead of
    silently returning ``repr(state_schema)`` (T18, FR-6)."""
    with pytest.raises(IRValidationError):
        _state_schema_signature({"x": "str"})


@pytest.mark.unit
def test_state_schema_signature_succeeds_on_basemodel_subclass() -> None:
    """A real :class:`BaseModel` subclass returns its serialization-mode JSON
    schema (INV-1 preserved by T18)."""

    class _Schema(BaseModel):
        x: str
        y: int

    out = _state_schema_signature(_Schema)
    assert isinstance(out, dict)
    assert "properties" in out
    assert set(out["properties"].keys()) == {"x", "y"}
