# SPDX-License-Identifier: Apache-2.0
"""``std.sql_query`` -- read-only sqlite/duckdb queries inside the jail."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from stargraph.errors import StargraphRuntimeError
from stargraph.tools.std.sql_query import sql_query

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


@pytest.fixture
def jail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("STARGRAPH_TOOLS_FS_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def sqlite_db(jail: Path) -> str:
    db = jail / "data.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
    conn.executemany("INSERT INTO t VALUES (?, ?)", [(i, f"row{i}") for i in range(5)])
    conn.commit()
    conn.close()
    return "data.sqlite"


def test_select_rows_and_columns(sqlite_db: str) -> None:
    out = sql_query(database=sqlite_db, query="SELECT id, name FROM t ORDER BY id")
    assert out["columns"] == ["id", "name"]
    assert out["row_count"] == 5
    assert out["rows"][0] == {"id": 0, "name": "row0"}
    assert out["truncated"] is False


def test_positional_params(sqlite_db: str) -> None:
    out = sql_query(database=sqlite_db, query="SELECT name FROM t WHERE id = ?", params=[3])
    assert out["rows"] == [{"name": "row3"}]


def test_max_rows_truncation(sqlite_db: str) -> None:
    out = sql_query(database=sqlite_db, query="SELECT id FROM t ORDER BY id", max_rows=2)
    assert out["row_count"] == 2
    assert out["truncated"] is True


def test_writes_blocked_by_readonly_open(sqlite_db: str) -> None:
    with pytest.raises(StargraphRuntimeError, match="sqlite query failed"):
        sql_query(database=sqlite_db, query="INSERT INTO t VALUES (99, 'nope')")


def test_missing_database_is_loud(jail: Path) -> None:
    with pytest.raises(StargraphRuntimeError, match="does not exist"):
        sql_query(database="ghost.sqlite", query="SELECT 1")


def test_database_outside_jail_rejected(jail: Path) -> None:
    with pytest.raises(StargraphRuntimeError, match="escapes"):
        sql_query(database="../elsewhere.sqlite", query="SELECT 1")


def test_duckdb_engine(jail: Path) -> None:
    duckdb = pytest.importorskip("duckdb")
    db = jail / "data.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1, 'a'), (2, 'b')) v(id, name)")
    conn.close()
    out = sql_query(database="data.duckdb", query="SELECT * FROM t ORDER BY id", engine="duckdb")
    assert out["columns"] == ["id", "name"]
    assert out["rows"] == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]


def test_duckdb_readonly_blocks_writes(jail: Path) -> None:
    duckdb = pytest.importorskip("duckdb")
    db = jail / "ro.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.close()
    with pytest.raises(StargraphRuntimeError, match="duckdb query failed"):
        sql_query(database="ro.duckdb", query="INSERT INTO t VALUES (1)", engine="duckdb")
