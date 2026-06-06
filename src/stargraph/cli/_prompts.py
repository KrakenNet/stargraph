# SPDX-License-Identifier: Apache-2.0
"""Inline HITL handler -- walks the operator through open questions."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from prompt_toolkit.shortcuts import PromptSession

if TYPE_CHECKING:
    from rich.console import Console

    from stargraph.graph.run import GraphRun
    from stargraph.runtime.events import WaitingForInputEvent


def _coerce(raw: str, schema: dict[str, Any]) -> Any:
    t = schema.get("type", "string")
    raw = raw.strip()
    if t == "string":
        return raw
    if t == "integer":
        return int(raw)
    if t == "number":
        return float(raw)
    if t == "boolean":
        return raw.lower() in {"y", "yes", "true", "1"}
    if t == "array":
        items = cast("dict[str, Any]", schema.get("items") or {"type": "string"})
        item_t = items.get("type", "string")
        if item_t in {"string", "integer", "number", "boolean"}:
            parts = [p.strip() for p in raw.split(",")]
            return [_coerce(p, items) for p in parts if p]
        return json.loads(raw)
    if t == "object":
        return json.loads(raw)
    return raw  # unknown -> raw string


class HITLHandler:
    """Inline handler that resolves a `WaitingForInputEvent` via stdin prompts."""

    def __init__(
        self,
        console: Console,
        *,
        session: PromptSession[str] | None = None,
    ) -> None:
        self._console = console
        self._session: PromptSession[str] = session or PromptSession()

    async def handle(self, event: WaitingForInputEvent, run: GraphRun) -> None:
        """Read open_questions or fall back to free-form, build response, call run.respond."""
        questions = event.interrupt_payload.get("open_questions")
        if isinstance(questions, list) and questions:
            await self._handle_structured(cast("list[dict[str, Any]]", questions), run)
        else:
            await self._handle_freeform(event.prompt, run)

    async def _handle_freeform(self, prompt: str, run: GraphRun) -> None:
        self._console.print(f"\n[yellow]⏸ {prompt}[/yellow]")
        raw = await self._session.prompt_async("> ")
        await run.respond(response={"answer": raw.strip()}, actor="cli")

    async def _handle_structured(self, questions: list[dict[str, Any]], run: GraphRun) -> None:
        required = [q for q in questions if q.get("kind") == "required"]
        optional = [q for q in questions if q.get("kind") != "required"]

        answers: dict[str, Any] = {}

        if required:
            self._console.print(f"\n[bold]Required ({len(required)}):[/bold]")
            for q in required:
                schema = cast("dict[str, Any]", q.get("schema") or {"type": "string"})
                slot = str(q["slot"])
                while True:
                    self._console.print(f"  [cyan]{slot}[/cyan]: {q.get('prompt', '')}")
                    raw = await self._session.prompt_async("  > ")
                    if not raw.strip():
                        self._console.print("  [yellow](required -- please answer)[/yellow]")
                        continue
                    try:
                        answers[slot] = _coerce(raw, schema)
                        break
                    except (ValueError, json.JSONDecodeError) as e:
                        self._console.print(f"  [red]parse error: {e}; try again[/red]")

        if optional:
            self._console.print(f"\n[dim]Optional ({len(optional)}):[/dim]")
            choice = (
                (
                    await self._session.prompt_async(
                        "  [a] answer all  [s] skip all  [n] one-by-one: "
                    )
                )
                .strip()
                .lower()
            )
            if choice in {"a", "n"}:
                for q in optional:
                    schema = cast("dict[str, Any]", q.get("schema") or {"type": "string"})
                    slot = str(q["slot"])
                    self._console.print(f"  [cyan]{slot}[/cyan]: {q.get('prompt', '')}")
                    label = "  > " if choice == "a" else f"  {slot} (blank to skip): "
                    raw = await self._session.prompt_async(label)
                    if not raw.strip():
                        continue
                    try:
                        answers[slot] = _coerce(raw, schema)
                    except (ValueError, json.JSONDecodeError) as e:
                        self._console.print(f"  [red]skipped {slot}: {e}[/red]")
            # 's' or anything else: skip all

        await run.respond(response={"slot_answers": answers}, actor="cli")
