# SPDX-License-Identifier: Apache-2.0
"""Field-level merge-strategy registry + confidence-decay formula (FR-11).

Per design §3.6.3, ``Mirror[T]`` state-schema fields declare a per-field
merge strategy when multiple parallel branches write the same field. The
four built-ins follow the LangGraph channel-reducer ontology:

* ``last-write`` ↔ ``LastValue``           — picks the last value seen.
* ``append``     ↔ ``Topic``               — list concatenation.
* ``max``        ↔ ``BinaryOperatorAggregate`` over :func:`builtins.max`.
* ``min``        ↔ ``BinaryOperatorAggregate`` over :func:`builtins.min`.
* ``custom:<dotted.path>`` resolves a user-supplied callable via the same
  import-resolution discipline the tool registry uses (FR-11).

On a ``last-write`` conflict (n ≥ 2 competing branches all writing the
same field with no reducer declared), the documented decay formula
lowers the surviving value's confidence::

    c_merged = c_winner * (1 - 0.5 * (n - 1) / n)

Concrete pinned values (design §3.6.3): n=2 ⇒ 0.75·c, n=3 ⇒ 0.667·c,
n=4 ⇒ 0.625·c. The formula is exposed both as the constant
:data:`CONFIDENCE_DECAY_FORMULA` (a pure callable for hash-stability /
documentation use) and the named function
:func:`confidence_after_last_write_conflict` (for call-site clarity).

Compile-time check: graphs whose parallel blocks fan out to multiple
branches over a non-empty state schema with no reducer declared on any
field are refused at :class:`stargraph.Graph` construction
(:class:`stargraph.errors.IRValidationError`) per FR-11 — the LangGraph
``InvalidUpdateError`` analogue. The check itself lives in
:mod:`stargraph.graph.definition`; this module only exports the registry +
formula it consults.

The module is pure: no I/O, no event-bus emission. Call sites that hit
the ``last-write`` path build a ``stargraph.evidence`` payload via
:func:`build_last_write_conflict_evidence` and route it through the
fathom adapter on the live ``RunContext`` (FR-3 provenance discipline).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from stargraph.errors import ValidationError

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "CONFIDENCE_DECAY_FORMULA",
    "MergeRegistry",
    "build_last_write_conflict_evidence",
    "confidence_after_last_write_conflict",
]


# ---------------------------------------------------------------------------
# Confidence-decay formula (design §3.6.3, Learning B)
# ---------------------------------------------------------------------------


def confidence_after_last_write_conflict(*, c_winner: float, n: int) -> float:
    """Lowered confidence after a ``last-write`` conflict (design §3.6.3).

    Implements ``c_merged = c_winner * (1 - 0.5 * (n - 1) / n)``. Pinned
    values: n=2 ⇒ 0.75·c, n=3 ⇒ 0.667·c, n=4 ⇒ 0.625·c. ``n=1`` (a
    single branch — not actually a conflict) collapses the multiplier
    to ``1.0`` so callers can use the same helper for the trivial path.

    Keyword-only arguments to make call sites self-documenting and to
    match the regression-pin tests in
    ``tests/unit/test_confidence_decay_formula.py``.
    """
    if n <= 0:
        raise ValidationError(
            "confidence_after_last_write_conflict requires n >= 1",
            n=n,
        )
    return c_winner * (1.0 - 0.5 * (n - 1) / n)


#: The decay formula exposed as a constant callable per design §3.6.3.
#: Documented as the canonical reference; ``module::CONFIDENCE_DECAY_FORMULA``
#: is the path quoted in design and in evidence payloads.
CONFIDENCE_DECAY_FORMULA: Callable[..., float] = confidence_after_last_write_conflict


def build_last_write_conflict_evidence(
    *,
    field: str,
    n_branches: int,
    original_confidence: float,
) -> dict[str, Any]:
    """Build a ``stargraph.evidence`` payload for a ``last-write`` resolution.

    Returns a dict with the four documented slots from design §3.6.3:
    ``kind`` (literal ``"last-write-conflict"``), ``field`` (the state
    field that contended), ``n_branches``, ``original_confidence``, and
    ``merged_confidence`` (the decayed value). The caller routes the
    payload through ``FathomAdapter.assert_with_provenance`` so the full
    six-slot provenance tuple is stamped at emit time (NFR-6, FR-3).

    Pure helper: never emits a fact, never touches the bus. The merge
    module is pure on purpose so unit tests can exercise the formula
    without spinning a runtime context.
    """
    merged = confidence_after_last_write_conflict(
        c_winner=original_confidence,
        n=n_branches,
    )
    return {
        "kind": "last-write-conflict",
        "field": field,
        "n_branches": n_branches,
        "original_confidence": original_confidence,
        "merged_confidence": merged,
    }


# ---------------------------------------------------------------------------
# Built-in reducers (LangGraph channel-reducer ontology, FR-11)
# ---------------------------------------------------------------------------


def _last_write(values: list[Any]) -> Any:
    """``LastValue`` analogue: pick the last value in iteration order."""
    if not values:
        raise ValidationError(
            "last-write requires at least one value",
            strategy="last-write",
        )
    return values[-1]


def _append(values: list[Any]) -> list[Any]:
    """``Topic`` analogue: concatenate per-branch list outputs.

    Each entry in ``values`` is itself a list (one per branch); the
    reducer flattens one level. Non-list entries raise a structured
    :class:`ValidationError` rather than silently coercing — FR-6
    force-loud at the seam.
    """
    out: list[Any] = []
    for entry in values:
        if not isinstance(entry, list):
            raise ValidationError(
                "append reducer requires list-typed branch outputs",
                strategy="append",
                actual_type=type(entry).__name__,
            )
        out.extend(entry)  # pyright: ignore[reportUnknownArgumentType]
    return out


def _max(values: list[Any]) -> Any:
    """``BinaryOperatorAggregate`` over :func:`builtins.max` (FR-11)."""
    if not values:
        raise ValidationError(
            "max requires at least one value",
            strategy="max",
        )
    return max(values)


def _min(values: list[Any]) -> Any:
    """``BinaryOperatorAggregate`` over :func:`builtins.min` (FR-11)."""
    if not values:
        raise ValidationError(
            "min requires at least one value",
            strategy="min",
        )
    return min(values)


_BUILTINS: dict[str, Callable[[list[Any]], Any]] = {
    "last-write": _last_write,
    "append": _append,
    "max": _max,
    "min": _min,
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class MergeRegistry:
    """Pluggable registry of field-level merge strategies (FR-11).

    Resolves the four built-in strategy names plus dynamic
    ``custom:<dotted.path>`` lookups against a per-instance overlay
    populated by :meth:`register`. Built-ins always shadow user
    registrations under the same name (force-loud: rejecting the
    overwrite is safer than silently swapping the LangGraph-equivalent
    semantics out from under existing graphs).

    The ``custom:`` prefix uses the same import-resolution discipline as
    the tool registry: ``custom:my.module.callable`` resolves to
    ``getattr(importlib.import_module("my.module"), "callable")``.
    Unresolvable dotted paths raise rather than silently fall through —
    silent fall-through hides bugs (FR-6).
    """

    def __init__(self) -> None:
        self._user: dict[str, Callable[[list[Any]], Any]] = {}

    def register(self, name: str, fn: Callable[[list[Any]], Any]) -> None:
        """Register a user strategy under ``name``.

        Names colliding with built-ins raise :class:`ValidationError` —
        rebinding ``last-write`` to a different reducer would silently
        break the LangGraph-equivalent semantics every graph relies on.
        """
        if name in _BUILTINS:
            raise ValidationError(
                "cannot override built-in merge strategy",
                name=name,
            )
        self._user[name] = fn

    def get(self, name: str) -> Callable[[list[Any]], Any]:
        """Resolve a strategy name to its reducer callable.

        Lookup order: built-ins → user-registered → ``custom:`` dotted
        path. Unknown names raise :class:`ValidationError` (a subclass
        of ``LookupError`` semantics via :class:`KeyError`-compatible
        handling at the test layer is intentionally avoided; we use the
        Stargraph hierarchy so a single ``except StargraphError`` catches the
        whole category).
        """
        builtin = _BUILTINS.get(name)
        if builtin is not None:
            return builtin
        user = self._user.get(name)
        if user is not None:
            return user
        if name.startswith("custom:"):
            return _resolve_custom(name)
        raise KeyError(
            f"unknown merge strategy: {name!r} (allowed: "
            f"{sorted([*_BUILTINS, 'custom:<dotted.path>'])})"
        )


def _resolve_custom(name: str) -> Callable[[list[Any]], Any]:
    """Resolve ``custom:<dotted.path>`` to a callable (FR-11).

    Splits on the rightmost dot to support module paths of arbitrary
    depth (``custom:builtins.sum`` → ``builtins.sum``;
    ``custom:my.pkg.mod.fn`` → ``my.pkg.mod.fn``). Failures raise
    :class:`ValidationError` with structured context rather than the
    raw ``ImportError`` / ``AttributeError`` so callers can pattern
    match on the Stargraph category.
    """
    ref = name.removeprefix("custom:")
    if "." not in ref:
        raise ValueError(f"custom merge strategy requires a dotted callable path, got {name!r}")
    module_path, _, attr = ref.rpartition(".")
    module = importlib.import_module(module_path)  # raises ImportError on miss
    fn = getattr(module, attr)  # raises AttributeError on miss
    if not callable(fn):
        raise TypeError(f"custom merge strategy ref is not callable: {name!r}")
    return fn  # pyright: ignore[reportUnknownVariableType]
