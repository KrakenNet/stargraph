# SPDX-License-Identifier: Apache-2.0
"""Storesmith integration tests.

The DSPy generator is stubbed for determinism, but the verify gate runs for real
in every test — these assert the "always works" contract end-to-end: a bogus
passing test cannot get a store that doesn't persist, doesn't replace, or skips
the migrate guard recorded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from tests.fixtures.smith_testkit import CTX, drive, stub_build

from stargraph.skills.storesmith import _ledger, gate
from stargraph.skills.storesmith.nodes.build import Build
from stargraph.skills.storesmith.nodes.recall import Recall
from stargraph.skills.storesmith.nodes.record import RecordBuild
from stargraph.skills.storesmith.nodes.triage import TriageGate
from stargraph.skills.storesmith.seeds import (
    _DOC_STORE_SOURCE,  # pyright: ignore[reportPrivateUsage]
)
from stargraph.skills.storesmith.state import State

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

# The good store + a real test driving it (distilled from SQLiteDocStore).
GOOD_STORE = _DOC_STORE_SOURCE
GOOD_STORE_TEST = """\
import asyncio
from pathlib import Path

from store import SqliteDocStore


def test_roundtrip(tmp_path: Path) -> None:
    store = SqliteDocStore(tmp_path / "docs.db")

    async def _run() -> None:
        await store.bootstrap()
        await store.put("doc-1", "hello", metadata={"k": "v", "n": 1})
        got = await store.get("doc-1")
        assert got is not None
        assert got.content == "hello"

    asyncio.run(_run())
"""
GOOD_FIXTURE: dict[str, Any] = {
    "doc_id": "doc-1",
    "content": "hello",
    "content2": "updated",
    "metadata": {"k": "v", "n": 1},
}
GOOD_GEN: dict[str, Any] = {
    "class_name": "SqliteDocStore",
    "fixture": GOOD_FIXTURE,
    "store_source": GOOD_STORE,
    "test_source": GOOD_STORE_TEST,
}

# Adversarial: a structurally-valid store whose put uses INSERT OR IGNORE, so the
# second put is silently dropped and get returns the stale content — it violates
# the INSERT OR REPLACE contract while its own trivial test passes. The contract
# tier must catch the missing replace.
NO_REPLACE_STORE = """\
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from stargraph.errors import MigrationNotSupported
from stargraph.stores._common import MigrationPlan, StoreHealth, _validate_migration_plan
from stargraph.stores.doc import Document


class CheatDocStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    async def bootstrap(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._path) as conn:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS documents ("
                "  doc_id TEXT PRIMARY KEY, content TEXT NOT NULL, metadata TEXT NOT NULL)"
            )
            await conn.commit()

    async def health(self) -> StoreHealth:
        return StoreHealth(ok=True, version=1, fragment_count=0,
                           fs_type="unknown", lock_state="free")

    async def migrate(self, plan: MigrationPlan) -> None:
        _validate_migration_plan(plan, store="cheat")
        raise MigrationNotSupported("noop", store="cheat", operation="add_column",
                                    reason="poc")

    async def put(self, doc_id: str, content: str | bytes, *,
                  metadata: dict[str, Any] | None = None) -> None:
        async with aiosqlite.connect(self._path) as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO documents (doc_id, content, metadata) VALUES (?, ?, ?)",
                (doc_id, str(content), json.dumps(metadata or {})),
            )
            await conn.commit()

    async def get(self, doc_id: str) -> Document | None:
        async with (
            aiosqlite.connect(self._path) as conn,
            conn.execute(
                "SELECT doc_id, content, metadata FROM documents WHERE doc_id = ?",
                (doc_id,),
            ) as cur,
        ):
            row = await cur.fetchone()
        if row is None:
            return None
        return Document(id=str(row[0]), content=str(row[1]),
                        metadata=json.loads(row[2]), created_at=datetime.now(UTC))

    async def query(self, filter: str | None = None, *, limit: int = 100) -> list[Document]:
        async with (
            aiosqlite.connect(self._path) as conn,
            conn.execute("SELECT doc_id, content, metadata FROM documents LIMIT ?",
                         (limit,)) as cur,
        ):
            rows = await cur.fetchall()
        return [Document(id=str(r[0]), content=str(r[1]), metadata=json.loads(r[2]),
                         created_at=datetime.now(UTC)) for r in rows]
"""

# A second adversarial shape: health() returns None instead of a StoreHealth.
HEALTH_NONE_STORE = """\
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from stargraph.errors import MigrationNotSupported
from stargraph.stores._common import MigrationPlan, StoreHealth, _validate_migration_plan
from stargraph.stores.doc import Document


class BadHealthStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    async def bootstrap(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._path) as conn:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS documents ("
                "  doc_id TEXT PRIMARY KEY, content TEXT NOT NULL, metadata TEXT NOT NULL)"
            )
            await conn.commit()

    async def health(self) -> StoreHealth | None:
        return None

    async def migrate(self, plan: MigrationPlan) -> None:
        _validate_migration_plan(plan, store="bad")
        raise MigrationNotSupported("noop", store="bad", operation="x", reason="poc")

    async def put(self, doc_id: str, content: str | bytes, *,
                  metadata: dict[str, Any] | None = None) -> None:
        async with aiosqlite.connect(self._path) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO documents (doc_id, content, metadata) VALUES (?, ?, ?)",
                (doc_id, str(content), json.dumps(metadata or {})),
            )
            await conn.commit()

    async def get(self, doc_id: str) -> Document | None:
        async with (
            aiosqlite.connect(self._path) as conn,
            conn.execute("SELECT doc_id, content, metadata FROM documents WHERE doc_id = ?",
                         (doc_id,)) as cur,
        ):
            row = await cur.fetchone()
        if row is None:
            return None
        return Document(id=str(row[0]), content=str(row[1]), metadata=json.loads(row[2]),
                        created_at=datetime.now(UTC))

    async def query(self, filter: str | None = None, *, limit: int = 100) -> list[Document]:
        async with (
            aiosqlite.connect(self._path) as conn,
            conn.execute("SELECT doc_id, content, metadata FROM documents LIMIT ?",
                         (limit,)) as cur,
        ):
            rows = await cur.fetchall()
        return [Document(id=str(r[0]), content=str(r[1]), metadata=json.loads(r[2]),
                         created_at=datetime.now(UTC)) for r in rows]
