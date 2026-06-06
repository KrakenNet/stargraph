# SPDX-License-Identifier: Apache-2.0
"""Unit tests for FR-12 race/any side-effect rejection (design §3.6.1).

Per requirements FR-12, ``Graph.__init__`` must refuse to compile any IR
where a ``race`` or ``any`` parallel block has a branch whose tools include
``side_effects`` ∈ ``{write, external}`` -- unless the branch explicitly
opts in via ``allow_unsafe_cancel: true``. The rationale (design §3.6.1) is
that ``race``/``any`` cancel losing branches mid-flight; cancelling a
write/external tool risks half-committed I/O.

The Phase-1 IR (:class:`stargraph.ir._models.ParallelBlock`) does not yet
carry the per-branch tool-side-effect map nor the ``allow_unsafe_cancel``
flag (those land alongside the same reducer-aware IR extension noted in
task 3.11). The compile-time check is therefore staged as a structural
hook in :func:`stargraph.graph.definition._check_race_side_effects` that
accepts the would-be IR-derived side-effect map and allow-list as
explicit parameters. These tests exercise the hook directly so the
violation/opt-in semantics are pinned today; when the IR grows the
fields, ``Graph.__init__`` populates the parameters from the IR and the
tests still pass without modification.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Protocol, cast

import pytest

from stargraph.errors import IRValidationError
from stargraph.ir._models import ParallelBlock
from stargraph.tools.spec import SideEffects

if TYPE_CHECKING:

    class _CheckFn(Protocol):
        def __call__(
            self,
            parallel_blocks: list[ParallelBlock],
            *,
            side_effects_by_node: dict[str, SideEffects],
            allow_unsafe_cancel_nodes: frozenset[str],
        ) -> None: ...


def _load_check() -> _CheckFn:
    """Resolve the module-private ``_check_race_side_effects`` helper.

    Uses ``getattr`` to dodge pyright-strict's ``reportPrivateUsage`` on
    direct attribute access while keeping the call site fully typed via
    the :class:`_CheckFn` Protocol.
    """
    mod = importlib.import_module("stargraph.graph.definition")
    return cast("_CheckFn", getattr(mod, "_check_race_side_effects"))  # noqa: B009


def test_race_branch_with_write_side_effect_raises() -> None:
    """``race`` strategy + branch with ``side_effects=write`` ⇒ ``IRValidationError``."""
    block = ParallelBlock(targets=["writer_node"], join="merge", strategy="race")
    check = _load_check()

    with pytest.raises(IRValidationError) as excinfo:
        check(
            [block],
            side_effects_by_node={"writer_node": SideEffects.write},
            allow_unsafe_cancel_nodes=frozenset(),
        )

    err = excinfo.value
    assert err.context["violation"] == "unsafe-cancel"
    assert "writer_node" in err.context["node_ids"]
    # Strategy is part of the structured context so operators can audit
    # which block triggered the rejection (race vs any).
    assert err.context["strategy"] == "race"


def test_race_branch_with_allow_unsafe_cancel_constructs() -> None:
    """Opt-in ``allow_unsafe_cancel`` on the branch suppresses the rejection."""
    block = ParallelBlock(targets=["writer_node"], join="merge", strategy="race")
    check = _load_check()

    # No exception: the explicit opt-in honors design §3.6.1's escape hatch.
    check(
        [block],
        side_effects_by_node={"writer_node": SideEffects.write},
        allow_unsafe_cancel_nodes=frozenset({"writer_node"}),
    )
