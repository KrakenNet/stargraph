# SPDX-License-Identifier: Apache-2.0
"""Trainset curator: ledger CRUD, doctor preflight, CLI, and the TUI journey.

The headline journey (CLAUDE.md: UI work starts from a user journey) is the
labeler loop — seed the set, review a pair, attach a verdict, and edit-to-gold
a node so the fix is re-gated before it is stored. Exercised through both the
CLI (`CliRunner`) and the Textual TUI (`run_test` Pilot). Playwright does not
apply to a terminal UI; Textual's own headless driver is the equivalent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import click
import pytest
from textual.widgets import DataTable, Input, TabbedContent
from typer.testing import CliRunner

from stargraph.skills.nodesmith import _ledger
from stargraph.skills.nodesmith._doctor import healthy, run_doctor
from stargraph.skills.nodesmith.cli import app
from stargraph.skills.nodesmith.nodes import build as build_mod
from stargraph.skills.nodesmith.seeds import SEEDS
from stargraph.skills.nodesmith.tui import NodesmithTUI

if TYPE_CHECKING:
    from collections.abc import Callable


class _StubProgram:
    """Stand-in for ``NodeProgram`` — returns a known-good pair, no LLM."""

    _KEYS = ("class_name", "reads", "writes", "fixture", "node_source", "test_source")

    def __init__(self, *_a: object, **_k: object) -> None:
        self._pair = SEEDS[0]

    def generate(
        self, _brief: str, _lessons: list[str], _findings: list[dict[str, object]]
    ) -> dict[str, object]:
        return {k: self._pair[k] for k in self._KEYS}


pytestmark = pytest.mark.integration

runner = CliRunner()

_MARKER = "# ======== TEST (test_node.py) — edit above for node.py ========"


def _editor_returning(text: str) -> Callable[..., str]:
    """A stand-in for ``click.edit`` that returns ``text`` (no real $EDITOR)."""

    def _edit(*_a: object, **_k: object) -> str:
        return text

    return _edit


# --------------------------------------------------------------------------- #
# ledger CRUD / stats / drift
# --------------------------------------------------------------------------- #
def test_seed_is_idempotent_and_readds_deleted() -> None:
    assert _ledger.seed_trainset(SEEDS) == len(SEEDS)
    assert _ledger.seed_trainset(SEEDS) == 0  # ids already present
    victim = _ledger.load_trainset()[0]["id"]
    assert _ledger.delete_trainset(victim) is True
    assert len(_ledger.load_trainset()) == len(SEEDS) - 1
    assert _ledger.seed_trainset(SEEDS) == 1  # only the deleted one comes back


def test_find_and_update_by_prefix() -> None:
    _ledger.seed_trainset(SEEDS)
    assert _ledger.find_trainset("5eed00000001")["class_name"] == "SeverityBand"  # type: ignore[index]
    updated = _ledger.update_trainset("5eed00000001", verdict="reject", reason="nope")
    assert updated is not None
    assert _ledger.find_trainset("5eed00000001")["verdict"] == "reject"  # type: ignore[index]


def test_stats_count_verdicts_and_sources() -> None:
    _ledger.seed_trainset(SEEDS)
    s = _ledger.trainset_stats()
    assert s["total"] == len(SEEDS)
    assert s["seed"] == len(SEEDS)
    assert s["accepted"] == len(SEEDS)  # seeds ship accepted
    assert s["unreviewed"] == 0


def test_drift_excludes_seeds_and_tracks_generator() -> None:
    _ledger.seed_trainset(SEEDS)  # excluded from drift
    _ledger.append_trainset({"brief": "g1", "attempts": 1, "passed": True})
    _ledger.append_trainset({"brief": "g2", "attempts": 3, "passed": True})
    # 1 of 2 generated rows passed first-try; the 8 seeds are excluded
    rate = _ledger.drift_rate(window=10)
    assert rate == pytest.approx(0.5)  # pyright: ignore[reportUnknownMemberType]


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #
def test_doctor_reports_healthy_toolchain() -> None:
    checks = run_doctor()
    assert healthy(checks)
    e2e = next(c for c in checks if c.name == "gate end-to-end")
    assert e2e.ok  # generate files + run code + run tests + verify all work


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_doctor_exits_zero() -> None:
    assert runner.invoke(app, ["doctor"]).exit_code == 0


def test_cli_seed_label_rm() -> None:
    assert runner.invoke(app, ["seed"]).exit_code == 0
    assert len(_ledger.load_trainset()) == len(SEEDS)
    assert runner.invoke(app, ["trainset", "label", "5eed00000002", "--reject"]).exit_code == 0
    assert _ledger.find_trainset("5eed00000002")["verdict"] == "reject"  # type: ignore[index]
    both = runner.invoke(app, ["trainset", "label", "5eed00000002", "--accept", "--reject"])
    assert both.exit_code == 1  # exactly one of --accept/--reject
    assert runner.invoke(app, ["trainset", "rm", "5eed00000002"]).exit_code == 0
    assert _ledger.find_trainset("5eed00000002") is None


def test_cli_list_show_stats_run() -> None:
    _ledger.seed_trainset(SEEDS)
    assert runner.invoke(app, ["trainset", "list"]).exit_code == 0
    assert runner.invoke(app, ["trainset", "show", "5eed00000001"]).exit_code == 0
    assert runner.invoke(app, ["trainset", "stats"]).exit_code == 0


def test_cli_edit_to_gold_stores_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    _ledger.seed_trainset(SEEDS)
    row = _ledger.find_trainset("5eed00000001")
    assert row is not None
    fixed_node = row["node_source"] + "# edited to gold\n"
    buf = f"{fixed_node}\n{_MARKER}\n{row['test_source']}"
    monkeypatch.setattr(click, "edit", _editor_returning(buf))

    assert runner.invoke(app, ["trainset", "edit", "5eed00000001"]).exit_code == 0
    after = _ledger.find_trainset("5eed00000001")
    assert after is not None
    assert after["source"] == _ledger.SOURCE_EDITED
    assert after["verdict"] == "accept"
    assert "# edited to gold" in after["node_source"]


def test_cli_edit_rejects_a_broken_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    _ledger.seed_trainset(SEEDS)
    row = _ledger.find_trainset("5eed00000001")
    assert row is not None
    broken = "def oops(:\n    pass\n"
    buf = f"{broken}\n{_MARKER}\n{row['test_source']}"
    monkeypatch.setattr(click, "edit", _editor_returning(buf))

    assert runner.invoke(app, ["trainset", "edit", "5eed00000001"]).exit_code == 1
    after = _ledger.find_trainset("5eed00000001")
    assert after is not None
    assert after["source"] == _ledger.SOURCE_SEED  # unchanged — a failing edit is never stored


# --------------------------------------------------------------------------- #
# TUI journey (Textual headless Pilot)
# --------------------------------------------------------------------------- #
async def test_tui_seeds_then_labels() -> None:
    app_ = NodesmithTUI()
    async with app_.run_test() as pilot:
        await pilot.pause()
        app_.query_one(TabbedContent).active = "curate"
        await pilot.pause()
        await pilot.press("s")  # load seeds
        await pilot.pause()
        assert len(_ledger.load_trainset()) == len(SEEDS)
        await pilot.press("r")  # reject the highlighted (first) row
        await pilot.pause()
    first = _ledger.load_trainset()[0]
    assert first["verdict"] == "reject"


async def test_tui_edit_to_gold(monkeypatch: pytest.MonkeyPatch) -> None:
    _ledger.seed_trainset(SEEDS)
    row = _ledger.load_trainset()[0]
    fixed_node = row["node_source"] + "# tui edited\n"
    buf = f"{fixed_node}\n{_MARKER}\n{row['test_source']}"
    monkeypatch.setattr(click, "edit", _editor_returning(buf))

    app_ = NodesmithTUI()
    async with app_.run_test() as pilot:
        await pilot.pause()
        app_.query_one(TabbedContent).active = "curate"
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
    after = _ledger.find_trainset(row["id"])
    assert after is not None
    assert after["source"] == _ledger.SOURCE_EDITED
    assert "# tui edited" in after["node_source"]


async def test_tui_generate_records_a_gate_passing_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generate tab drives the real generate→gate→record loop (LLM stubbed)."""
    monkeypatch.setattr(build_mod, "NodeProgram", _StubProgram)

    app_ = NodesmithTUI()
    async with app_.run_test() as pilot:
        await pilot.pause()
        app_.query_one("#brief", Input).value = "a node that bands severity"
        await pilot.click("#gen-btn")
        await cast("Any", app_.workers).wait_for_complete()
        await pilot.pause()
        generated = [r for r in _ledger.load_trainset() if r.get("source") == "generated"]
        assert len(generated) == 1
        assert generated[0]["verdict"] is None  # recorded, unreviewed
        # accept the just-generated pair from the Generate tab
        await pilot.click("#gen-accept")
        await pilot.pause()
    after = next(r for r in _ledger.load_trainset() if r.get("source") == "generated")
    assert after["verdict"] == "accept"


async def test_tui_doctor_tab_runs_checks() -> None:
    app_ = NodesmithTUI()
    async with app_.run_test() as pilot:
        await pilot.pause()
        app_.action_tab("doctor")
        await cast("Any", app_.workers).wait_for_complete()
        await pilot.pause()
        rows = cast("DataTable[Any]", app_.query_one("#doctor-rows", DataTable))
        assert rows.row_count == len(run_doctor())
        assert "healthy" in app_._doctor_text  # pyright: ignore[reportPrivateUsage]


async def test_tui_stats_tab_reports_counts() -> None:
    _ledger.seed_trainset(SEEDS)
    app_ = NodesmithTUI()
    async with app_.run_test() as pilot:
        await pilot.pause()
        app_.action_tab("stats")
        await pilot.pause()
        assert f"total: {len(SEEDS)}" in app_._stats_text  # pyright: ignore[reportPrivateUsage]
        assert "drift" in app_._stats_text  # pyright: ignore[reportPrivateUsage]
