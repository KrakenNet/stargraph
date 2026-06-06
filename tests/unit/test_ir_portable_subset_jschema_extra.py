# SPDX-License-Identifier: Apache-2.0
"""AST walker test enforcing FR-12 / AC-13.6: ``json_schema_extra`` is dict-only.

Walks ``src/stargraph/ir/_models.py`` with :func:`ast.parse` and asserts that
every ``json_schema_extra=<expr>`` keyword argument -- whether on a
``ConfigDict(...)`` / ``BaseModel.model_config = ...`` site, a ``Field(...)``
call, or anywhere else -- resolves to a **dict literal** (:class:`ast.Dict`),
never to a callable form (:class:`ast.Lambda` or :class:`ast.Name`).

Pydantic 2.9+ supports dict-merge semantics on ``json_schema_extra``;
callables are forbidden under Stargraph's portable-subset because they break
declarative schema export -- the schema generator can serialize a dict at
build time, but a callable defers logic to runtime, defeating offline
JSON-Schema export and round-trip parity (FR-12 / AC-13.6).

The walker is robust to no current uses: a vacuous pass on today's
``_models.py`` is correct, and any future addition of
``json_schema_extra=lambda ...`` or ``json_schema_extra=some_fn`` will trip
the assertion immediately.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
MODELS_FILE: Path = PROJECT_ROOT / "src" / "stargraph" / "ir" / "_models.py"


def _line(node: ast.AST) -> int:
    """Return ``node.lineno`` or ``0`` if missing (defensive)."""
    return getattr(node, "lineno", 0)


def _iter_json_schema_extra_kwargs(tree: ast.AST) -> list[ast.keyword]:
    """Yield every ``json_schema_extra=<value>`` keyword arg on any ``Call`` node."""
    found: list[ast.keyword] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "json_schema_extra":
                found.append(kw)
    return found


def test_json_schema_extra_is_dict_literal_only() -> None:
    """FR-12 / AC-13.6: every ``json_schema_extra=...`` must be a dict literal.

    Callable forms (``ast.Lambda``, ``ast.Name`` referencing a function) are
    rejected -- declarative schema export cannot inspect callables at
    build time.
    """
    assert MODELS_FILE.is_file(), f"missing IR models module: {MODELS_FILE}"
    tree = ast.parse(MODELS_FILE.read_text(encoding="utf-8"), filename=str(MODELS_FILE))

    violations: list[str] = []
    for kw in _iter_json_schema_extra_kwargs(tree):
        value = kw.value
        if isinstance(value, ast.Dict):
            continue
        kind = type(value).__name__
        violations.append(
            f"{MODELS_FILE.relative_to(PROJECT_ROOT)}:{_line(kw)}: "
            f"json_schema_extra value is {kind}, expected ast.Dict"
        )

    assert not violations, (
        "json_schema_extra must be a dict literal under Stargraph's portable-subset "
        f"(FR-12, AC-13.6); callables forbidden: {violations!r}"
    )
