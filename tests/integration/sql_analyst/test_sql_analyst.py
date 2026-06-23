# SPDX-License-Identifier: Apache-2.0
"""SQL Analyst skill — the bounded repair loop, with both seams stubbed.

Journey: given a question + schema, the node generates SQL, runs it, and
repairs from the last error within a bounded number of attempts. Both the model
call and the database call are injected, so no live LM or DB is involved.
"""

from __future__ import annotations

from typing import Any

import pytest

from stargraph.skills.sql_analyst import SQL_ANALYST, SqlAnalystState
from stargraph.skills.sql_analyst.nodes.analyze import Analyze

pytestmark = pytest.mark.integration


class _Ctx:
    run_id = "sql-analyst-test"


def _rows(n: int) -> list[dict[str, Any]]:
    return [{"id": i} for i in range(n)]


async def test_happy_path_runs_once_and_succeeds() -> None:
    node = Analyze(
        generator=lambda _q, _s, _e: "SELECT * FROM orders",
        runner=lambda _sql: _rows(2),
    )
    state = SqlAnalystState(question="how many orders?", table_schema="orders(id INT)")
    out = await node.execute(state, _Ctx())
    assert out["succeeded"] is True
    assert len(out["rows"]) == 2
    assert out["attempts"] == 1
    assert "2" in out["answer"]
    assert out["query"] == "SELECT * FROM orders"
    assert out["error"] == ""


async def test_repair_path_recovers_on_second_attempt() -> None:
    calls = {"n": 0}

    def flaky_runner(_sql: str) -> list[dict[str, Any]]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("syntax error near FROM")
        return _rows(3)

    node = Analyze(
        generator=lambda _q, _s, last_error: f"SELECT 1 -- {last_error}",
        runner=flaky_runner,
    )
    state = SqlAnalystState(question="count orders", table_schema="orders(id INT)")
    out = await node.execute(state, _Ctx())
    assert out["succeeded"] is True
    assert out["attempts"] == 2
    assert len(out["rows"]) == 3
    assert out["error"] == ""


async def test_total_failure_after_max_attempts() -> None:
    def always_fails(_sql: str) -> list[dict[str, Any]]:
        raise RuntimeError("no such table: orders")

    node = Analyze(
        generator=lambda _q, _s, _e: "SELECT * FROM orders",
        runner=always_fails,
    )
    state = SqlAnalystState(question="count orders", table_schema="orders(id INT)", max_attempts=3)
    out = await node.execute(state, _Ctx())
    assert out["succeeded"] is False
    assert out["attempts"] == 3
    assert out["rows"] == []
    assert "no such table" in out["error"]
    assert out["answer"] == ""


async def test_blank_question_raises() -> None:
    node = Analyze(generator=lambda _q, _s, _e: "SELECT 1", runner=lambda _sql: [])
    with pytest.raises(ValueError, match="question is required"):
        await node.execute(SqlAnalystState(question="  ", table_schema="orders(id INT)"), _Ctx())


def test_missing_runner_raises() -> None:
    with pytest.raises(ValueError, match="runner is required"):
        Analyze(generator=lambda _q, _s, _e: "SELECT 1")


def test_skill_declares_only_state_channels() -> None:
    assert SQL_ANALYST.kind.value == "agent"
    assert SQL_ANALYST.site_id == "sql-analyst@0.1.0"
    assert SQL_ANALYST.declared_output_keys == frozenset(
        {
            "question",
            "table_schema",
            "max_attempts",
            "query",
            "rows",
            "answer",
            "error",
            "attempts",
            "succeeded",
        }
    )
