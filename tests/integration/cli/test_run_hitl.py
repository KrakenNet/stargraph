# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import Any

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.shortcuts import PromptSession
from rich.console import Console

from stargraph.cli._prompts import HITLHandler
from stargraph.runtime.events import WaitingForInputEvent


class _RunStub:
    """Stand-in for GraphRun that records respond() calls."""

    def __init__(self) -> None:
        self.run_id = "r-test"
        self.state = "awaiting-input"
        self.captured: list[dict[str, Any]] = []

    async def respond(self, response: dict[str, Any], actor: str) -> None:
        self.captured.append({"actor": actor, "response": response})


def _waiting(
    open_questions: list[dict[str, Any]] | None = None,
    *,
    prompt: str = "go",
) -> WaitingForInputEvent:
    payload: dict[str, Any] = {}
    if open_questions is not None:
        payload["open_questions"] = open_questions
    return WaitingForInputEvent(
        run_id="r-test",
        step=1,
        ts=datetime.now(UTC),
        prompt=prompt,
        interrupt_payload=payload,
    )


@pytest.mark.integration
async def test_handler_collects_required_and_skips_optional() -> None:
    run = _RunStub()
    with create_pipe_input() as pipe_in:
        # Two required answers, then 's' to skip optionals.
        pipe_in.send_text('classify, act\n{"doc": "sqlite:./.docs"}\ns\n')
        session: PromptSession[str] = PromptSession(input=pipe_in, output=DummyOutput())
        console = Console(file=io.StringIO(), force_terminal=False, width=120)
        handler = HITLHandler(console, session=session)

        event = _waiting(
            [
                {
                    "slot": "nodes",
                    "kind": "required",
                    "prompt": "Which nodes?",
                    "schema": {"type": "array", "items": {"type": "string"}},
                },
                {
                    "slot": "stores",
                    "kind": "required",
                    "prompt": "Which stores?",
                    "schema": {"type": "object"},
                },
                {
                    "slot": "csv_delim",
                    "kind": "edge_case",
                    "prompt": "Delimiter?",
                    "schema": {"type": "string"},
                },
            ]
        )
        await handler.handle(event, run)  # type: ignore[arg-type]

    assert len(run.captured) == 1
    answers = run.captured[0]["response"]["slot_answers"]
    assert answers["nodes"] == ["classify", "act"]
    assert answers["stores"] == {"doc": "sqlite:./.docs"}
    assert "csv_delim" not in answers


@pytest.mark.integration
async def test_handler_reprompts_on_blank_required() -> None:
    run = _RunStub()
    with create_pipe_input() as pipe_in:
        # First answer blank, then 'alice'. Then 's' for optional batch.
        pipe_in.send_text("\nalice\n")
        session: PromptSession[str] = PromptSession(input=pipe_in, output=DummyOutput())
        handler = HITLHandler(
            Console(file=io.StringIO(), force_terminal=False),
            session=session,
        )
        event = _waiting(
            [
                {
                    "slot": "name",
                    "kind": "required",
                    "prompt": "Name?",
                    "schema": {"type": "string"},
                },
            ]
        )
        await handler.handle(event, run)  # type: ignore[arg-type]

    assert run.captured[0]["response"]["slot_answers"] == {"name": "alice"}


@pytest.mark.integration
async def test_handler_freeform_fallback_when_no_open_questions() -> None:
    run = _RunStub()
    with create_pipe_input() as pipe_in:
        pipe_in.send_text("yes please go ahead\n")
        session: PromptSession[str] = PromptSession(input=pipe_in, output=DummyOutput())
        handler = HITLHandler(
            Console(file=io.StringIO(), force_terminal=False),
            session=session,
        )
        event = _waiting(open_questions=None, prompt="Approve and continue?")
        await handler.handle(event, run)  # type: ignore[arg-type]

    assert run.captured[0]["response"] == {"answer": "yes please go ahead"}


@pytest.mark.integration
async def test_handler_answer_all_optionals() -> None:
    run = _RunStub()
    with create_pipe_input() as pipe_in:
        # 'a' to answer all, then "," for the only optional.
        pipe_in.send_text("a\n,\n")
        session: PromptSession[str] = PromptSession(input=pipe_in, output=DummyOutput())
        handler = HITLHandler(
            Console(file=io.StringIO(), force_terminal=False),
            session=session,
        )
        event = _waiting(
            [
                {
                    "slot": "csv_delim",
                    "kind": "edge_case",
                    "prompt": "Delim?",
                    "schema": {"type": "string"},
                },
            ]
        )
        await handler.handle(event, run)  # type: ignore[arg-type]

    assert run.captured[0]["response"]["slot_answers"] == {"csv_delim": ","}


@pytest.mark.integration
async def test_handler_int_reprompts_on_bad_value() -> None:
    run = _RunStub()
    with create_pipe_input() as pipe_in:
        # First answer "notanumber", reprompted, then "42". Then 's' for optionals.
        pipe_in.send_text("notanumber\n42\n")
        session: PromptSession[str] = PromptSession(input=pipe_in, output=DummyOutput())
        handler = HITLHandler(
            Console(file=io.StringIO(), force_terminal=False),
            session=session,
        )
        event = _waiting(
            [
                {
                    "slot": "n",
                    "kind": "required",
                    "prompt": "How many?",
                    "schema": {"type": "integer"},
                },
            ]
        )
        await handler.handle(event, run)  # type: ignore[arg-type]

    assert run.captured[0]["response"]["slot_answers"] == {"n": 42}
