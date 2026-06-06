# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``serve/lifecycle._resolve_run`` registry lookup (T17).

Pins that ``_resolve_run`` checks ``deps["runs"].get(run_id)`` before
raising. Explicit ``_run=`` override takes precedence over registry
lookup; missing-run path still surfaces a :class:`StargraphRuntimeError`.
"""

from __future__ import annotations

from typing import Any

import pytest

from stargraph.errors import StargraphRuntimeError
from stargraph.graph import Graph, GraphRun
from stargraph.ir import IRDocument, NodeSpec

pytestmark = pytest.mark.unit


def _graph() -> Graph:
    return Graph(
        IRDocument(
            ir_version="1.0.0",
            id="run:resolve-test",
            nodes=[NodeSpec(id="a", kind="echo")],
        ),
    )


@pytest.mark.unit
def test_resolve_run_returns_run_from_registry_when_present() -> None:
    """``_resolve_run(run_id)`` returns the :class:`GraphRun` stored in
    ``deps["runs"][run_id]`` (T17)."""
    from stargraph.serve import lifecycle

    run = GraphRun(run_id="r1", graph=_graph())
    deps = {"runs": {"r1": run}}
    out = lifecycle._resolve_run("r1", None, deps=deps)  # pyright: ignore[reportPrivateUsage]
    assert out is run


@pytest.mark.unit
def test_resolve_run_raises_when_absent() -> None:
    """``_resolve_run`` raises :class:`StargraphRuntimeError` when ``run_id`` is
    not in ``deps["runs"]`` (T17)."""
    from stargraph.serve import lifecycle

    deps: dict[str, Any] = {"runs": {}}
    with pytest.raises(StargraphRuntimeError):
        lifecycle._resolve_run("missing", None, deps=deps)  # pyright: ignore[reportPrivateUsage]
