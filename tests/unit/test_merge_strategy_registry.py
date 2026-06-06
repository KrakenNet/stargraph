# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the field-level merge-strategy registry (FR-11).

Per design §3.6 / requirements FR-11, ``stargraph.runtime.merge`` exposes a
``MergeRegistry`` keyed on the four built-in names (``last-write``,
``append``, ``max``, ``min``) plus dynamic ``custom:<callable_ref>`` lookup.

This file is the **TDD-RED** half of the FR-11 cycle.  The
``stargraph.runtime.merge`` module does not yet exist; imports are deferred
inside each test body so the file parses cleanly under ruff/pyright while
the tests themselves fail with :class:`ImportError` until task 3.11 lands
the implementation.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Protocol, cast

import pytest

if TYPE_CHECKING:

    class _Strategy(Protocol):
        def __call__(self, values: list[Any]) -> Any: ...

    class _MergeRegistry(Protocol):
        def get(self, name: str) -> _Strategy: ...

    class _MergeModule(Protocol):
        MergeRegistry: type[_MergeRegistry]


def _load() -> _MergeModule:
    """Deferred import of the not-yet-built ``stargraph.runtime.merge`` module."""
    return cast("_MergeModule", importlib.import_module("stargraph.runtime.merge"))


def test_registry_resolves_last_write() -> None:
    """``last-write`` is the documented default; lookup must return a callable."""
    reg = _load().MergeRegistry()
    strategy = reg.get("last-write")
    # last-write semantics: pick the last value in the iteration order.
    assert strategy([1, 2, 3]) == 3


def test_registry_resolves_append() -> None:
    """``append`` concatenates branch outputs (LangGraph ``Topic`` analogue)."""
    reg = _load().MergeRegistry()
    strategy = reg.get("append")
    assert strategy([[1, 2], [3], [4, 5]]) == [1, 2, 3, 4, 5]


def test_registry_resolves_max() -> None:
    """``max`` is BinaryOperatorAggregate over ``builtins.max`` (FR-11)."""
    reg = _load().MergeRegistry()
    strategy = reg.get("max")
    assert strategy([3, 7, 1, 5]) == 7


def test_registry_resolves_min() -> None:
    """``min`` is BinaryOperatorAggregate over ``builtins.min`` (FR-11)."""
    reg = _load().MergeRegistry()
    strategy = reg.get("min")
    assert strategy([3, 7, 1, 5]) == 1


def test_registry_resolves_custom_callable_ref() -> None:
    """``custom:<dotted.path>`` resolves a user-supplied callable.

    Per FR-11 the registry must accept arbitrary dotted-path callable refs
    using the same import-resolution discipline as the tool registry.
    ``builtins.sum`` is a stable reference present in every Python runtime.
    """
    reg = _load().MergeRegistry()
    strategy = reg.get("custom:builtins.sum")
    assert strategy([1, 2, 3, 4]) == 10


def test_registry_unknown_strategy_raises() -> None:
    """Unknown strategy names must raise rather than silently default."""
    reg = _load().MergeRegistry()
    with pytest.raises((KeyError, ValueError, LookupError)):
        reg.get("not-a-real-strategy")


def test_registry_custom_unresolvable_raises() -> None:
    """``custom:<bogus.path>`` must raise; silent fall-through hides bugs."""
    reg = _load().MergeRegistry()
    with pytest.raises((ImportError, AttributeError, ValueError, LookupError)):
        reg.get("custom:does.not.exist.callable")
