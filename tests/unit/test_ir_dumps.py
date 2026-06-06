# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :mod:`stargraph.ir._dumps` (FR-15, AC-11.1, AC-11.2, AC-11.3, AC-11.4).

Covers:

* Byte-identical round-trip through canonical fixtures (AC-11.3): for every
  fixture under ``tests/fixtures/ir/canonical/``, ``dumps(loads(t)) == t``.
* :data:`dumps_canonical` is the pinned partial that sets ``hashable=True``
  (AC-11.4) and emits ``sort_keys=True`` JSON with stable ordering.
* ``exclude_defaults=True`` keeps the wire compact (AC-11.1, AC-11.2).
* ``ensure_ascii=False`` and ``separators=(',', ':')`` are honored (compact
  form, no whitespace, non-ASCII not escaped).
* :func:`loads` accepts a custom subclass via the second positional argument
  (overload coverage for non-default ``model``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stargraph.ir import (
    IRDocument,
    NodeSpec,
    dumps,
    dumps_canonical,
    loads,
)
from stargraph.ir._dumps import dumps as raw_dumps

FIXTURES_DIR: Path = Path(__file__).resolve().parents[1] / "fixtures" / "ir" / "canonical"


def _fixtures() -> list[Path]:
    """Return every ``*.json`` under ``tests/fixtures/ir/canonical/`` (sorted)."""
    return sorted(FIXTURES_DIR.glob("*.json"))


# ---------------------------------------------------------------------------
# Round-trip byte-equality (AC-11.3)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_canonical_fixtures_directory_is_populated() -> None:
    """Sanity: the canonical fixtures directory exists with at least the two files."""
    files = _fixtures()
    names = {p.name for p in files}
    assert {"minimal.json", "full.json"}.issubset(names), names


@pytest.mark.unit
@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda p: p.name)
def test_dumps_loads_byte_identical_round_trip(fixture: Path) -> None:
    """AC-11.3: ``dumps(loads(text)) == text`` for every canonical fixture."""
    text = fixture.read_text(encoding="utf-8").rstrip("\n")
    doc = loads(text)
    assert isinstance(doc, IRDocument)
    assert dumps(doc) == text


# ---------------------------------------------------------------------------
# dumps_canonical: hashable=True, sort_keys behavior (AC-11.4)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dumps_canonical_is_dumps_with_hashable_true() -> None:
    """:data:`dumps_canonical` is :func:`dumps` partial-applied with ``hashable=True``."""
    doc = IRDocument(ir_version="1.0.0", id="run:t", nodes=[NodeSpec(id="n1", kind="task")])
    assert dumps_canonical(doc) == dumps(doc, hashable=True)


@pytest.mark.unit
def test_dumps_canonical_sorts_top_level_keys() -> None:
    """``hashable=True`` emits keys in alphabetical order (deterministic for hashing)."""
    doc = IRDocument(ir_version="1.0.0", id="run:t", nodes=[])
    canon = dumps_canonical(doc)
    # Pydantic's declared field order would emit ir_version first; sort_keys=True
    # rearranges to alphabetical: id, ir_version, nodes.
    assert canon == '{"id":"run:t","ir_version":"1.0.0","nodes":[]}'


@pytest.mark.unit
def test_default_dumps_preserves_declared_field_order() -> None:
    """``hashable=False`` (default) keeps Pydantic v2 declared field order."""
    doc = IRDocument(ir_version="1.0.0", id="run:t", nodes=[])
    text = dumps(doc)
    # ir_version is declared first on IRDocument, so it leads the wire form.
    assert text.startswith('{"ir_version":"1.0.0"')


# ---------------------------------------------------------------------------
# exclude_defaults=True (AC-11.1, AC-11.2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dumps_excludes_default_values() -> None:
    """``exclude_defaults=True``: empty optional sections are absent from the wire."""
    doc = IRDocument(ir_version="1.0.0", id="run:t", nodes=[])
    text = dumps(doc)
    decoded = json.loads(text)
    assert decoded == {"ir_version": "1.0.0", "id": "run:t", "nodes": []}
    # Default sections (rules=[], tools=[], state_schema={}, ...) all elided.
    assert "rules" not in decoded
    assert "tools" not in decoded
    assert "skills" not in decoded
    assert "state_schema" not in decoded


@pytest.mark.unit
def test_dumps_keeps_non_default_optional_section() -> None:
    """A non-default optional section appears on the wire (proves exclusion is value-driven)."""
    doc = IRDocument(
        ir_version="1.0.0",
        id="run:t",
        nodes=[NodeSpec(id="n1", kind="task")],
    )
    decoded = json.loads(dumps(doc))
    assert decoded["nodes"] == [{"id": "n1", "kind": "task"}]


# ---------------------------------------------------------------------------
# Compact JSON formatting
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dumps_uses_compact_separators() -> None:
    """``separators=(',', ':')``: no spaces between keys/values or list elements."""
    doc = IRDocument(
        ir_version="1.0.0",
        id="run:t",
        nodes=[NodeSpec(id="n1", kind="task")],
    )
    text = dumps(doc)
    assert ", " not in text
    assert ": " not in text


@pytest.mark.unit
def test_dumps_does_not_escape_non_ascii() -> None:
    """``ensure_ascii=False``: non-ASCII characters survive verbatim."""
    doc = IRDocument(
        ir_version="1.0.0",
        id="run:café",  # non-ASCII id
        nodes=[],
    )
    text = dumps(doc)
    assert "café" in text
    assert "\\u" not in text


# ---------------------------------------------------------------------------
# loads() accepts non-default model class (overload coverage)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_loads_accepts_custom_irbase_subclass() -> None:
    """:func:`loads` supports a non-default ``model`` argument (overload path)."""
    text = '{"id":"n1","kind":"task"}'
    node = loads(text, NodeSpec)
    assert isinstance(node, NodeSpec)
    assert node.id == "n1"


@pytest.mark.unit
def test_raw_dumps_is_re_exported_under_same_object() -> None:
    """``stargraph.ir.dumps`` and ``stargraph.ir._dumps.dumps`` are the same callable."""
    assert dumps is raw_dumps
