# SPDX-License-Identifier: Apache-2.0
"""Portable-subset AST walker for ``stargraph.ir._models`` (FR-7, FR-8, FR-9).

This test enforces three independent bans on the IR Pydantic model module by
parsing it with :mod:`ast` (no runtime import). Each assertion lives in its
own ``test_*`` function so a failure points cleanly at the violated rule:

1. **FR-7**: no Pydantic ``computed_field``, ``field_validator``, or
   ``model_validator`` decorators. Computed properties and runtime validator
   hooks break the JSON Schema round-trip and turn the IR into a Python-only
   contract.

2. **FR-8**: no PEP-695 ``type X = ...`` aliases (``ast.TypeAlias`` nodes).
   These require Python 3.12+ and cannot be expressed in the portable subset
   shared with non-CPython consumers. The legacy ``X: TypeAlias = ...``
   annotation form is *not* covered by ``ast.TypeAlias`` and is unaffected.

3. **FR-9**: no ``float`` appearing in a field's type annotation -- monetary
   and time-precision fields must use :class:`decimal.Decimal` or integer
   nanoseconds. We walk every :class:`ast.AnnAssign` in the module (top-level
   and class-body) and recurse into :class:`ast.Subscript` slices so generics
   like ``list[float]`` or ``Annotated[float, ...]`` also fail.

The walker has no allow-list -- ``_models.py`` is the only module under
scrutiny because every IR Pydantic type lives there.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
IR_MODELS_FILE: Path = PROJECT_ROOT / "src" / "stargraph" / "ir" / "_models.py"

_FORBIDDEN_DECORATORS: frozenset[str] = frozenset(
    {"computed_field", "field_validator", "model_validator"},
)


def _load_models_ast() -> ast.Module:
    """Parse ``src/stargraph/ir/_models.py`` once per call (cheap; tiny file)."""
    return ast.parse(
        IR_MODELS_FILE.read_text(encoding="utf-8"),
        filename=str(IR_MODELS_FILE),
    )


def _decorator_name(node: ast.expr) -> str | None:
    """Return the bare name of a decorator expression, or ``None`` if unknown.

    Handles the three shapes Pydantic decorators take in source:
    ``@field_validator`` (Name), ``@field_validator(...)`` (Call of Name),
    and ``@pydantic.field_validator(...)`` (Attribute / Call of Attribute).
    """
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _annotation_uses_float(node: ast.expr) -> bool:
    """``True`` iff the annotation expression mentions the ``float`` builtin.

    Recurses through :class:`ast.Subscript` (``list[float]``,
    ``Annotated[float, ...]``), :class:`ast.BinOp` for PEP-604 unions
    (``int | float``), and :class:`ast.Tuple` (subscript slices like
    ``dict[str, float]``).
    """
    if isinstance(node, ast.Name):
        return node.id == "float"
    if isinstance(node, ast.Subscript):
        return _annotation_uses_float(node.value) or _annotation_uses_float(node.slice)
    if isinstance(node, ast.Tuple):
        return any(_annotation_uses_float(elt) for elt in node.elts)
    if isinstance(node, ast.BinOp):
        return _annotation_uses_float(node.left) or _annotation_uses_float(node.right)
    if isinstance(node, ast.Attribute):
        # ``builtins.float`` or similar fully-qualified reference.
        return node.attr == "float"
    return False


def _line(node: ast.AST) -> int:
    """Return ``node.lineno`` or ``0`` if missing."""
    return getattr(node, "lineno", 0)


def test_no_computed_field_or_validator_decorators() -> None:
    """FR-7 / AC-13.1: no ``computed_field``/``field_validator``/``model_validator``."""
    tree = _load_models_ast()
    offenders: list[str] = []
    for node in ast.walk(tree):
        decorators: list[ast.expr] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            decorators = list(node.decorator_list)
        for dec in decorators:
            name = _decorator_name(dec)
            if name in _FORBIDDEN_DECORATORS:
                offenders.append(
                    f"{IR_MODELS_FILE.name}:{_line(dec)}:@{name}",
                )
    assert not offenders, (
        "Pydantic computed_field/field_validator/model_validator are banned "
        f"in IR models (FR-7, AC-13.1): {offenders!r}"
    )


def test_no_pep695_type_alias_statement() -> None:
    """FR-8 / AC-13.2: no PEP-695 ``type X = ...`` statements (``ast.TypeAlias``)."""
    tree = _load_models_ast()
    offenders: list[str] = []
    for node in ast.walk(tree):
        # ``ast.TypeAlias`` exists on Python 3.12+; on older interpreters the
        # attribute is absent and the check is vacuously satisfied.
        type_alias_cls = getattr(ast, "TypeAlias", None)
        if type_alias_cls is not None and isinstance(node, type_alias_cls):
            offenders.append(f"{IR_MODELS_FILE.name}:{_line(node)}")
    assert not offenders, (
        f"PEP-695 `type X = ...` aliases are banned in IR models (FR-8, AC-13.2): {offenders!r}"
    )


def test_no_float_in_field_annotations() -> None:
    """FR-9 / AC-13.3: no ``float`` in any annotated assignment or function arg."""
    tree = _load_models_ast()
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and _annotation_uses_float(node.annotation):
            offenders.append(f"{IR_MODELS_FILE.name}:{_line(node)}:AnnAssign")
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            arg_lists: list[list[ast.arg]] = [
                list(args.posonlyargs),
                list(args.args),
                list(args.kwonlyargs),
            ]
            for arg_list in arg_lists:
                for arg in arg_list:
                    if arg.annotation is not None and _annotation_uses_float(
                        arg.annotation,
                    ):
                        offenders.append(
                            f"{IR_MODELS_FILE.name}:{_line(arg)}:arg:{arg.arg}",
                        )
            if node.returns is not None and _annotation_uses_float(node.returns):
                offenders.append(f"{IR_MODELS_FILE.name}:{_line(node)}:return")
    assert not offenders, (
        "`float` in IR field annotations is banned -- use Decimal or int "
        f"nanoseconds (FR-9, AC-13.3): {offenders!r}"
    )
