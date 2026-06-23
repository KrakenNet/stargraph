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
import json
import os
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar, cast

import click
from rich.console import Group
from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    LoadingIndicator,
    Log,
    Select,
    Sparkline,
    Static,
    TabbedContent,
    TabPane,
)

from stargraph.skills.nodesmith import _curate, _ledger
from stargraph.skills.nodesmith._curate import short_id as _short
from stargraph.skills.nodesmith.gate import (
    NODE_FILE,
    TEST_FILE,
    all_passed,
    run_node,
    verify_sources,
)
from stargraph.skills.nodesmith.seeds import SEEDS

if TYPE_CHECKING:
    from textual.binding import BindingType

_CTX = SimpleNamespace(run_id="nodesmith-tui")


def _opt_float(field: str, raw: str) -> float | None:
    """Parse an optional float knob; blank → None (use the model default)."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{field} must be a number, got {raw!r}") from None


def _opt_int(field: str, raw: str) -> int | None:
    """Parse an optional whole-number knob; blank → None (use the model default)."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{field} must be a whole number, got {raw!r}") from None


def _fetch_ollama_models(url: str, timeout_s: float = 2.0) -> list[str]:
    """GET ``{url}/api/tags`` and return installed model names (sorted).

    Returns ``[]`` on any error — server down, bad payload, timeout — so the
    caller falls back to the free-text model field instead of crashing.
    """
    from urllib.error import URLError
    from urllib.request import urlopen

    endpoint = url.rstrip("/") + "/api/tags"
    try:
        with urlopen(endpoint, timeout=timeout_s) as resp:
            data: Any = json.loads(resp.read().decode("utf-8"))
        raw_models: Any = data["models"]
    except (URLError, OSError, ValueError, TypeError, KeyError, TimeoutError):
        return []
    names: list[str] = []
    for entry in raw_models:
        try:
            name: Any = entry["name"]
        except (TypeError, KeyError, IndexError):
            continue
        if isinstance(name, str) and name:
            names.append(name)
    return sorted(names)


def _to_text(value: Any) -> str:
    """Render a fixture value for an input box: strings as-is, else JSON."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value)


def _parse_value(raw: str) -> Any:
    """Parse an input box back into a value: JSON if it parses (so 5 → int,
    ``true`` → bool, ``{...}`` → dict), otherwise the raw string; blank → None."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _format_run_result(result: dict[str, Any], inputs: dict[str, Any]) -> str:
    """Human-readable view of a gate.run_node result for the Test output pane."""
    if not result.get("ok"):
        return f"[red]run failed[/]\n{result.get('msg', 'unknown error')}"
    lines = ["[green]ran ✓[/]", "", "inputs:"]
    lines += [f"  {k} = {v!r}" for k, v in inputs.items()] or ["  (none)"]
    lines += ["", "output:"]
    output = result.get("output", {})
    lines += [f"  {k} = {v!r}" for k, v in output.items()] or ["  (empty)"]
    missing = result.get("missing_writes", [])
    undeclared = result.get("undeclared", [])
    if missing:
        lines.append(f"\n[yellow]declared writes not produced:[/] {missing}")
    if undeclared:
        lines.append(f"[yellow]wrote undeclared keys:[/] {undeclared}")
    if not missing and not undeclared:
        lines.append("\n[green]output matches declared writes.[/]")
    return "\n".join(lines)


def _format_gate_result(passed: bool, results: list[Any]) -> str:
    """Human-readable view of a verify_sources result for the Test output pane."""
    head = "[green]full gate: PASS ✓[/]" if passed else "[red]full gate: FAIL ✗[/]"
    lines = [head, ""]
    for r in results:
        mark = "✓" if getattr(r, "passed", False) else "✗"
        lines.append(f"  {mark} {getattr(r, 'kind', '?')} ({getattr(r, 'duration_ms', 0)}ms)")
        for f in getattr(r, "findings", []) or []:
            lines.append(f"      - {str(f.get('msg', ''))[:300]}")
    return "\n".join(lines)


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


