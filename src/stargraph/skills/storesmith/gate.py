# SPDX-License-Identifier: Apache-2.0
"""The store smith verify gate — the "always works" contract for stores.

The three-tier shape + subprocess isolation live in :mod:`stargraph.skills._smith.gate`;
this module supplies the *store* contract: import the generated module, find the
single class implementing the ``DocStore`` protocol, construct it on a tmpfile
sqlite DB, and exercise the full ``bootstrap → health → put → get → query →
replace → migrate-guard`` round trip inside one ``asyncio.run``. Because every
assert is on observable behavior (real persistence, replace, absence, the
migrate guard), a trivially-passing generated test cannot land a broken store.
The fixed artifact filenames are ``store.py`` + ``test_store.py``.

TRUST BOUNDARY: see :mod:`stargraph.skills._smith.gate` - tiers 2-3 execute
LLM-generated code in a subprocess (process isolation, not a sandbox).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from stargraph.skills._smith.gate import (
    VerifierResult,
    all_passed,
    make_contract_tier,
    run_tiered_gate,
)

__all__ = [
    "STORE_FILE",
    "TEST_FILE",
    "VerifierResult",
    "all_passed",
    "run_full_gate",
    "verify_sources",
]

STORE_FILE = "store.py"
TEST_FILE = "test_store.py"

# Driver executed in a subprocess: imports the candidate store, finds the single
# DocStore class, constructs it on a throwaway sqlite DB, drives the full async
# round trip, and prints a one-line JSON verdict. Dependency-free beyond
# stargraph + aiosqlite (both present wherever the gate runs).
_CONTRACT_DRIVER = """\
import asyncio, importlib.util, json, sys, tempfile
from pathlib import Path

from stargraph.errors import MigrationNotSupported
from stargraph.stores._common import MigrationPlan, StoreHealth


def _fail(msg):
    print(json.dumps({"ok": False, "msg": msg}))
    sys.exit(0)


contract = json.loads(Path("contract.json").read_text())
fixture = contract.get("fixture", {})
doc_id = str(fixture.get("doc_id", "doc-1"))
content = str(fixture.get("content", "hello"))
content2 = str(fixture.get("content2", "updated"))
metadata = fixture.get("metadata", {}) or {}

spec_mod = importlib.util.spec_from_file_location("candidate_store", "store.py")
mod = importlib.util.module_from_spec(spec_mod)
try:
    spec_mod.loader.exec_module(mod)
except Exception as e:
    _fail(f"import failed: {type(e).__name__}: {e}")

_METHODS = ("bootstrap", "health", "migrate", "put", "get", "query")
stores = [
    v for v in vars(mod).values()
    if isinstance(v, type)
    and getattr(v, "__module__", None) == mod.__name__
    and all(callable(getattr(v, m, None)) for m in _METHODS)
]
if not stores:
    _fail("no DocStore class defined in store.py")
if len(stores) > 1:
    _fail(f"expected one store, found {[c.__name__ for c in stores]}")
cls = stores[0]

db_path = Path(tempfile.mkdtemp()) / "s.db"
try:
    store = cls(db_path)
except TypeError as e:
    _fail(f"constructor must accept a single path: {type(e).__name__}: {e}")


async def _exercise():
    await store.bootstrap()

    h = await store.health()
    if not isinstance(h, StoreHealth):
        return "health() must return a StoreHealth"
    if h.ok is not True:
        return "health().ok must be True after bootstrap"
    if not isinstance(h.version, int):
        return "health().version must be an int"

    await store.put(doc_id, content, metadata=metadata)
    d = await store.get(doc_id)
    if d is None:
        return "get() returned None for a doc that was just put"
    if d.content != content:
        return f"get().content {d.content!r} != put content {content!r}"
    if d.metadata != metadata:
        return f"get().metadata {d.metadata!r} != put metadata {metadata!r}"

    if await store.get("definitely-absent") is not None:
        return "get() of an absent doc_id must return None"

    rows = await store.query()
    if not any(getattr(r, "id", None) == doc_id for r in rows):
        return "query() did not surface the document that was put"

    await store.put(doc_id, content2)
    d2 = await store.get(doc_id)
    if d2 is None or d2.content != content2:
        got = None if d2 is None else d2.content
        return f"second put did not replace content (INSERT OR REPLACE): got {got!r}"

    plan = MigrationPlan(target_version=2, operations=[{"op": "rename_column"}])
    try:
        await store.migrate(plan)
    except MigrationNotSupported:
        pass
    else:
        return "migrate() must raise MigrationNotSupported on a non-add_column plan"
    return None


try:
    err = asyncio.run(_exercise())
except Exception as e:
    _fail(f"store raised while exercising the contract: {type(e).__name__}: {e}")
if err:
    _fail(err)

print(json.dumps({"ok": True, "class": cls.__name__}))
"""


def run_full_gate(
    work_dir: Path,
    files: dict[str, str],
    *,
    fixture: dict[str, Any],
) -> list[VerifierResult]:
    """static → contract → tests in ``work_dir``, short-circuiting on first failure.

    Shared verbatim by the build node and the offline optimizer's metric. The
    contract tier imports the store and exercises the full async round trip on a
    throwaway sqlite DB (see ``_CONTRACT_DRIVER``).
    """
    return run_tiered_gate(
        work_dir,
        files,
        contract_tier=make_contract_tier(_CONTRACT_DRIVER, {"fixture": fixture}),
        test_file=TEST_FILE,
    )


def verify_sources(
    store_source: str,
    test_source: str,
    *,
    fixture: dict[str, Any],
) -> tuple[bool, list[VerifierResult]]:
    """Run the full gate on raw source in a throwaway temp dir.

    The convenience entry point for callers that hold source strings rather than a
    work dir — ``storesmith make``, the doctor preflight, and seed verification.
    Returns ``(passed, results)``.
    """
    files = {STORE_FILE: store_source, TEST_FILE: test_source}
    with tempfile.TemporaryDirectory(prefix="storesmith-verify-") as d:
        results = run_full_gate(Path(d), files, fixture=fixture)
    return all_passed(results), results
