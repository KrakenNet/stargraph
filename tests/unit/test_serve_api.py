# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``serve/api._RunsPage.cursor`` field (T14).

Pins the additive Pydantic field ``cursor: str | None = None`` on
``_RunsPage`` -- v1 returns ``None`` (opaque cursor encoding lands in
Phase 3 per scope.md "Deferred").
"""

from __future__ import annotations

import pytest

from harbor.serve.api import _RunsPage

pytestmark = pytest.mark.unit


@pytest.mark.unit
def test_runs_page_default_cursor_is_none() -> None:
    """A ``_RunsPage`` constructed without ``cursor=`` defaults to ``None`` (T14)."""
    page = _RunsPage(items=[], total=0, limit=10, offset=0)
    assert page.cursor is None


@pytest.mark.unit
def test_runs_page_serializes_cursor_field() -> None:
    """An explicit ``cursor=`` value round-trips through ``model_dump`` (T14)."""
    page = _RunsPage(items=[], total=0, limit=10, offset=0, cursor="opaque-cursor-v1")
    dump = page.model_dump()
    assert "cursor" in dump
    assert dump["cursor"] == "opaque-cursor-v1"
