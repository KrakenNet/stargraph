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
from textual.widgets import DataTable, Input, Select, TabbedContent
from typer.testing import CliRunner

from stargraph.skills._smith import web
from stargraph.skills.nodesmith import _ledger, retrieval
from stargraph.skills.nodesmith import tui as tui_mod
from stargraph.skills.nodesmith._doctor import healthy, run_doctor
from stargraph.skills.nodesmith.cli import app
from stargraph.skills.nodesmith.nodes import build as build_mod
from stargraph.skills.nodesmith.program import configure_lm
from stargraph.skills.nodesmith.seeds import SEEDS
from stargraph.skills.nodesmith.tui import ClarifyModal, NodesmithTUI

if TYPE_CHECKING:
    from collections.abc import Callable


class _StubProgram:
    """Stand-in for ``NodeProgram`` — returns a known-good pair, no LLM."""

    _KEYS = ("class_name", "reads", "writes", "fixture", "node_source", "test_source")

    def __init__(self, *_a: object, **_k: object) -> None:
        self._pair = SEEDS[0]

    def generate(
        self,
        _brief: str,
        _lessons: list[str],
        _findings: list[dict[str, object]],
        _context: str = "",
    ) -> dict[str, object]:
        return {k: self._pair[k] for k in self._KEYS}


class _CtxCaptureProgram(_StubProgram):
    """Like _StubProgram but records the relevant_context it was handed, so a test
    can assert RAG grounding actually reached the generator."""

    last_context = ""

    def generate(
        self,
        brief: str,
        lessons: list[str],
        findings: list[dict[str, object]],
        context: str = "",
    ) -> dict[str, object]:
        type(self).last_context = context
        return super().generate(brief, lessons, findings, context)


class _BrokenProgram:
    """Stand-in that always emits an un-gateable node, so every repair attempt
    fails — drives the "stuck after repairs" clarify path."""

    def __init__(self, *_a: object, **_k: object) -> None:
        self._pair = SEEDS[0]

    def generate(
        self,
        _brief: str,
        _lessons: list[str],
        _findings: list[dict[str, object]],
        _context: str = "",
    ) -> dict[str, object]:
        out = {k: self._pair[k] for k in _StubProgram._KEYS}  # pyright: ignore[reportPrivateUsage]
        out["node_source"] = "def broken(:\n"  # SyntaxError → static tier fails
        return out


pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture(autouse=True)
def _offline_generation(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """Keep the Generate journey offline by default: no clarification needed and
    no web research (both consult the model / network otherwise). Local RAG
    retrieval still runs live. The clarify/web-specific tests override in-body."""
    import stargraph.skills._smith.web as web_mod
    import stargraph.skills.nodesmith.program as prog

    def _no_clarify(_brief: str, _findings: list[dict[str, Any]]) -> dict[str, Any]:
        return {"needs": False, "question": "", "options": []}

    def _decline(_brief: str) -> tuple[bool, list[str]]:
        return False, []

    # Neutralize the *decision* (no model call, no network), but leave research()
    # itself real so the web-specific test can exercise it by overriding _decide.
    monkeypatch.setattr(prog, "clarify", _no_clarify)
    monkeypatch.setattr(web_mod, "_decide", _decline)


async def _wait_for_modal(pilot: Any, app_: NodesmithTUI) -> ClarifyModal:
    """Pump the loop until the clarify modal is on top (the worker is blocked on
    push_screen_wait); fail loudly if it never appears."""
    for _ in range(200):
        await pilot.pause()
        if isinstance(app_.screen, ClarifyModal):
            return app_.screen
    raise AssertionError("clarify modal did not appear")


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
        app_.query_one("#model", Input).value = "stub-model"
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


async def test_tui_generate_scopes_lm_with_dspy_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: Generate runs in a Textual worker task, where dspy.configure()
    is illegal once DSPy is owned by another task. Own DSPy from the test task,
    then run Generate and assert it still records — proving the worker uses
    dspy.context(), not the global configure_lm() (which raises 'can only be
    called from the same async task that called it first')."""
    monkeypatch.setattr(build_mod, "NodeProgram", _StubProgram)
    configure_lm("owner-model", url="http://test-owner:11434")  # test task owns DSPy

    app_ = NodesmithTUI()
    async with app_.run_test() as pilot:
        await pilot.pause()
        app_.query_one("#brief", Input).value = "a node that bands severity"
        app_.query_one("#model", Input).value = "stub-model"
        await pilot.click("#gen-btn")
        await cast("Any", app_.workers).wait_for_complete()
        await pilot.pause()
    # A record only exists if the worker completed; the bug raised before Build.
    generated = [r for r in _ledger.load_trainset() if r.get("source") == "generated"]
    assert len(generated) == 1


async def test_tui_generate_passes_knobs_to_the_lm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Journey: the user sets model + temperature + context length + max tokens
    in the Generate tab; those values reach make_lm (so the knobs are real, not
    decorative). The LM object is built but never called — the stubbed program
    does the generation — so no Ollama server is needed."""
    monkeypatch.setattr(build_mod, "NodeProgram", _StubProgram)
    import stargraph.skills.nodesmith.program as prog

    captured: dict[str, Any] = {}
    real_make_lm = prog.make_lm

    def spy_make_lm(model: str, **kw: Any) -> Any:
        captured["model"] = model
        captured.update(kw)
        return real_make_lm(model, **kw)

    monkeypatch.setattr(prog, "make_lm", spy_make_lm)

    app_ = NodesmithTUI()
    async with app_.run_test() as pilot:
        await pilot.pause()
        app_.query_one("#brief", Input).value = "a node that bands severity"
        app_.query_one("#model", Input).value = "laguna-xs"
        app_.query_one("#temperature", Input).value = "0.2"
        app_.query_one("#num-ctx", Input).value = "8192"
        app_.query_one("#max-tokens", Input).value = "256"
        await pilot.click("#gen-btn")
        await cast("Any", app_.workers).wait_for_complete()
        await pilot.pause()

    assert captured["model"] == "laguna-xs"
    assert captured["temperature"] == 0.2
    assert captured["num_ctx"] == 8192
    assert captured["max_tokens"] == 256


async def test_tui_model_dropdown_lists_models_with_text_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Journey: the model picker fills from Ollama's installed list; choosing one
    drives generation, and if nothing's chosen the free-text field is the
    fallback (so a missing server never blocks you)."""

    def _stub_models(*_a: object, **_k: object) -> list[str]:
        return ["m-a", "m-b"]

    monkeypatch.setattr(tui_mod, "_fetch_ollama_models", _stub_models)

    app_ = NodesmithTUI()
    async with app_.run_test() as pilot:
        await pilot.pause()
        await cast("Any", app_.workers).wait_for_complete()  # on-mount model fetch
        await pilot.pause()
        select = cast("Any", app_.query_one("#model-select", Select))
        select.value = "m-a"
        assert app_._current_model() == "m-a"  # pyright: ignore[reportPrivateUsage]
        # clear the dropdown → fall back to the typed field
        select.clear()
        app_.query_one("#model", Input).value = "typed-model"
        assert app_._current_model() == "typed-model"  # pyright: ignore[reportPrivateUsage]


async def test_tui_generate_preflight_clarifies_ambiguous_brief(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Journey: an underspecified brief → the generator asks first. The user picks
    an offered option (multiple choice), the answer is folded into the brief, and
    generation proceeds — recording a pair whose brief carries the clarification."""
    monkeypatch.setattr(build_mod, "NodeProgram", _StubProgram)
    import stargraph.skills.nodesmith.program as prog

    def _clar(_brief: str, findings: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "needs": not findings,  # pre-flight only (no findings yet)
            "question": "severity bands or a numeric score?",
            "options": ["bands", "numeric"],
        }

    monkeypatch.setattr(prog, "clarify", _clar)

    app_ = NodesmithTUI()
    async with app_.run_test() as pilot:
        await pilot.pause()
        await cast("Any", app_.workers).wait_for_complete()  # on-mount model fetch
        app_.query_one("#brief", Input).value = "a node about severity"
        app_.query_one("#model", Input).value = "stub-model"
        await pilot.click("#gen-btn")
        await _wait_for_modal(pilot, app_)
        await pilot.click("#clarify-opt-0")  # choose "bands"
        await cast("Any", app_.workers).wait_for_complete()
        await pilot.pause()
    generated = [r for r in _ledger.load_trainset() if r.get("source") == "generated"]
    assert len(generated) == 1
    assert "[clarification]" in generated[0]["brief"]
    assert "bands" in generated[0]["brief"]  # the chosen option reached the brief


async def test_tui_generate_clarifies_when_stuck_after_repairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Journey: the repair loop exhausts its attempts → the generator asks how to
    proceed (fed the failures). The user answers in free text (the fallback when no
    options are offered); a clarified retry runs. The broken stub keeps failing, so
    nothing is recorded — the point is that the stuck-state question fired."""
    monkeypatch.setattr(build_mod, "NodeProgram", _BrokenProgram)
    import stargraph.skills.nodesmith.program as prog

    def _clar(_brief: str, findings: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "needs": bool(findings),  # silent pre-flight; ask only once stuck
            "question": "what should it do on empty input?",
            "options": [],
        }

    monkeypatch.setattr(prog, "clarify", _clar)

    app_ = NodesmithTUI()
    async with app_.run_test() as pilot:
        await pilot.pause()
        await cast("Any", app_.workers).wait_for_complete()
        app_.query_one("#brief", Input).value = "an ambiguous node"
        app_.query_one("#model", Input).value = "stub-model"
        await pilot.click("#gen-btn")
        modal = await _wait_for_modal(pilot, app_)  # appears only after the build fails
        text = modal.query_one("#clarify-text", Input)
        text.value = "return a default"
        text.focus()
        await pilot.pause()
        await pilot.press("enter")  # submit the free-text answer
        await cast("Any", app_.workers).wait_for_complete()
        await pilot.pause()
    # broken stub never passes, so nothing is recorded; the question still fired.
    assert not [r for r in _ledger.load_trainset() if r.get("source") == "generated"]
    assert app_._last_generated is None  # pyright: ignore[reportPrivateUsage]


# --------------------------------------------------------------------------- #
# RAG retrieval + web research (grounding)
# --------------------------------------------------------------------------- #
def test_retrieve_context_grounds_on_repo_and_ledger() -> None:
    """RAG pulls the real NodeBase contract from repo source AND the most relevant
    accepted pair from the ledger — no hardcoded context."""
    _ledger.seed_trainset(SEEDS)
    snippets = retrieval.retrieve_context("classify severity into bands", k=4)
    assert snippets
    text = retrieval.format_context(snippets)
    assert "NodeBase" in text  # repo contract grounded
    assert any(s.source.startswith("ledger:") for s in snippets)  # an accepted pair surfaced


def test_retrieve_context_empty_brief_returns_nothing() -> None:
    _ledger.seed_trainset(SEEDS)
    assert retrieval.retrieve_context("   ", k=4) == []


async def test_tui_generate_injects_retrieved_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Journey: the build program is handed a non-empty relevant_context retrieved
    from repo source + accepted pairs (RAG actually reaches generation)."""
    _ledger.seed_trainset(SEEDS)
    _CtxCaptureProgram.last_context = ""
    monkeypatch.setattr(build_mod, "NodeProgram", _CtxCaptureProgram)

    app_ = NodesmithTUI()
    async with app_.run_test() as pilot:
        await pilot.pause()
        await cast("Any", app_.workers).wait_for_complete()
        app_.query_one("#brief", Input).value = "classify severity into bands"
        app_.query_one("#model", Input).value = "stub-model"
        await pilot.click("#gen-btn")
        await cast("Any", app_.workers).wait_for_complete()
        await pilot.pause()
    ctx = _CtxCaptureProgram.last_context
    assert ctx  # grounding was injected
    assert "NodeBase" in ctx  # repo contract reached the generator


def test_research_injects_web_snippets(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the model decides it needs research, search + fetch results become
    grounding snippets — search/fetch stubbed so no network is touched."""

    def _decide_yes(_brief: str) -> tuple[bool, list[str]]:
        return True, ["stargraph node base"]

    def _search(_q: str, **_k: object) -> list[dict[str, str]]:
        return [{"title": "Docs", "url": "https://x/y", "snippet": "how nodes work"}]

    def _fetch(_u: str, **_k: object) -> str:
        return "full page body text"

    monkeypatch.setattr(web, "_decide", _decide_yes)
    monkeypatch.setattr(web, "web_search", _search)
    monkeypatch.setattr(web, "web_fetch", _fetch)

    out = web.research("how do I write a node")
    assert out
    text = retrieval.format_context(out)
    assert "how nodes work" in text  # search snippet landed
    assert "full page body text" in text  # the top hit was fetched for depth


def test_research_skips_when_model_declines(monkeypatch: pytest.MonkeyPatch) -> None:
    def _decide_no(_brief: str) -> tuple[bool, list[str]]:
        return False, []

    monkeypatch.setattr(web, "_decide", _decide_no)
    assert web.research("a self-contained node") == []


def test_web_search_is_best_effort_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A network/transport failure yields [] (never raises), so generation falls
    back to local RAG context."""

    def _boom(*_a: object, **_k: object) -> str:
        raise RuntimeError("no network")

    monkeypatch.setattr(web, "_http_get", _boom)
    assert web.web_search("anything") == []
    assert web.web_fetch("https://x/y") == ""


async def test_tui_test_tab_runs_node_on_given_inputs() -> None:
    """Journey: pick a node, see an input box per declared read (prefilled from
    its fixture), Run it, and get the actual output keyed by the declared writes.
    Executes the real node in the gate's subprocess — no stubbing."""
    _ledger.seed_trainset(SEEDS)
    app_ = NodesmithTUI()
    async with app_.run_test() as pilot:
        await pilot.pause()
        await cast("Any", app_.workers).wait_for_complete()
        app_.action_tab("curate")
        await pilot.pause()
        app_.action_tab("test")
        await pilot.pause()
        target = app_._test_target  # pyright: ignore[reportPrivateUsage]
        assert target is not None
        reads = list(target["reads"])
        boxes = [cast("Input", b) for b in app_.query("#test-inputs Input")]
        assert len(boxes) == len(reads)  # one input box per declared read
        # prefilled from the fixture: a read with a fixture value isn't blank
        for box, r in zip(boxes, reads, strict=True):
            if target["fixture"].get(r) is not None:
                assert box.value != ""
        app_.action_test_run()
        await cast("Any", app_.workers).wait_for_complete()
        await pilot.pause()
    out = app_._test_output_text  # pyright: ignore[reportPrivateUsage]
    assert "ran ✓" in out
    assert "matches declared writes" in out
    for w in target["writes"]:
        assert w in out  # each declared write shows up in the output


async def test_tui_test_tab_full_gate_passes_for_a_seed() -> None:
    """Journey: the Full gate button re-runs the same static→contract→tests gate
    on the selected pair; a gate-verified seed passes."""
    _ledger.seed_trainset(SEEDS)
    app_ = NodesmithTUI()
    async with app_.run_test() as pilot:
        await pilot.pause()
        await cast("Any", app_.workers).wait_for_complete()
        app_.action_tab("curate")
        await pilot.pause()
        app_.action_tab("test")
        await pilot.pause()
        app_.action_test_gate()
        await cast("Any", app_.workers).wait_for_complete()
        await pilot.pause()
    assert "PASS ✓" in app_._test_output_text  # pyright: ignore[reportPrivateUsage]


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
