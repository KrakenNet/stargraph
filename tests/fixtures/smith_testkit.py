# SPDX-License-Identifier: Apache-2.0
"""Shared test kit for the smith integration suites.

Every smith's tests drive the same lifecycle-node sequence against a stubbed
generator, so ``CTX`` (a throwaway ExecutionContext), ``stub_build`` (patch a
Build node's program to return a fixed dict offline), and ``drive`` (thread state
through a node list) are identical across suites and live here. The per-suite
``isolated_home`` + ``offline_web`` fixtures stay in each smith's conftest (only
the ``*_HOME`` env-var name differs).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Callable

    from stargraph.nodes.base import ExecutionContext
    from stargraph.skills._smith.build import SmithBuild
    from stargraph.skills._smith.state import SmithState

CTX = cast("ExecutionContext", SimpleNamespace(run_id="smith-test"))


def stub_build(build_cls: Callable[[], SmithBuild], gen: dict[str, Any]) -> SmithBuild:
    """Construct a smith's Build node with its program stubbed to return ``gen``.

    The build loop's only nondeterministic seam is ``program.generate``; pinning it
    to a constant dict makes the generate → gate → repair loop run fully offline.
    """
    b = build_cls()
    b._program.generate = lambda brief, lessons, last_findings, relevant_context="": gen  # type: ignore[assignment]
    return b


async def drive[StateT: SmithState](nodes: list[Any], state: StateT) -> StateT:
    """Execute lifecycle nodes in order, threading state via ``model_copy(update=…)``."""
    for node in nodes:
        out = await node.execute(state, CTX)
        state = state.model_copy(update=out)
    return state