class ClarifyModal(ModalScreen[str]):
    """A blocking clarifying question, surfaced when the generator is unsure.

    Shows the model's question with its concrete answers as buttons (FR: multiple
    choice when possible) plus a free-text box as the fallback. Dismisses with the
    chosen / typed answer — or an empty string if the user skips, which the caller
    treats as "proceed without clarifying".
    """

    CSS = """
    ClarifyModal { align: center middle; }
    #clarify-box {
        width: 70%; height: auto; max-height: 80%;
        padding: 1 2; border: round $accent; background: $surface;
    }
    #clarify-q { padding-bottom: 1; }
    #clarify-box Button { width: 1fr; margin-bottom: 1; }
    """

    def __init__(self, question: str, options: list[str]) -> None:
        super().__init__()
        self._question = question
        self._options = options

    def compose(self) -> ComposeResult:
        with Vertical(id="clarify-box"):
            yield Static(f"[bold]clarify[/]\n{self._question}", id="clarify-q")
            for i, opt in enumerate(self._options):
                yield Button(opt, id=f"clarify-opt-{i}")
            yield Input(placeholder="…or type an answer, then Enter", id="clarify-text")
            yield Button("Skip", id="clarify-skip")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        bid = event.button.id or ""
        if bid.startswith("clarify-opt-"):
            self.dismiss(self._options[int(bid.rsplit("-", 1)[1])])
        elif bid == "clarify-skip":
            self.dismiss("")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value.strip())


