# SPDX-License-Identifier: Apache-2.0
"""Textual TUI — the nodesmith console (use *and* tweak, FR Q3).

Four tabs behind one app:

* **Generate** — enter a brief + model/url, run the real generate→gate→repair
  loop in a worker, watch the attempts, then accept / reject / edit-to-gold the
  result. This is the "use it" half — the same path as ``nodesmith make``.
* **Curate** — browse the (spec → node) trainset, attach verdicts, edit-to-gold.
* **Doctor** — prove the run/test/verify/write toolchain end-to-end.
* **Stats** — counts by verdict/source plus the generator drift signal.

The LLM call sits behind ``Build._program`` (the stub seam), so the Generate
path is driven headless in tests with no live model. Edit-to-gold re-gates the
fix before storing it — a failing edit is never saved.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar, cast

import click
from rich.console import Group
from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Log,
    Sparkline,
    Static,
    TabbedContent,
    TabPane,
)

from stargraph.skills.nodesmith import _curate, _ledger
from stargraph.skills.nodesmith._curate import short_id as _short
from stargraph.skills.nodesmith.gate import NODE_FILE, TEST_FILE, all_passed
from stargraph.skills.nodesmith.seeds import SEEDS

if TYPE_CHECKING:
    from textual.binding import BindingType

_CTX = SimpleNamespace(run_id="nodesmith-tui")


def _pair_renderable(row: dict[str, Any]) -> Group:
    """A node.py + test_node.py syntax-highlighted view of one trainset row."""
    head = Text.assemble(
        (f"{_short(row)}  ", "bold"),
        (f"source={row.get('source')}  verdict={row.get('verdict') or '—'}\n", "cyan"),
        (f"brief: {row.get('brief')}\n", ""),
        (f"reads={row.get('reads')}  writes={row.get('writes')}\n", "dim"),
    )
    return Group(
        head,
        Text("node.py", style="bold green"),
        Syntax(str(row.get("node_source", "")), "python", theme="ansi_dark"),
        Text("test_node.py", style="bold green"),
        Syntax(str(row.get("test_source", "")), "python", theme="ansi_dark"),
    )


class NodesmithTUI(App[None]):
    CSS = """
    #rows { width: 50%; }
    #detail, #gen-detail { width: 1fr; padding: 0 1; }
    #gen-log { height: 8; border: round $accent; }
    .field { height: 3; }
    Input { width: 1fr; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        ("1", "tab('generate')", "Generate"),
        ("2", "tab('curate')", "Curate"),
        ("3", "tab('doctor')", "Doctor"),
        ("4", "tab('stats')", "Stats"),
        ("g", "generate", "Generate"),
        ("a", "accept", "Accept"),
        ("r", "reject", "Reject"),
        ("e", "edit", "Edit→gold"),
        ("d", "delete", "Delete"),
        ("s", "seed", "Seed"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[dict[str, Any]] = []
        self._last_generated: dict[str, Any] | None = None
        self._doctor_ran = False
        # Last text rendered into the doctor/stats panes — a typed seam tests
        # assert on without reaching into Textual widget internals.
        self._doctor_text = ""
        self._stats_text = ""

    # --- layout ------------------------------------------------------------ #
    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="generate"):
            with TabPane("Generate", id="generate"):
                yield Input(placeholder="brief: what the node should do…", id="brief")
                with Horizontal(classes="field"):
                    yield Input(
                        value=os.environ.get("LLM_OLLAMA_MODEL", ""),
                        placeholder="model (e.g. laguna-xs)",
                        id="model",
                    )
                    yield Input(
                        value=os.environ.get("LLM_OLLAMA_URL", ""),
                        placeholder="lm url",
                        id="url",
                    )
                    yield Input(placeholder="lm key", password=True, id="key")
                with Horizontal(classes="field"):
                    yield Button("Generate", id="gen-btn", variant="primary")
                    yield Button("Accept", id="gen-accept")
                    yield Button("Reject", id="gen-reject")
                    yield Button("Edit→gold", id="gen-edit")
                yield Log(id="gen-log")
                with VerticalScroll():
                    yield Static(id="gen-detail")
            with TabPane("Curate", id="curate"), Horizontal():
                yield DataTable(id="rows")
                with VerticalScroll():
                    yield Static(id="detail")
            with TabPane("Doctor", id="doctor"):
                yield DataTable(id="doctor-rows")
                yield Static(id="doctor-summary")
            with TabPane("Stats", id="stats"):
                yield Static(id="stats-body")
                yield Sparkline([], id="drift-spark")
        yield Footer()

    def on_mount(self) -> None:
        table = cast("DataTable[Any]", self.query_one("#rows", DataTable))
        table.cursor_type = "row"
        table.add_columns("id", "source", "verdict", "class", "brief")
        dtable = cast("DataTable[Any]", self.query_one("#doctor-rows", DataTable))
        dtable.add_columns("check", "status", "detail")
        self._reload()

    # --- tab switching ----------------------------------------------------- #
    def _active(self) -> str:
        return self.query_one(TabbedContent).active

    def action_tab(self, pane: str) -> None:
        self.query_one(TabbedContent).active = pane
        self._on_tab(pane)

    def on_tabbed_content_tab_activated(self, _event: TabbedContent.TabActivated) -> None:
        # Covers clicking the tab bar / arrow-key switches; action_tab covers the
        # digit bindings and programmatic switches.
        self._on_tab(self._active())

    def _on_tab(self, active: str) -> None:
        if active == "doctor" and not self._doctor_ran:
            self._run_doctor()
        elif active == "stats":
            self._refresh_stats()

    # --- curate data ------------------------------------------------------- #
    def _reload(self, keep: int | None = None) -> None:
        table = cast("DataTable[Any]", self.query_one("#rows", DataTable))
        idx = keep if keep is not None else table.cursor_row
        table.clear()
        self._rows = _ledger.load_trainset()
        for r in self._rows:
            brief = str(r.get("brief", ""))
            table.add_row(
                _short(r),
                str(r.get("source", "")),
                r.get("verdict") or "—",
                str(r.get("class_name", "")),
                brief if len(brief) <= 40 else brief[:37] + "...",
            )
        if self._rows:
            table.move_cursor(row=max(0, min(idx, len(self._rows) - 1)))
        self._show_detail()

    def _curate_row(self) -> dict[str, Any] | None:
        table = cast("DataTable[Any]", self.query_one("#rows", DataTable))
        if not self._rows or table.cursor_row < 0:
            return None
        return self._rows[table.cursor_row]

    def _show_detail(self) -> None:
        detail = self.query_one("#detail", Static)
        row = self._curate_row()
        if row is None:
            detail.update("[dim]empty — press 's' to load seeds.[/]")
            return
        detail.update(_pair_renderable(row))

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        self._show_detail()

    # --- target for accept/reject/edit (which tab is in focus) ------------- #
    def _target_row(self) -> dict[str, Any] | None:
        if self._active() == "generate":
            return self._last_generated
        if self._active() == "curate":
            return self._curate_row()
        return None

    # --- generate ---------------------------------------------------------- #
    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "brief":
            self.action_generate()

    def action_generate(self) -> None:
        if self._active() != "generate":
            return
        brief = self.query_one("#brief", Input).value.strip()
        if not brief:
            self.notify("brief is required", severity="error")
            return
        model = self.query_one("#model", Input).value.strip()
        url = self.query_one("#url", Input).value.strip()
        key = self.query_one("#key", Input).value.strip() or "placeholder"
        log = self.query_one("#gen-log", Log)
        log.clear()
        log.write_line(f"generating: {brief}")
        self.run_worker(self._generate(brief, model, url, key), exclusive=True, group="gen")

    async def _generate(self, brief: str, model: str, url: str, key: str) -> None:
        from stargraph.skills.nodesmith.nodes.build import Build
        from stargraph.skills.nodesmith.nodes.recall import Recall
        from stargraph.skills.nodesmith.program import configure_lm
        from stargraph.skills.nodesmith.state import State

        log = self.query_one("#gen-log", Log)
        try:
            if url:
                configure_lm(url, model, key)
            state = State(brief=brief, model_id=model)
            recalled = await Recall().execute(state, _CTX)  # pyright: ignore[reportArgumentType]
            state = state.model_copy(update=recalled)
            out = await Build().execute(state, _CTX)  # pyright: ignore[reportArgumentType]
        except Exception as exc:
            log.write_line(f"error: {type(exc).__name__}: {exc}")
            self.notify("generation failed (see log)", severity="error")
            return

        results = out.get("verifier_results", [])
        for r in results:
            mark = "✓" if getattr(r, "passed", False) else "✗"
            log.write_line(f"  {mark} {getattr(r, 'kind', '?')}")
        log.write_line(f"{out.get('fix_attempts')} attempt(s)")

        if not all_passed(results):
            self._last_generated = None
            self.query_one("#gen-detail", Static).update(
                "[red]gate failed[/] — not recorded (only gate-passing builds are stored)."
            )
            self.notify("gate failed — not recorded", severity="warning")
            return

        files = out.get("artifact_files", {})
        class_name = out.get("class_name", "")
        row = _ledger.append_trainset(
            {
                "brief": brief,
                "class_name": class_name,
                "node_name": class_name,
                "reads": out.get("reads", []),
                "writes": out.get("writes", []),
                "fixture": out.get("fixture", {}),
                "node_source": files.get(NODE_FILE, ""),
                "test_source": files.get(TEST_FILE, ""),
                "model_id": model,
                "attempts": out.get("fix_attempts", 1),
                "passed": True,
                "verdict": None,
            }
        )
        self._last_generated = row
        self.query_one("#gen-detail", Static).update(_pair_renderable(row))
        self.notify(f"recorded {_short(row)} — accept / reject / edit→gold")
        self._reload()
        self._refresh_stats()

    # --- actions (accept / reject / edit / delete / seed) ------------------ #
    def _label(self, verdict: str) -> None:
        row = self._target_row()
        if row is None:
            return
        updated = _ledger.update_trainset(str(row.get("id", "")), verdict=verdict)
        if self._last_generated is not None and self._last_generated.get("id") == row.get("id"):
            self._last_generated = updated
        self.notify(f"{_short(row)} → {verdict}")
        self._reload()
        self._refresh_stats()

    def action_accept(self) -> None:
        self._label("accept")

    def action_reject(self) -> None:
        self._label("reject")

    def action_delete(self) -> None:
        if self._active() != "curate":
            return
        row = self._curate_row()
        if row is None:
            return
        _ledger.delete_trainset(str(row.get("id", "")))
        self.notify(f"deleted {_short(row)}")
        self._reload()
        self._refresh_stats()

    def action_seed(self) -> None:
        added = _ledger.seed_trainset(SEEDS)
        self.notify(f"seeded {added} new pair(s)")
        self._reload()
        self._refresh_stats()

    def action_edit(self) -> None:
        row = self._target_row()
        if row is None:
            return
        buffer = _curate.build_edit_buffer(row)
        try:
            with self.suspend():  # hand the terminal to $EDITOR
                edited = click.edit(buffer, extension=".py")
        except SuspendNotSupported:
            edited = click.edit(buffer, extension=".py")
        if not edited or edited == buffer:
            self.notify("no changes")
            return
        ok, msg = _curate.apply_edit(str(row.get("id", "")), edited)
        self.notify(msg, severity="information" if ok else "error")
        if ok:
            if self._last_generated is not None and self._last_generated.get("id") == row.get("id"):
                self._last_generated = _ledger.find_trainset(str(row.get("id", "")))
            self._reload()
            self._refresh_stats()

    # --- generate-tab buttons (so accept/edit work without leaving an Input) #
    def on_button_pressed(self, event: Button.Pressed) -> None:
        handlers = {
            "gen-btn": self.action_generate,
            "gen-accept": self.action_accept,
            "gen-reject": self.action_reject,
            "gen-edit": self.action_edit,
        }
        handler = handlers.get(event.button.id or "")
        if handler is not None:
            handler()

    # --- doctor ------------------------------------------------------------ #
    def _run_doctor(self) -> None:
        self._doctor_ran = True
        self.query_one("#doctor-summary", Static).update("[dim]running…[/]")
        self.run_worker(self._doctor(), exclusive=True, group="doctor")

    async def _doctor(self) -> None:
        from stargraph.skills.nodesmith._doctor import healthy, run_doctor

        checks = await asyncio.to_thread(run_doctor)
        table = cast("DataTable[Any]", self.query_one("#doctor-rows", DataTable))
        table.clear()
        for c in checks:
            table.add_row(c.name, "[green]ok[/]" if c.ok else "[red]FAIL[/]", c.detail)
        ok = healthy(checks)
        self._doctor_text = (
            "healthy — toolchain functional." if ok else "unhealthy — a hard check failed."
        )
        self.query_one("#doctor-summary", Static).update(
            f"[green]{self._doctor_text}[/]" if ok else f"[red]{self._doctor_text}[/]"
        )

    # --- stats ------------------------------------------------------------- #
    def _refresh_stats(self) -> None:
        s = _ledger.trainset_stats()
        lines = [f"{k}: {v}" for k, v in s.items()]
        lines.append(f"drift (first-try pass rate): {_ledger.drift_rate():.0%}")
        self._stats_text = "\n".join(lines)
        self.query_one("#stats-body", Static).update(self._stats_text)
        # Sparkline of per-generated-row first-try success (real data, no fakes).
        gen = [
            r
            for r in _ledger.load_trainset()
            if r.get("source", _ledger.SOURCE_GENERATED) == _ledger.SOURCE_GENERATED
        ]
        series = [1.0 if int(r.get("attempts", 1)) == 1 else 0.0 for r in gen]
        self.query_one("#drift-spark", Sparkline).data = series


def run_tui() -> None:
    NodesmithTUI().run()
