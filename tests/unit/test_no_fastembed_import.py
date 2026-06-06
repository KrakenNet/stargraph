# SPDX-License-Identifier: Apache-2.0
"""AST walker — bans every ``fastembed`` import under ``src/stargraph/stores`` (FR-15, AC-11.5).

Walks every ``.py`` module under ``src/stargraph/stores/`` and inspects each
``ast.Import`` / ``ast.ImportFrom`` node. Both ``import fastembed`` and
``from fastembed import ...`` (and any submodule form, e.g. ``from
fastembed.embedding import ...``) are forbidden.

Background (research.md, fastembed #615): the upstream ``fastembed`` package
ships a hard ONNX-runtime dependency that has caused install/runtime regressions
on slim CI images. Stargraph uses ``sentence-transformers`` (MiniLM-L6-v2) directly
through :mod:`stargraph.stores.embeddings`, so ``fastembed`` should never appear
in the import graph. This walker pins that contract at unit-test speed without
booting LanceDB or the embedder cache.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Project root resolved relative to this test file: tests/unit/ -> tests/ -> repo root.
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_STORES_ROOT: Path = _REPO_ROOT / "src" / "stargraph" / "stores"

_FORBIDDEN_TOP_LEVEL: str = "fastembed"


def _iter_stores_python_files() -> list[Path]:
    """Return every ``.py`` module under ``src/stargraph/stores`` (sorted, excluding caches)."""
    return sorted(p for p in _STORES_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _is_fastembed_module(name: str | None) -> bool:
    """Return True if ``name`` names ``fastembed`` or any submodule of it."""
    if name is None:
        return False
    return name == _FORBIDDEN_TOP_LEVEL or name.startswith(_FORBIDDEN_TOP_LEVEL + ".")


def _collect_fastembed_imports(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, rendered-import)`` for every fastembed import in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_fastembed_module(alias.name):
                    offenders.append((node.lineno, f"import {alias.name}"))
        elif (
            # ``from fastembed import X`` or ``from fastembed.sub import X``.
            # ``node.level > 0`` means a relative import (``from . import x``)
            # which cannot reach the top-level ``fastembed`` package.
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and _is_fastembed_module(node.module)
        ):
            imported = ", ".join(alias.name for alias in node.names) or "*"
            offenders.append((node.lineno, f"from {node.module} import {imported}"))
    return offenders


@pytest.mark.knowledge
@pytest.mark.unit
def test_no_fastembed_imports_in_stores() -> None:
    """No ``fastembed`` imports anywhere under ``src/stargraph/stores`` (FR-15, AC-11.5)."""
    files = _iter_stores_python_files()
    assert files, f"no python files found under {_STORES_ROOT!s}"

    all_offenders: list[tuple[Path, int, str]] = []
    for path in files:
        for lineno, rendered in _collect_fastembed_imports(path):
            all_offenders.append((path, lineno, rendered))

    if all_offenders:
        rendered = "\n".join(
            f"  {p.relative_to(_REPO_ROOT)}:{lineno}: {what}" for p, lineno, what in all_offenders
        )
        pytest.fail(
            "Forbidden fastembed imports found in src/stargraph/stores "
            "(FR-15, AC-11.5; see research.md fastembed #615):\n"
            + rendered
            + "\nStargraph uses sentence-transformers MiniLM-L6-v2 directly via "
            "stargraph.stores.embeddings; fastembed must not enter the import graph."
        )