"""

TRIVIAL_TEST = "def test_x():\n    assert True\n"

# Syntax error → fails every tier fast; the repair loop can never fix a constant stub.
BAD_GEN: dict[str, Any] = {
    "class_name": "Broken",
    "fixture": {},
    "store_source": "class Oops(:\n    pass\n",
    "test_source": TRIVIAL_TEST,
}


# --------------------------------------------------------------------------- #
# Gate (the un-cheatable floor)
# --------------------------------------------------------------------------- #
async def test_gate_passes_a_real_store(tmp_path: Path) -> None:
    files = {gate.STORE_FILE: GOOD_STORE, gate.TEST_FILE: GOOD_STORE_TEST}
    results = gate.run_full_gate(tmp_path / "g", files, fixture=GOOD_FIXTURE)
    assert gate.all_passed(results)


async def test_gate_contract_catches_missing_insert_or_replace(tmp_path: Path) -> None:
    files = {gate.STORE_FILE: NO_REPLACE_STORE, gate.TEST_FILE: TRIVIAL_TEST}
    results = gate.run_full_gate(tmp_path / "g", files, fixture=GOOD_FIXTURE)
    contract = next(r for r in results if r.kind == "contract")
    assert not contract.passed  # a trivially-passing test did NOT save it
    assert "replace" in contract.findings[0]["msg"].lower()
    assert not gate.all_passed(results)


async def test_gate_contract_catches_bad_health(tmp_path: Path) -> None:
    files = {gate.STORE_FILE: HEALTH_NONE_STORE, gate.TEST_FILE: TRIVIAL_TEST}
    results = gate.run_full_gate(tmp_path / "g", files, fixture=GOOD_FIXTURE)
    assert not next(r for r in results if r.kind == "contract").passed


# --------------------------------------------------------------------------- #
# Build loop + record
# --------------------------------------------------------------------------- #
async def test_build_succeeds_first_try() -> None:
    out = await stub_build(Build, GOOD_GEN).execute(State(brief="sqlite doc store"), CTX)
    assert out["succeeded"] is True
    assert out["fix_attempts"] == 1
    assert out["class_name"] == "SqliteDocStore"


async def test_build_exhausts_retries_on_persistent_failure() -> None:
    out = await stub_build(Build, BAD_GEN).execute(State(brief="broken"), CTX)
    assert out["succeeded"] is False
    assert out["fix_attempts"] == 3  # bounded, never infinite


async def test_success_records_trainset_pair_and_lands(tmp_path: Path) -> None:
    out_dir = tmp_path / "generated"
    state = State(brief="sqlite doc store", model_id="stub-model", output_dir=str(out_dir))
    final = await drive([stub_build(Build, GOOD_GEN), RecordBuild()], state)

    pairs = _ledger.load_trainset()
    assert len(pairs) == 1
    assert pairs[0]["class_name"] == "SqliteDocStore"
    assert pairs[0]["passed"] is True
    assert pairs[0]["model_id"] == "stub-model"
    assert final.landed_path
    assert (out_dir / "sqlite_doc_store.py").exists()
    assert (out_dir / "test_sqlite_doc_store.py").exists()


async def test_failure_logs_lesson_and_records_no_pair() -> None:
    state = State(brief="broken thing")
    await drive([stub_build(Build, BAD_GEN), RecordBuild()], state)

    assert _ledger.load_trainset() == []  # no false-positive training data
    lessons = _ledger._read_jsonl(_ledger.home() / _ledger.LESSONS_FILE)  # pyright: ignore[reportPrivateUsage]
    assert any(le["failed_kind"] == "escalate" for le in lessons)


# --------------------------------------------------------------------------- #
# Reflexion recall + drift (idea 1 / idea 2 substrate)
# --------------------------------------------------------------------------- #
async def test_recall_surfaces_relevant_lesson() -> None:
    _ledger.append_lesson(
        brief="a sqlite doc store that round-trips metadata",
        failed_kind="contract",
        finding="get did not replace content on second put",
        attempts=1,
    )
    _ledger.append_lesson(
        brief="totally unrelated cron trigger thing",
        failed_kind="tests",
        finding="irrelevant",
        attempts=1,
    )
    out = await Recall().execute(State(brief="sqlite doc store metadata"), CTX)
    assert out["recalled_lessons"]
    assert "replace content" in out["recalled_lessons"][0]


def test_drift_rate_tracks_first_try_ratio() -> None:
    for attempts in (1, 1, 1, 3):  # 3 of 4 nailed on first try
        _ledger.append_trainset({"brief": "x", "attempts": attempts, "passed": True})
    assert _ledger.drift_rate(window=10) == pytest.approx(0.75)  # pyright: ignore[reportUnknownMemberType]


async def test_triage_rejects_empty_brief() -> None:
    with pytest.raises(ValueError, match="brief is required"):
        await TriageGate().execute(State(brief="  "), CTX)
