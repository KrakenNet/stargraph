# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :func:`stargraph.nodes.retrieval._coerce_metadata`."""

from __future__ import annotations

from typing import Any

import pytest

from stargraph.nodes.retrieval import _coerce_metadata  # pyright: ignore[reportPrivateUsage]


@pytest.mark.unit
def test_coerce_metadata_preserves_scalar_entries() -> None:
    meta: dict[str, Any] = {
        "title": "ops note",
        "rank": 3,
        "score": 0.75,
        "active": True,
        "stale": False,
    }

    assert _coerce_metadata(meta) == meta


@pytest.mark.unit
def test_coerce_metadata_drops_non_scalar_entries() -> None:
    meta: dict[str, Any] = {
        "nested": {"owner": "alice"},
        "tags": ["runbook", "ops"],
        "missing": None,
    }

    assert _coerce_metadata(meta) == {}


@pytest.mark.unit
def test_coerce_metadata_returns_empty_dict_for_empty_input() -> None:
    assert _coerce_metadata({}) == {}


@pytest.mark.unit
def test_coerce_metadata_keeps_only_scalar_subset() -> None:
    meta: dict[str, Any] = {
        "id": "doc-1",
        "priority": 5,
        "confidence": 0.95,
        "verified": True,
        "payload": {"ignore": "nested"},
        "chunks": ["a", "b"],
        "notes": None,
    }

    assert _coerce_metadata(meta) == {
        "id": "doc-1",
        "priority": 5,
        "confidence": 0.95,
        "verified": True,
    }
