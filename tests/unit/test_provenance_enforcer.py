# SPDX-License-Identifier: Apache-2.0
"""Mandatory-provenance enforcer (FR-3, NFR-6, design §4.5).

Two guards land here:

1. **AST walker** — scans every ``.py`` module under ``src/stargraph`` and asserts
   that no ``<expr>.assert_fact(...)`` call exists outside ``src/stargraph/fathom/``.
   Engine code paths must route through the Fathom adapter
   (``ProvenanceFathomAdapter.assert_fact``) so the 6-slot
   :class:`ProvenanceBundle` is attached to every assertion. Direct
   ``engine.assert_fact(...)`` calls in non-fathom modules would bypass the
   provenance contract — FR-3 makes that fatal.

2. **Schema guard** — pins the :class:`ProvenanceBundle` slot set to exactly
   the six fields named in design §4.5 (``origin``, ``source``, ``run_id``,
   ``step``, ``confidence``, ``timestamp``). Adding/removing/renaming a slot
   without coordinating the wire format and the encoder is a regression; this
   test catches that statically against the TypedDict's ``__annotations__``.

Status at Phase 3.1: no violations found at time of writing; the AST walker
acts as a regression guard going forward (will fail if any future engine code
calls ``assert_fact`` directly outside ``src/stargraph/fathom/``).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from stargraph.fathom import ProvenanceBundle

# Project root resolved relative to this test file: tests/unit/ -> tests/ -> repo root.
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_SRC_STARGRAPH: Path = _REPO_ROOT / "src" / "stargraph"
_FATHOM_DIR: Path = _SRC_STARGRAPH / "fathom"

# Six-slot ProvenanceBundle contract per design §4.5.
_REQUIRED_PROVENANCE_SLOTS: frozenset[str] = frozenset(
    {"origin", "source", "run_id", "step", "confidence", "timestamp"}
)


def _iter_stargraph_python_files_outside_fathom() -> list[Path]:
    """Return every ``.py`` module under ``src/stargraph`` excluding ``src/stargraph/fathom/``."""
    return sorted(
        p
        for p in _SRC_STARGRAPH.rglob("*.py")
        if "__pycache__" not in p.parts and _FATHOM_DIR not in p.parents and p != _FATHOM_DIR
    )


def _collect_assert_fact_calls(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, attribute-source)`` for every ``<expr>.assert_fact(...)`` call.

    Only attribute-access calls (``foo.assert_fact(...)``) are flagged; a bare
    ``assert_fact(...)`` reference would already be a ``NameError`` at runtime
    in modules that do not import it, so we focus on the realistic bypass
    pattern: an engine module holding an engine handle and calling
    ``self.engine.assert_fact(...)`` (or similar) without a provenance bundle.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr != "assert_fact":
            continue
        # Render the receiver expression for the failure message.
        try:
            receiver = ast.unparse(func.value)
        except AttributeError:  # pragma: no cover - py<3.9 fallback, unused on 3.12+
            receiver = "<expr>"
        hits.append((node.lineno, f"{receiver}.assert_fact(...)"))
    return hits


@pytest.mark.unit
def test_no_direct_assert_fact_outside_fathom() -> None:
    """No engine code calls ``.assert_fact(...)`` outside ``src/stargraph/fathom/`` (FR-3)."""
    files = _iter_stargraph_python_files_outside_fathom()
    assert files, f"no python files found under {_SRC_STARGRAPH!s} outside fathom/"

    violations: list[tuple[Path, int, str]] = []
    for path in files:
        for lineno, rendered in _collect_assert_fact_calls(path):
            violations.append((path, lineno, rendered))

    if violations:
        rendered = "\n".join(
            f"  {p.relative_to(_REPO_ROOT)}:{lineno}: {what}" for p, lineno, what in violations
        )
        pytest.fail(
            "Direct .assert_fact(...) calls found outside src/stargraph/fathom/ "
            "(FR-3, NFR-6, design §4.5):\n"
            + rendered
            + "\n\nRoute the assertion through ProvenanceFathomAdapter.assert_fact "
            "with a 6-slot ProvenanceBundle so the wire format remains intact."
        )


@pytest.mark.unit
def test_provenance_bundle_has_six_required_slots() -> None:
    """``ProvenanceBundle`` exposes exactly the 6 design-§4.5 slots (FR-3)."""
    annotations = set(ProvenanceBundle.__annotations__)
    missing = _REQUIRED_PROVENANCE_SLOTS - annotations
    extra = annotations - _REQUIRED_PROVENANCE_SLOTS
    assert not missing, (
        "ProvenanceBundle is missing required provenance slots "
        f"{sorted(missing)} (design §4.5 mandates {sorted(_REQUIRED_PROVENANCE_SLOTS)})"
    )
    assert not extra, (
        "ProvenanceBundle has unexpected slots "
        f"{sorted(extra)}; the 6-slot contract is fixed by design §4.5. "
        "Adding a slot requires a coordinated wire-format + encoder update."
    )