class NodesmithTUI(App[None]):
    CSS = """
    #rows { width: 50%; }
    #detail { width: 1fr; padding: 0 1; }
    #gen-settings, #test-controls { width: 40%; padding: 0 1; }
    #gen-output, #test-output-pane { width: 1fr; padding: 0 1; }
    #gen-detail, #test-output { padding: 0 1; }
    #gen-log { height: 8; border: round $accent; }
    #gen-spinner { display: none; height: 1; }
    #model-refresh { width: 5; }
    .field { height: 3; }
    Input { width: 1fr; }
    Select { width: 1fr; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        ("1", "tab('generate')", "Generate"),
        ("2", "tab('curate')", "Curate"),
        ("3", "tab('test')", "Test"),
        ("4", "tab('doctor')", "Doctor"),
        ("5", "tab('stats')", "Stats"),
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
        self._test_target: dict[str, Any] | None = None
        # Test-tab input state: which node is currently shown (so a re-entered
        # tab doesn't rebuild and clobber edits), a monotonic sequence so each
        # rebuild's input IDs are unique, and field → input-widget-id.
        self._test_loaded_id = "__unset__"
        self._test_seq = 0
        self._test_input_ids: dict[str, str] = {}
        # Last text rendered into the doctor/stats/test panes — a typed seam
        # tests assert on without reaching into Textual widget internals.
        self._doctor_text = ""
        self._stats_text = ""
        self._test_output_text = ""

    # --- layout ------------------------------------------------------------ #
    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="generate"):
            with TabPane("Generate", id="generate"), Horizontal():
                with Vertical(id="gen-settings"):
                    yield Input(placeholder="brief: what the node should do…", id="brief")
                    with Horizontal(classes="field"):
                        yield Select([], prompt="model", id="model-select", allow_blank=True)
                        yield Button("↻", id="model-refresh")
                    yield Input(
                        value=os.environ.get("LLM_OLLAMA_MODEL", ""),
                        placeholder="or type a model",
                        id="model",
                    )
                    with Horizontal(classes="field"):
                        yield Input(placeholder="temperature (e.g. 0.2)", id="temperature")
                        yield Input(placeholder="context length (e.g. 8192)", id="num-ctx")
                        yield Input(placeholder="max tokens (optional)", id="max-tokens")
                    with Horizontal(classes="field"):
                        yield Button("Generate", id="gen-btn", variant="primary")
                        yield Button("Accept", id="gen-accept")
                        yield Button("Reject", id="gen-reject")
                        yield Button("Edit→gold", id="gen-edit")
                with Vertical(id="gen-output"):
                    yield LoadingIndicator(id="gen-spinner")
                    yield Log(id="gen-log")
                    with VerticalScroll():
                        yield Static(id="gen-detail")
            with TabPane("Curate", id="curate"), Horizontal():
                yield DataTable(id="rows")
                with VerticalScroll():
                    yield Static(id="detail")
            with TabPane("Test", id="test"), Horizontal():
                with Vertical(id="test-controls"):
                    yield Static(id="test-target-info")
                    with VerticalScroll(id="test-inputs"):
                        yield Static("(no node selected)", id="test-inputs-empty")
                    with Horizontal(classes="field"):
                        yield Button("Run node", id="test-run", variant="primary")
                        yield Button("Full gate", id="test-gate")
                with Vertical(id="test-output-pane"), VerticalScroll():
                    yield Static(id="test-output")
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
        self.query_one("#gen-spinner", LoadingIndicator).display = False
        self._reload()
        self.action_refresh_models()

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
        elif active == "test":
            self._load_test_target()

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
        model = self._current_model()
        if not model:
            self.notify("model is required (pick one or type it)", severity="error")
            return
        try:
            temperature = _opt_float("temperature", self.query_one("#temperature", Input).value)
            num_ctx = _opt_int("context length", self.query_one("#num-ctx", Input).value)
            max_tokens = _opt_int("max tokens", self.query_one("#max-tokens", Input).value)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        log = self.query_one("#gen-log", Log)
        log.clear()
        log.write_line(f"generating: {brief}")
        self.run_worker(
            self._generate(brief, model, temperature, num_ctx, max_tokens),
            exclusive=True,
            group="gen",
        )

    async def _generate(
        self,
        brief: str,
        model: str,
        temperature: float | None,
        num_ctx: int | None,
        max_tokens: int | None,
    ) -> None:
        from stargraph.skills.nodesmith.program import make_lm

        log = self.query_one("#gen-log", Log)
        spinner = self.query_one("#gen-spinner", LoadingIndicator)
        spinner.display = True
        try:
            # The model call is synchronous and network-bound, so the whole
            # generate→gate→repair loop runs in a worker THREAD; that keeps the
            # event loop free (the spinner animates, the UI stays responsive)
            # and lets the loop emit live phase lines via call_from_thread.
            lm = make_lm(model, temperature=temperature, num_ctx=num_ctx, max_tokens=max_tokens)
            # Pre-flight: let the generator ask a clarifying question on an
            # ambiguous brief before burning attempts on a guess.
            brief = await self._maybe_clarify(brief, [], lm)
            out = await asyncio.to_thread(self._build_sync, brief, model, lm)
            # Stuck after the repair loop? Offer one clarification (fed the failed
            # findings) and retry once with the answer folded into the brief.
            if not all_passed(out.get("verifier_results", [])):
                findings = [
                    f for r in out.get("verifier_results", []) if not r.passed for f in r.findings
                ]
                clarified = await self._maybe_clarify(brief, findings, lm)
                if clarified != brief:
                    brief = clarified
                    self._log_gen("retrying with your clarification…")
                    out = await asyncio.to_thread(self._build_sync, brief, model, lm)
        except Exception as exc:
            log.write_line(f"error: {type(exc).__name__}: {exc}")
            self.notify("generation failed (see log)", severity="error")
            return
        finally:
            spinner.display = False

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

    def _build_sync(self, brief: str, model: str, lm: Any) -> dict[str, Any]:
        """Run the generate→gate→repair loop off the event loop (worker thread).

        DSPy forbids ``dspy.configure`` outside the task that first configured
        it, so the LM is scoped with ``dspy.context`` inside this thread's own
        loop — which also makes the cross-task rule moot.
        """
        import asyncio as _aio

        import dspy  # pyright: ignore[reportMissingTypeStubs]

        from stargraph.skills.nodesmith.nodes.build import Build
        from stargraph.skills.nodesmith.nodes.recall import Recall
        from stargraph.skills.nodesmith.state import State

        def emit(msg: str) -> None:
            self.call_from_thread(self._log_gen, msg)

        async def run() -> dict[str, Any]:
            state = State(brief=brief, model_id=model)
            node = Build(on_progress=emit)
            with dspy.context(lm=lm):  # pyright: ignore[reportUnknownMemberType]
                # Recall (RAG + model-decided web research) calls the LM, so it
                # runs inside the scoped context, not before it.
                emit("recalling lessons + RAG context…")
                recalled = await Recall().execute(state, _CTX)  # pyright: ignore[reportArgumentType]
                emit(
                    f"grounding: {len(recalled.get('recalled_lessons', []))} lessons, "
                    f"{len(str(recalled.get('recalled_context', '')))} chars context"
                )
                state = state.model_copy(update=recalled)
                return await node.execute(state, _CTX)  # pyright: ignore[reportArgumentType]

        return _aio.run(run())

    def _log_gen(self, msg: str) -> None:
        self.query_one("#gen-log", Log).write_line(msg)

    async def _maybe_clarify(self, brief: str, findings: list[dict[str, Any]], lm: Any) -> str:
        """Consult the model; if it wants clarification, pop the modal and fold the
        answer into the brief. Returns the (possibly augmented) brief unchanged when
        no question is needed or the user skips."""
        clar = await asyncio.to_thread(self._clarify_sync, brief, findings, lm)
        if not clar.get("needs"):
            return brief
        question = str(clar.get("question", ""))
        options = [str(o) for o in clar.get("options", [])]
        spinner = self.query_one("#gen-spinner", LoadingIndicator)
        spinner.display = False
        self._log_gen(f"❓ {question}")
        answer = await self.push_screen_wait(ClarifyModal(question, options))
        spinner.display = True
        if not answer:
            return brief
        self._log_gen(f"↳ {answer}")
        return f"{brief}\n\n[clarification] {question}\n[answer] {answer}"

    def _clarify_sync(self, brief: str, findings: list[dict[str, Any]], lm: Any) -> dict[str, Any]:
        """Run the (synchronous) clarify predictor off the event loop, scoping the
        LM with ``dspy.context`` — same task-ownership reason as ``_build_sync``."""
        import dspy  # pyright: ignore[reportMissingTypeStubs]

        from stargraph.skills.nodesmith.program import clarify

        with dspy.context(lm=lm):  # pyright: ignore[reportUnknownMemberType]
            return clarify(brief, findings)

    # --- model picker ------------------------------------------------------ #
    def _current_model(self) -> str:
        """The dropdown selection if one is chosen, else the free-text field."""
        select = cast("Select[str]", self.query_one("#model-select", Select))
        value = select.value
        if isinstance(value, str) and value:
            return value.strip()
        return self.query_one("#model", Input).value.strip()

    def action_refresh_models(self) -> None:
        self.run_worker(self._load_models(), exclusive=True, group="models")

    async def _load_models(self) -> None:
        from stargraph.skills.nodesmith.program import DEFAULT_OLLAMA_URL

        models = await asyncio.to_thread(_fetch_ollama_models, DEFAULT_OLLAMA_URL)
        select = cast("Select[str]", self.query_one("#model-select", Select))
        select.set_options((m, m) for m in models)
        if not models:
            self.notify("no Ollama models found — type the model name", severity="warning")

    # --- test the generated node ------------------------------------------- #
    def _load_test_target(self) -> None:
        """Point the Test tab at the current node (last generated, else the
        highlighted Curate row) and render one input box per declared read.

        Guarded by the loaded node id so the double tab-activation fire (and a
        plain re-entry) neither duplicate input IDs nor clobber edited values.
        """
        target = self._last_generated or self._curate_row()
        target_id = str(target.get("id", "")) if target else ""
        if target_id == self._test_loaded_id:
            return
        self._test_loaded_id = target_id
        self._test_target = target
        info = self.query_one("#test-target-info", Static)
        box = self.query_one("#test-inputs", VerticalScroll)
        box.remove_children()
        self._test_input_ids = {}
        if target is None:
            info.update("[dim]no node yet — generate one or highlight a row in Curate.[/]")
            box.mount(Static("(no node selected)"))
            return
        reads = list(target.get("reads", []))
        writes = list(target.get("writes", []))
        info.update(
            f"[bold]{target.get('class_name', '?')}[/]\nreads={reads}\nwrites={writes}\n"
            "set inputs below, then Run node (or Full gate)."
        )
        fixture = dict(target.get("fixture", {}))
        if not reads:
            box.mount(Static("(node declares no reads)"))
            return
        self._test_seq += 1
        widgets: list[Input] = []
        for f in reads:
            wid = f"in-{self._test_seq}-{f}"
            self._test_input_ids[f] = wid
            widgets.append(Input(value=_to_text(fixture.get(f)), placeholder=f, id=wid))
        box.mount(*widgets)

    def action_test_run(self) -> None:
        target = self._test_target
        if target is None:
            self.notify("no node selected", severity="error")
            return
        reads = list(target.get("reads", []))
        writes = list(target.get("writes", []))
        node_source = str(target.get("node_source", ""))
        inputs: dict[str, Any] = {}
        for field in reads:
            wid = self._test_input_ids.get(field)
            if wid:
                inputs[field] = _parse_value(self.query_one(f"#{wid}", Input).value)
        self.run_worker(
            self._test_run(node_source, inputs, reads, writes), exclusive=True, group="test"
        )

    async def _test_run(
        self, node_source: str, inputs: dict[str, Any], reads: list[str], writes: list[str]
    ) -> None:
        result = await asyncio.to_thread(
            run_node, node_source, inputs=inputs, reads=reads, writes=writes
        )
        self._test_output_text = _format_run_result(result, inputs)
        self.query_one("#test-output", Static).update(self._test_output_text)

    def action_test_gate(self) -> None:
        target = self._test_target
        if target is None:
            self.notify("no node selected", severity="error")
            return
        self.run_worker(self._test_gate(target), exclusive=True, group="test")

    async def _test_gate(self, target: dict[str, Any]) -> None:
        passed, results = await asyncio.to_thread(
            verify_sources,
            str(target.get("node_source", "")),
            str(target.get("test_source", "")),
            reads=list(target.get("reads", [])),
            writes=list(target.get("writes", [])),
            fixture=dict(target.get("fixture", {})),
        )
        self._test_output_text = _format_gate_result(passed, results)
        self.query_one("#test-output", Static).update(self._test_output_text)

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
            "model-refresh": self.action_refresh_models,
            "test-run": self.action_test_run,
            "test-gate": self.action_test_gate,
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
