# SPDX-License-Identifier: Apache-2.0
"""AST walker -- no fact-asserting code path bypasses the provenance contract (NFR-5).

Extends the engine NFR-6 walker
(:mod:`tests.unit.test_provenance_enforcer`) which bans
``<expr>.assert_fact(...)`` outside ``src/stargraph/fathom/``. NFR-5 widens the
contract to the knowledge layer:

* **Walks** ``src/stargraph/stores`` and ``src/stargraph/skills`` ASTs.
* **Allowed** writers into the ``facts`` table:

  - ``FathomAdapter.assert_with_provenance(...)`` (the canonical
    provenance-bearing assertion seam, design §4.5).
  - ``FactStore.apply_delta(...)`` (typed-delta path, design §4.2 / FR-29 --
    centralised in :mod:`stargraph.stores._delta`).
  - ``FactStore.pin(...)`` / ``FactStore.unpin(...)`` reached *via* the
    Protocol (callers hold a typed ``FactStore`` reference).
  - The :class:`stargraph.stores.sqlite_fact.SQLiteFactStore` implementation
    itself -- it is the FactStore Protocol's storage seam.

* **Banned** patterns (would silently bypass lineage / provenance):

  - ``<expr>.assert_fact(...)`` outside ``src/stargraph/fathom/`` (NFR-6
    inherited; reasserted here for the stores+skills surface so future
    refactors that move imports across packages don't quietly drop the
    guard).
  - Direct SQL ``INSERT`` / ``UPDATE`` / ``DELETE`` against the ``facts``
    table from any module other than ``src/stargraph/stores/sqlite_fact.py``
    (the FactStore impl). A skill or sibling store opening its own
    ``aiosqlite.connect(...)`` and writing to ``facts`` would skip the
    :func:`stargraph.stores._delta._validate_delta_provenance` gate.

The walker is purely static -- it parses Python source via :mod:`ast` and
matches by call shape / string content, so it adds no import-time cost and
runs at unit-test speed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

# Project root resolved relative to this test file: tests/unit/ -> tests/ -> repo root.
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_SRC_STARGRAPH: Path = _REPO_ROOT / "src" / "stargraph"
_STORES_DIR: Path = _SRC_STARGRAPH / "stores"
_SKILLS_DIR: Path = _SRC_STARGRAPH / "skills"
_FATHOM_DIR: Path = _SRC_STARGRAPH / "fathom"

# The FactStore Protocol implementation -- the one place direct SQL writes
# to the ``facts`` table are legitimate.
_SQLITE_FACT_STORE_IMPL: Path = _STORES_DIR / "sqlite_fact.py"

# Match any SQL statement that mutates the ``facts`` table. Case-insensitive,
# tolerates ``OR REPLACE`` / ``OR IGNORE`` in INSERT and the optional
# ``FROM`` after DELETE. Whitespace between keyword and table name is
# normalised to a single space before matching.
_FACTS_WRITE_SQL_RE = re.compile(
    r"\b(?:INSERT(?:\s+OR\s+(?:REPLACE|IGNORE))?\s+INTO|UPDATE|DELETE\s+FROM)\s+facts\b",
    re.IGNORECASE,
)


def _iter_python_files(root: Path) -> list[Path]:
    """Return every ``.py`` module under ``root`` (sorted, excluding caches)."""
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _collect_assert_fact_calls(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, attribute-source)`` for every ``<expr>.assert_fact(...)`` call.

    Mirrors :func:`tests.unit.test_provenance_enforcer._collect_assert_fact_calls`
    so the stores+skills surface gets the same NFR-6 guard locally even if
    the engine-wide walker is ever scoped down.
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
        try:
            receiver = ast.unparse(func.value)
        except AttributeError:  # pragma: no cover - py<3.9 fallback, unused on 3.12+
            receiver = "<expr>"
        hits.append((node.lineno, f"{receiver}.assert_fact(...)"))
    return hits


def _collect_facts_table_writes(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, snippet)`` for every direct SQL write to the ``facts`` table.

    Walks every :class:`ast.Constant` whose value is a ``str`` and checks the
    string content against :data:`_FACTS_WRITE_SQL_RE`. Implicitly-concatenated
    string fragments (``"INSERT INTO facts" " VALUES (?, ?)"``) are joined by
    the AST as separate ``Constant`` nodes per fragment -- :mod:`ast` exposes
    the joined ``BinOp`` only for ``+`` concatenation. To handle the common
    ``aiosqlite``/``sqlite3`` parenthesised-tuple form (Python implicit
    concatenation), we additionally walk :class:`ast.JoinedStr` and stitch
    sibling ``Constant`` strings inside ``Call.args`` tuples before testing.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, str]] = []

    def _record(lineno: int, payload: str) -> None:
        match = _FACTS_WRITE_SQL_RE.search(payload)
        if match is None:
            return
        # Use the matched fragment (truncated) for the failure rendering.
        snippet = match.group(0)
        hits.append((lineno, snippet))

    for node in ast.walk(tree):
        # Plain string literal: ``"INSERT INTO facts ..."``
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            _record(node.lineno, node.value)
            continue

        # ``"INSERT INTO " + "facts ..."`` chains -- collapse left+right.
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = node.left.value if isinstance(node.left, ast.Constant) else None
            right = node.right.value if isinstance(node.right, ast.Constant) else None
            if isinstance(left, str) and isinstance(right, str):
                _record(node.lineno, left + right)
            continue

        # f-strings: collapse the constant fragments and skip placeholders.
        if isinstance(node, ast.JoinedStr):
            collapsed = "".join(
                v.value
                for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
            if collapsed:
                _record(node.lineno, collapsed)
    return hits


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.knowledge
@pytest.mark.unit
def test_no_direct_assert_fact_in_stores_or_skills() -> None:
    """Ban ``.assert_fact(...)`` under ``stores`` and ``skills`` (NFR-5, extends NFR-6).

    The engine-wide walker in :mod:`tests.unit.test_provenance_enforcer`
    already enforces this for everything outside ``src/stargraph/fathom/``;
    we re-pin the stores and skills surfaces explicitly so a future scope
    narrowing of that walker still leaves the knowledge layer covered.
    """
    files = _iter_python_files(_STORES_DIR) + _iter_python_files(_SKILLS_DIR)
    assert files, (
        f"no python files found under {_STORES_DIR!s} or {_SKILLS_DIR!s} "
        "-- AST walker would otherwise pass vacuously"
    )

    violations: list[tuple[Path, int, str]] = []
    for path in files:
        # Defence in depth: even if a future move lands a fathom helper
        # inside ``stores`` or ``skills``, that file must route through
        # FathomAdapter, not call ``engine.assert_fact`` directly.
        if _FATHOM_DIR in path.parents:
            continue
        for lineno, rendered in _collect_assert_fact_calls(path):
            violations.append((path, lineno, rendered))

    if violations:
        rendered = "\n".join(
            f"  {p.relative_to(_REPO_ROOT)}:{lineno}: {what}" for p, lineno, what in violations
        )
        pytest.fail(
            "Direct .assert_fact(...) calls found in src/stargraph/stores or "
            "src/stargraph/skills (NFR-5, extends NFR-6):\n"
            + rendered
            + "\n\nRoute the assertion through FathomAdapter.assert_with_provenance "
            "with a 6-slot ProvenanceBundle so the wire format remains intact."
        )


@pytest.mark.knowledge
@pytest.mark.unit
def test_no_direct_facts_table_writes_outside_fact_store_impl() -> None:
    """Only ``src/stargraph/stores/sqlite_fact.py`` may issue raw SQL writes to ``facts`` (NFR-5).

    Direct ``INSERT INTO facts`` / ``UPDATE facts`` / ``DELETE FROM facts``
    from any sibling store, skill, or helper would skip the typed-delta
    provenance gate (:func:`stargraph.stores._delta._validate_delta_provenance`)
    and silently corrupt the lineage column. Writers must reach the table
    via :meth:`FactStore.pin` / :meth:`FactStore.unpin` /
    :meth:`FactStore.apply_delta`, or via
    :meth:`FathomAdapter.assert_with_provenance` for fact assertions.
    """
    files = _iter_python_files(_STORES_DIR) + _iter_python_files(_SKILLS_DIR)
    assert files, (
        f"no python files found under {_STORES_DIR!s} or {_SKILLS_DIR!s} "
        "-- AST walker would otherwise pass vacuously"
    )

    violations: list[tuple[Path, int, str]] = []
    for path in files:
        if path == _SQLITE_FACT_STORE_IMPL:
            continue  # FactStore impl is the legitimate writer.
        for lineno, snippet in _collect_facts_table_writes(path):
            violations.append((path, lineno, snippet))

    if violations:
        rendered = "\n".join(
            f"  {p.relative_to(_REPO_ROOT)}:{lineno}: {snippet!r}"
            for p, lineno, snippet in violations
        )
        pytest.fail(
            "Direct SQL writes to the `facts` table found outside "
            "src/stargraph/stores/sqlite_fact.py (NFR-5):\n"
            + rendered
            + "\n\nRoute writes through FactStore.apply_delta (typed-delta "
            "provenance gate) or FactStore.pin/unpin via the Protocol; "
            "fact assertions must go through "
            "FathomAdapter.assert_with_provenance."
        )


@pytest.mark.knowledge
@pytest.mark.unit
def test_walker_detects_synthetic_assert_fact_violation(tmp_path: Path) -> None:
    """Sanity check: walker flags a synthetic ``.assert_fact(...)`` call.

    Pinned so the walker isn't accidentally a no-op (e.g. the AST visitor
    being short-circuited by an early ``continue``). Writes a fixture file
    under ``tmp_path`` and runs the same collector against it.
    """
    fixture = tmp_path / "fake_skill.py"
    fixture.write_text(
        "def go(engine):\n    engine.assert_fact('foo', {})\n",
        encoding="utf-8",
    )
    hits = _collect_assert_fact_calls(fixture)
    assert hits, "walker failed to flag a synthetic .assert_fact(...) call"
    assert hits[0][1].endswith(".assert_fact(...)")


@pytest.mark.knowledge
@pytest.mark.unit
def test_walker_detects_synthetic_facts_table_write(tmp_path: Path) -> None:
    """Sanity check: walker flags a synthetic ``INSERT INTO facts`` literal.

    Pinned so the SQL-string detector isn't accidentally a no-op when the
    regex / AST visitor diverges from the constants emitted by Python's
    parser.
    """
    fixture = tmp_path / "fake_store.py"
    fixture.write_text(
        'def go(conn):\n    conn.execute("INSERT INTO facts (id) VALUES (?)", (1,))\n',
        encoding="utf-8",
    )
    hits = _collect_facts_table_writes(fixture)
    assert hits, "walker failed to flag a synthetic INSERT INTO facts literal"
    assert "INSERT" in hits[0][1].upper()
