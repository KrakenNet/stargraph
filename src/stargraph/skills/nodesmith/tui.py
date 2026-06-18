# SPDX-License-Identifier: Apache-2.0
"""Textual TUI — interactive trainset curator (the labeling loop, visual).

Browse the (spec → node) pairs, read the generated node + test, and attach a
human verdict. ``e`` is edit-to-gold: drop to ``$EDITOR``, fix the node, and it
is re-gated before being stored as an accepted example — a failing edit is
never saved. Imports the ledger + gate in-process; no subprocess seam.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, cast

import click
from rich.console import Group
from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Static

from stargraph.skills.nodesmith import _curate, _ledger
from stargraph.skills.nodesmith._curate import short_id as _short
from stargraph.skills.nodesmith.seeds import SEEDS

if TYPE_CHECKING:
    from textual.binding import BindingType


class TrainsetTUI(App[None]):
    CSS = """
    #rows { width: 50%; }
    #detail { width: 50%; padding: 0 1; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
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

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield DataTable(id="rows")
            with VerticalScroll():
                yield Static(id="detail")
        yield Footer()

    def on_mount(self) -> None:
        table = cast("DataTable[Any]", self.query_one("#rows", DataTable))
        table.cursor_type = "row"
        table.add_columns("id", "source", "verdict", "class", "brief")
        self._reload()

    # --- data -------------------------------------------------------------- #
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

    def _current(self) -> dict[str, Any] | None:
        table = cast("DataTable[Any]", self.query_one("#rows", DataTable))
        if not self._rows or table.cursor_row < 0:
            return None
        return self._rows[table.cursor_row]

    def _show_detail(self) -> None:
        detail = self.query_one("#detail", Static)
        row = self._current()
        if row is None:
            detail.update("[dim]empty — press 's' to load seeds.[/]")
            return
        head = Text.assemble(
            (f"{_short(row)}  ", "bold"),
            (f"source={row.get('source')}  verdict={row.get('verdict') or '—'}\n", "cyan"),
            (f"brief: {row.get('brief')}\n", ""),
            (f"reads={row.get('reads')}  writes={row.get('writes')}\n", "dim"),
        )
        detail.update(
            Group(
                head,
                Text("node.py", style="bold green"),
                Syntax(str(row.get("node_source", "")), "python", theme="ansi_dark"),
                Text("test_node.py", style="bold green"),
                Syntax(str(row.get("test_source", "")), "python", theme="ansi_dark"),
            )
        )

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        self._show_detail()

    # --- actions ----------------------------------------------------------- #
    def _label(self, verdict: str) -> None:
        row = self._current()
        if row is None:
            return
        _ledger.update_trainset(str(row.get("id", "")), verdict=verdict)
        self.notify(f"{_short(row)} → {verdict}")
        self._reload()

    def action_accept(self) -> None:
        self._label("accept")

    def action_reject(self) -> None:
        self._label("reject")

    def action_delete(self) -> None:
        row = self._current()
        if row is None:
            return
        _ledger.delete_trainset(str(row.get("id", "")))
        self.notify(f"deleted {_short(row)}")
        self._reload()

    def action_seed(self) -> None:
        added = _ledger.seed_trainset(SEEDS)
        self.notify(f"seeded {added} new pair(s)")
        self._reload()

    def action_edit(self) -> None:
        row = self._current()
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
            self._reload()


def run_tui() -> None:
    TrainsetTUI().run()
