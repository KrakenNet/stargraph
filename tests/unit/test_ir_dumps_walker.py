# SPDX-License-Identifier: Apache-2.0
"""AST walker test enforcing FR-15 / AC-11.5: single canonical dumps entry point.

Walks every ``.py`` file under ``src/stargraph/`` with :func:`ast.parse` and asserts:

1. **No ``model_dump_json`` calls outside ``stargraph.ir._dumps``.** This Pydantic
   method is only defined on Pydantic models, and the only Pydantic models in
   the Stargraph codebase are :class:`IRBase` subclasses, so any ``.model_dump_json()``
   call is by construction a leak of IR serialization out of the canonical entry
   point.

2. **No ``json.dumps(<model>.model_dump(...))`` chains outside ``stargraph.ir._dumps``.**
   This is the textbook anti-pattern FR-15 forbids: bypassing :func:`stargraph.ir.dumps`
   by manually re-implementing the canonical compose step. Detected as
   ``Call(func=Attribute(value=Name(id='json'), attr='dumps'))`` whose first
   positional argument is itself a ``Call`` to an ``.model_dump`` attribute.

   Bare ``json.dumps(d, ...)`` calls on raw ``dict``/``list`` values (e.g.
   :func:`stargraph.ir._ids.fact_content_hash`'s canonical-fact hashing,
   :func:`stargraph.fathom._provenance._sanitize_provenance_slot`'s slot encoding)
   are *not* leaks: they serialize untyped data, not IR Pydantic models.

The allow-list is exactly one file: ``src/stargraph/ir/_dumps.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
SRC_STARGRAPH: Path = PROJECT_ROOT / "src" / "stargraph"
DUMPS_FILE: Path = SRC_STARGRAPH / "ir" / "_dumps.py"


def _iter_stargraph_py_files() -> list[Path]:
    """Return every ``.py`` file under ``src/stargraph/`` (sorted, deterministic)."""
    return sorted(p for p in SRC_STARGRAPH.rglob("*.py") if p.is_file())


def _is_model_dump_json_call(node: ast.AST) -> bool:
    """``True`` iff ``node`` is ``<expr>.model_dump_json(...)`` -- a Pydantic IR leak."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr == "model_dump_json"


def _is_json_dumps_of_model_dump(node: ast.AST) -> bool:
    """``True`` iff ``node`` is ``json.dumps(<expr>.model_dump(...), ...)``.

    This is the chained anti-pattern FR-15 forbids: a caller hand-rolling the
    ``json.dumps(model.model_dump())`` compose that :func:`stargraph.ir.dumps` owns.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if not (isinstance(func.value, ast.Name) and func.value.id == "json"):
        return False
    if func.attr != "dumps":
        return False
    if not node.args:
        return False
    first = node.args[0]
    if not isinstance(first, ast.Call):
        return False
    inner = first.func
    return isinstance(inner, ast.Attribute) and inner.attr == "model_dump"


def _line(node: ast.AST) -> int:
    """Return ``node.lineno`` or ``0`` if missing (defensive; AST nodes carry it)."""
    return getattr(node, "lineno", 0)


def test_no_model_dump_json_outside_canonical_dumps() -> None:
    """FR-15 / AC-11.5: ``model_dump_json`` is forbidden outside ``stargraph.ir._dumps``."""
    leaks: list[str] = []
    for path in _iter_stargraph_py_files():
        if path.resolve() == DUMPS_FILE.resolve():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if _is_model_dump_json_call(node):
                leaks.append(f"{path.relative_to(PROJECT_ROOT)}:{_line(node)}")
    assert not leaks, (
        f"model_dump_json called outside stargraph.ir._dumps (FR-15, AC-11.5): {leaks!r}"
    )


def test_no_json_dumps_of_model_dump_outside_canonical_dumps() -> None:
    """FR-15 / AC-11.5: ``json.dumps(x.model_dump(...))`` chain is forbidden outside ``_dumps``."""
    leaks: list[str] = []
    for path in _iter_stargraph_py_files():
        if path.resolve() == DUMPS_FILE.resolve():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if _is_json_dumps_of_model_dump(node):
                leaks.append(f"{path.relative_to(PROJECT_ROOT)}:{_line(node)}")
    assert not leaks, (
        f"json.dumps(<x>.model_dump(...)) bypass of stargraph.ir.dumps (FR-15, AC-11.5): {leaks!r}"
    )


def test_canonical_dumps_file_is_present_and_uses_both_primitives() -> None:
    """Sanity: ``_dumps.py`` exists and is the place that *does* call both primitives.

    Guards against silently relocating the canonical implementation: if someone
    moves the body out of ``_dumps.py``, the allow-list above would still pass
    while the rest of the codebase is uncovered. Asserting both primitives
    appear inside ``_dumps.py`` ensures the canonical entry stays put.
    """
    assert DUMPS_FILE.is_file(), f"missing canonical dumps module: {DUMPS_FILE}"
    tree = ast.parse(DUMPS_FILE.read_text(encoding="utf-8"), filename=str(DUMPS_FILE))
    has_json_dumps = False
    has_model_dump = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if isinstance(func.value, ast.Name) and func.value.id == "json" and func.attr == "dumps":
            has_json_dumps = True
        if func.attr == "model_dump":
            has_model_dump = True
    assert has_json_dumps, "_dumps.py no longer calls json.dumps -- canonical entry moved?"
    assert has_model_dump, "_dumps.py no longer calls .model_dump -- canonical entry moved?"
