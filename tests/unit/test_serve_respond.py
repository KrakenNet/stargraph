# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``serve/respond._resolve_run`` registry lookup (T17 mirror).

Mirrors ``tests/unit/test_serve_lifecycle.py`` for the respond-side
resolver. Same contract: ``deps["runs"].get(run_id)`` then explicit
override, then loud raise.
"""

from __future__ import annotations

from typing import Any

import pytest

from harbor.errors import HarborRuntimeError
from harbor.graph import Graph, GraphRun
from harbor.ir import IRDocument, NodeSpec

pytestmark = pytest.mark.unit


def _graph() -> Graph:
    return Graph(
        IRDocument(
            ir_version="1.0.0",
            id="run:respond-resolve-test",
            nodes=[NodeSpec(id="a", kind="echo")],
        ),
    )


@pytest.mark.unit
def test_resolve_run_returns_run_from_registry_when_present() -> None:
    """``_resolve_run`` returns the registry-stored :class:`GraphRun` (T17)."""
    from harbor.serve import respond

    run = GraphRun(run_id="r1", graph=_graph())
    deps = {"runs": {"r1": run}}
    out = respond._resolve_run("r1", None, deps=deps)  # pyright: ignore[reportPrivateUsage]
    assert out is run


@pytest.mark.unit
def test_resolve_run_raises_when_absent() -> None:
    """``_resolve_run`` raises :class:`HarborRuntimeError` when missing (T17)."""
    from harbor.serve import respond

    deps: dict[str, Any] = {"runs": {}}
    with pytest.raises(HarborRuntimeError):
        respond._resolve_run("missing", None, deps=deps)  # pyright: ignore[reportPrivateUsage]
