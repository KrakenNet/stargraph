# SPDX-License-Identifier: Apache-2.0
"""Analyze — the bounded generate→run→repair loop over a SQL runner.

An ``agent`` skill: it drives a question over structured data toward a
validated answer rather than performing a single pure transform. The model
call sits behind the injectable ``generator`` seam and the database call behind
the injectable ``runner`` seam (the nodesmith ``Build`` loop pattern, but over
a SQL runner instead of a code gate), so the node's value-add — generating a
query, running it, and repairing it from the last error within a bounded number
of attempts — is exercised in tests with no live model and no live database.
``_default_generator`` is the production DSPy path; there is no live-DB default
runner, so one must always be injected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    # (question, schema, last_error) -> the SQL the model produced.
    Generator = Callable[[str, str, str], str]
    # (sql) -> the rows the database returned.
    Runner = Callable[[str], list[dict[str, Any]]]


class Analyze(NodeBase):
    def __init__(
        self,
        generator: Generator | None = None,
        runner: Runner | None = None,
    ) -> None:
        # No safe default database exists, so a runner must always be injected.
        if runner is None:
            raise ValueError("runner is required: there is no safe default database to query")
        self._generator = generator or _default_generator
        self._runner = runner

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        del ctx  # no per-run context needed for the bounded loop
        question = str(getattr(state, "question", "") or "")
        table_schema = str(getattr(state, "table_schema", "") or "")
        max_attempts = int(getattr(state, "max_attempts", 3) or 3)
        if not question.strip():
            raise ValueError("question is required: nothing to analyze")

        sql = ""
        rows: list[dict[str, Any]] = []
        last_error = ""
        succeeded = False
        attempts = 0

        while attempts < max_attempts:
            attempts += 1
            sql = self._generator(question, table_schema, last_error)
            try:
                rows = self._runner(sql)
            except Exception as exc:  # any runner failure feeds the repair loop
                last_error = str(exc)
                continue
            succeeded = True
            last_error = ""
            break

        answer = f"{len(rows)} row(s)" if succeeded else ""
        return {
            "query": sql,
            "rows": rows,
            "answer": answer,
            "error": last_error,
            "attempts": attempts,
            "succeeded": succeeded,
        }


def _default_generator(question: str, schema: str, last_error: str) -> str:
    """Production generator — one DSPy call returning SQL for the question.

    Imported lazily so the skill (and its tests, which inject a stub) never pull
    in DSPy unless a real generation runs.
    """
    import dspy  # pyright: ignore[reportMissingTypeStubs]

    predictor = dspy.Predict("question, schema, last_error -> sql")  # pyright: ignore[reportUnknownMemberType]
    result = predictor(question=question, schema=schema, last_error=last_error)  # pyright: ignore[reportUnknownVariableType]
    sql = getattr(result, "sql", "")
    return sql if isinstance(sql, str) else ""
