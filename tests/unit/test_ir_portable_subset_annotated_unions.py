# SPDX-License-Identifier: Apache-2.0
"""Portable-subset AST walker for ``stargraph.ir._models`` (FR-10, FR-11).

This test parses ``src/stargraph/ir/_models.py`` with :mod:`ast` (no runtime
import) and enforces two independent portable-subset rules. Each rule lives
in its own ``test_*`` function so a failure points cleanly at the violated
contract:

1. **FR-10 / AC-13.4** -- *Annotated-only constraints*. Every constraint on
   an IR field must be expressed via ``Annotated[X, Field(<constraints>)]``.
   Two failure modes are flagged:

   * ``field: T = Field(..., ge=0, le=10)`` -- a bare ``Field(...)`` default
     carrying *constraint* kwargs (``ge``, ``le``, ``gt``, ``lt``,
     ``min_length``, ``max_length``, ``pattern``, ``regex``, ``multiple_of``,
     ``max_digits``, ``decimal_places``, ``min_items``, ``max_items``).
     ``Field(default=...)`` and ``Field(default_factory=...)`` without
     constraint kwargs are accepted -- they carry no schema constraint.

   * Use of Pydantic constrained-type *shortcuts* (``condecimal``, ``conint``,
     ``conlist``, ``constr``, ``confloat``) anywhere in the module. These
     compress constraints into a callable that the JSON Schema round-trip
     can't replay verbatim.

2. **FR-11 / AC-13.5** -- *Top-level tagged unions only*. The IR's
   discriminated-union pattern is ``Annotated[Union[...], Field(discriminator=...)]``
   (or its PEP-604 form ``Annotated[A | B | ..., Field(discriminator=...)]``).
   This subscript expression must appear only at the top level of an
   annotation -- never nested inside ``list[...]``, ``Sequence[...]``,
   ``dict[..., Union[...]]``, ``Optional[...]``, or any other container
   subscript. Nested discriminated unions defeat the FR-11 invariant that
   action variants are inspectable without recursion.

The walker has no allow-list -- ``_models.py`` is the only module under
scrutiny because every IR Pydantic type lives there.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
IR_MODELS_FILE: Path = PROJECT_ROOT / "src" / "stargraph" / "ir" / "_models.py"

# Pydantic ``Field(...)`` kwargs that impose a *schema constraint* (rather than
# merely supplying a default). Any of these in a bare ``Field(...)`` default
# means the field carries an implicit constraint that should have been hoisted
# into ``Annotated[X, Field(...)]``.
_CONSTRAINT_KWARGS: frozenset[str] = frozenset(
    {
        "ge",
        "le",
        "gt",
        "lt",
        "min_length",
        "max_length",
        "pattern",
        "regex",
        "multiple_of",
        "max_digits",
        "decimal_places",
        "min_items",
        "max_items",
    },
)

# Pydantic constrained-type shortcuts. Any reference to these names (call site
# *or* annotation site) is a portable-subset violation: the constraints must be
# spelled out as ``Annotated[X, Field(...)]`` so JSON Schema export round-trips.
_FORBIDDEN_SHORTCUTS: frozenset[str] = frozenset(
    {"condecimal", "conint", "conlist", "constr", "confloat"},
)

# Container subscripts that, when wrapping an ``Annotated[Union[...], Field(discriminator=...)]``,
# constitute a *nested* discriminated union (FR-11 violation). The list is not
# exhaustive of every container Python knows -- it covers the shapes the IR
# could plausibly use to nest a tagged union (FR-11 forbids them all by stating
# tagged unions live only at the top level of an annotation).
_CONTAINER_NAMES: frozenset[str] = frozenset(
    {
        "list",
        "List",
        "set",
        "Set",
        "frozenset",
        "FrozenSet",
        "tuple",
        "Tuple",
        "dict",
        "Dict",
        "Sequence",
        "MutableSequence",
        "Iterable",
        "Iterator",
        "Mapping",
        "MutableMapping",
        "Optional",
        "Union",
    },
)


def _load_models_ast() -> ast.Module:
    """Parse ``src/stargraph/ir/_models.py`` once per call (cheap; small file)."""
    return ast.parse(
        IR_MODELS_FILE.read_text(encoding="utf-8"),
        filename=str(IR_MODELS_FILE),
    )


def _line(node: ast.AST) -> int:
    """Return ``node.lineno`` or ``0`` if missing."""
    return getattr(node, "lineno", 0)


def _is_field_call(node: ast.expr) -> bool:
    """``True`` iff ``node`` is a call to the bare name ``Field``."""
    return (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Field"
    )


def _field_call_constraint_kwargs(node: ast.Call) -> list[str]:
    """Return the names of constraint-bearing kwargs passed to a ``Field(...)`` call."""
    return [kw.arg for kw in node.keywords if kw.arg is not None and kw.arg in _CONSTRAINT_KWARGS]


def _is_annotated_subscript(node: ast.expr) -> bool:
    """``True`` iff ``node`` is ``Annotated[...]`` (subscript of bare ``Annotated``)."""
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "Annotated"
    )


def _annotated_field_call(node: ast.Subscript) -> ast.Call | None:
    """Return the ``Field(...)`` call inside ``Annotated[X, Field(...), ...]``, or ``None``.

    The first slice element is the wrapped type; subsequent elements are the
    metadata. We scan the metadata tuple for the first ``Field(...)`` call.
    """
    sl = node.slice
    if isinstance(sl, ast.Tuple):
        for elt in sl.elts[1:]:
            if _is_field_call(elt):
                # mypy: _is_field_call narrows to ast.Call
                assert isinstance(elt, ast.Call)
                return elt
    return None


def _is_discriminated_annotated_union(node: ast.AST) -> bool:
    """``True`` iff ``node`` is ``Annotated[<Union>, Field(discriminator=...), ...]``.

    Accepts both classic ``Union[A, B, ...]`` and PEP-604 ``A | B | ...`` as
    the wrapped type. The metadata must include a ``Field(...)`` call carrying
    a ``discriminator=...`` kwarg.
    """
    if not isinstance(node, ast.Subscript):
        return False
    if not _is_annotated_subscript(node):
        return False
    field_call = _annotated_field_call(node)
    if field_call is None:
        return False
    has_discriminator = any(kw.arg == "discriminator" for kw in field_call.keywords)
    if not has_discriminator:
        return False
    # The wrapped type is the first element of the slice tuple. It must be a
    # union-shaped expression: either ``Union[...]`` or a ``BinOp`` chain of
    # ``|`` operators. We don't *require* this for the ban (any
    # ``Annotated[X, Field(discriminator=...)]`` is by construction a tagged
    # union per Pydantic's contract), but checking it makes the helper's
    # name accurate and avoids false positives if some unrelated future
    # ``Field(discriminator=...)`` shape appears.
    sl = node.slice
    if not isinstance(sl, ast.Tuple) or not sl.elts:
        return False
    wrapped = sl.elts[0]
    if (
        isinstance(wrapped, ast.Subscript)
        and isinstance(wrapped.value, ast.Name)
        and wrapped.value.id == "Union"
    ):
        return True
    return isinstance(wrapped, ast.BinOp) and isinstance(wrapped.op, ast.BitOr)


def _build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    """Return ``id(child) -> parent`` for every node reachable from ``tree``.

    ``ast`` doesn't expose parents; we walk once and record them so the FR-11
    nesting check can ask "is this Subscript the slice of a container?".
    """
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _enclosing_subscript_value_name(
    node: ast.AST,
    parents: dict[int, ast.AST],
) -> str | None:
    """If ``node`` is (transitively) the slice of a ``Subscript``, return its base name.

    Walks up the parent chain. The first ``Subscript`` ancestor whose ``slice``
    contains ``node`` is the wrapping container. Returns the bare ``Name``
    of that ``Subscript``'s ``value`` (e.g. ``"list"`` for ``list[Annotated[...]]``)
    or ``None`` if no such ancestor exists or the wrapping subscript's value
    is not a bare ``Name`` (e.g. attribute access -- not flagged).
    """
    current: ast.AST = node
    while True:
        parent = parents.get(id(current))
        if parent is None:
            return None
        # Is ``current`` somewhere inside ``parent.slice``? The slice can
        # be the node itself, an element of a Tuple slice, or nested
        # deeper -- we only care that ``current`` is a descendant of
        # ``parent.slice`` (not of ``parent.value``).
        if isinstance(parent, ast.Subscript) and _is_descendant_of(current, parent.slice):
            if isinstance(parent.value, ast.Name):
                return parent.value.id
            return None
        # ``current`` is the ``parent.value`` -- keep climbing.
        current = parent


def _is_descendant_of(node: ast.AST, root: ast.AST) -> bool:
    """``True`` iff ``node is root`` or ``node`` is reachable from ``root``."""
    if node is root:
        return True
    return any(child is node for child in ast.walk(root))


def test_no_bare_field_with_constraint_kwargs() -> None:
    """FR-10 / AC-13.4: bare ``Field(..., ge=...)`` defaults are banned.

    Constraints must live in ``Annotated[X, Field(...)]``. A ``Field(...)`` call
    used as the *default* value of an ``AnnAssign`` (right of ``=``) carrying
    any constraint kwarg (``ge``, ``le``, ``min_length``, etc.) is a violation.
    Plain defaults (``Field(default=...)``, ``Field(default_factory=...)``)
    without constraint kwargs are accepted.
    """
    tree = _load_models_ast()
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign):
            continue
        value = node.value
        if value is None or not _is_field_call(value):
            continue
        # mypy: _is_field_call narrows
        assert isinstance(value, ast.Call)
        bad_kwargs = _field_call_constraint_kwargs(value)
        if bad_kwargs:
            offenders.append(
                f"{IR_MODELS_FILE.name}:{_line(node)}:Field({','.join(bad_kwargs)})",
            )
    assert not offenders, (
        "bare Field(...) defaults with constraint kwargs are banned -- "
        "use Annotated[X, Field(...)] instead "
        f"(FR-10, AC-13.4): {offenders!r}"
    )


def test_no_constrained_type_shortcuts() -> None:
    """FR-10 / AC-13.4: ``condecimal``/``conint``/``conlist``/``constr``/``confloat`` are banned.

    These shortcut names are forbidden anywhere in ``_models.py`` -- as
    annotations, as call targets, or even as imported symbols. Constraints
    must use the explicit ``Annotated[X, Field(...)]`` form.
    """
    tree = _load_models_ast()
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_SHORTCUTS:
            offenders.append(f"{IR_MODELS_FILE.name}:{_line(node)}:{node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_SHORTCUTS:
            offenders.append(f"{IR_MODELS_FILE.name}:{_line(node)}:{node.attr}")
        elif isinstance(node, ast.alias) and node.name in _FORBIDDEN_SHORTCUTS:
            offenders.append(f"{IR_MODELS_FILE.name}:{_line(node)}:{node.name}")
    assert not offenders, (
        "Pydantic constrained-type shortcuts (condecimal/conint/conlist/"
        "constr/confloat) are banned in IR models -- use Annotated[X, Field(...)] "
        f"(FR-10, AC-13.4): {offenders!r}"
    )


def test_discriminated_unions_only_at_top_level() -> None:
    """FR-11 / AC-13.5: ``Annotated[Union[...], Field(discriminator=...)]`` only at top level.

    The discriminated-union pattern must not be nested inside ``list[...]``,
    ``Sequence[...]``, ``dict[..., Union[...]]``, ``Optional[...]``, or any
    other container subscript. Top-level positions are accepted: the right-hand
    side of ``Action = Annotated[...]`` (an ``Assign`` value) and the
    ``annotation`` of an ``AnnAssign`` (a class-body field annotation).
    """
    tree = _load_models_ast()
    parents = _build_parent_map(tree)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not _is_discriminated_annotated_union(node):
            continue
        wrapping = _enclosing_subscript_value_name(node, parents)
        if wrapping is None:
            continue  # Top-level (module assign value or AnnAssign annotation).
        if wrapping in _CONTAINER_NAMES:
            offenders.append(
                f"{IR_MODELS_FILE.name}:{_line(node)}:nested-in-{wrapping}",
            )
    assert not offenders, (
        "discriminated unions must live at the top level of an annotation -- "
        "no nesting inside list/Sequence/dict/Optional/Union "
        f"(FR-11, AC-13.5): {offenders!r}"
    )
