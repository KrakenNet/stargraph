# SPDX-License-Identifier: Apache-2.0
"""``nodesmith`` CLI — run the generator, curate the trainset, label results.

Subcommands::

    nodesmith doctor                 # prove the run/test/verify/write toolchain
    nodesmith seed                   # load the hand-verified seed pairs
    nodesmith make "<brief>" --lm-url ... --lm-model ...   # generate + review
    nodesmith trainset list|show|stats|label|edit|rm       # curate

Labeling is the training-set generator: every reviewed pair carries a human
``verdict`` (accept/reject) on top of the mechanical gate result. ``edit`` is
the edit-to-gold path — fix a node, re-gate it, store the fixed version as a
positive example. The TUI (``nodesmith tui``) is the same thing, interactive.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import click
import typer
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from stargraph.skills.nodesmith import _curate, _ledger
from stargraph.skills.nodesmith._curate import short_id as _short
from stargraph.skills.nodesmith.gate import NODE_FILE, TEST_FILE
from stargraph.skills.nodesmith.seeds import SEEDS

app = typer.Typer(no_args_is_help=True, help="Build Stargraph nodes; curate the trainset.")
trainset_app = typer.Typer(no_args_is_help=True, help="Browse + curate the (spec → node) trainset.")
app.add_typer(trainset_app, name="trainset")
console = Console()


def _verdict_style(verdict: Any) -> str:
    return {"accept": "[green]accept[/]", "reject": "[red]reject[/]"}.get(verdict, "[dim]—[/]")


# --------------------------------------------------------------------------- #
# doctor + seed
# --------------------------------------------------------------------------- #
@app.command()
def doctor() -> None:
    """Verify nodesmith can generate files, run code, run tests, and verify."""
    from stargraph.skills.nodesmith._doctor import healthy, run_doctor

    checks = run_doctor()
    table = Table("check", "status", "detail", title="nodesmith doctor")
    for c in checks:
        table.add_row(c.name, "[green]ok[/]" if c.ok else "[red]FAIL[/]", c.detail)
    console.print(table)
    if healthy(checks):
        console.print("[green]healthy[/] — toolchain functional.")
    else:
        console.print("[red]unhealthy[/] — a hard check failed (see above).")
        raise typer.Exit(1)


@app.command()
def seed() -> None:
    """Load the hand-authored, gate-verified seed pairs into the trainset."""
    added = _ledger.seed_trainset(SEEDS)
    console.print(f"seeded [bold]{added}[/] new pair(s) ({len(SEEDS)} available).")


# --------------------------------------------------------------------------- #
# make — generate from a brief, then review
# --------------------------------------------------------------------------- #
@app.command()
def make(
    brief: str = typer.Argument(..., help="what the node should do"),
    lm_url: str = typer.Option(..., "--lm-url", help="LLM endpoint (e.g. Ollama)"),
    lm_model: str = typer.Option(..., "--lm-model", help="model id, e.g. laguna-xs"),
    lm_key: str = typer.Option("placeholder", "--lm-key"),
    review: bool = typer.Option(True, help="prompt for an accept/reject verdict after building"),
) -> None:
    """Generate a node from BRIEF (bounded repair loop), gate it, then review."""
    from stargraph.skills.nodesmith.nodes.build import Build
    from stargraph.skills.nodesmith.nodes.recall import Recall
    from stargraph.skills.nodesmith.program import configure_lm
    from stargraph.skills.nodesmith.state import State

    configure_lm(lm_url, lm_model, lm_key)
    ctx = SimpleNamespace(run_id="nodesmith-make")

    async def _run() -> dict[str, Any]:
        state = State(brief=brief, model_id=lm_model)
        state = state.model_copy(update=await Recall().execute(state, ctx))  # pyright: ignore[reportArgumentType]
        return await Build().execute(state, ctx)  # pyright: ignore[reportArgumentType]

    out = asyncio.run(_run())
    files = out.get("artifact_files", {})
    node_src, test_src = files.get(NODE_FILE, ""), files.get(TEST_FILE, "")
    _print_pair(out.get("class_name", "?"), node_src, test_src)
    status = "[green]gate passed[/]" if out.get("succeeded") else "[red]gate failed[/]"
    console.print(f"{status} after {out.get('fix_attempts')} attempt(s).")

    if not out.get("succeeded"):
        console.print("not recorded (only gate-passing builds are stored).")
        raise typer.Exit(1)

    verdict = None
    if review:
        meets = typer.confirm("Does this meet your needs?", default=True)
        verdict = "accept" if meets else "reject"
    row = _ledger.append_trainset(
        {
            "brief": brief,
            "class_name": out.get("class_name", ""),
            "node_name": out.get("class_name", ""),
            "reads": out.get("reads", []),
            "writes": out.get("writes", []),
            "fixture": out.get("fixture", {}),
            "node_source": node_src,
            "test_source": test_src,
            "model_id": lm_model,
            "attempts": out.get("fix_attempts", 1),
            "passed": True,
            "verdict": verdict,
        }
    )
    console.print(f"recorded [bold]{_short(row)}[/] (verdict: {verdict or 'unreviewed'}).")
    if verdict == "reject":
        console.print(
            f"tip: `nodesmith trainset edit {_short(row)}` to fix it into a gold example."
        )


# --------------------------------------------------------------------------- #
# trainset curation
# --------------------------------------------------------------------------- #
@trainset_app.command("list")
def trainset_list(
    unreviewed: bool = typer.Option(False, "--unreviewed", help="only rows with no verdict"),
) -> None:
    """List trainset rows."""
    rows = _ledger.load_trainset()
    if unreviewed:
        rows = [r for r in rows if r.get("verdict") not in ("accept", "reject")]
    if not rows:
        console.print("[dim]empty — try `nodesmith seed`.[/]")
        return
    table = Table("id", "source", "verdict", "class", "brief")
    for r in rows:
        brief = str(r.get("brief", ""))
        table.add_row(
            _short(r),
            str(r.get("source", "")),
            _verdict_style(r.get("verdict")),
            str(r.get("class_name", "")),
            brief if len(brief) <= 56 else brief[:53] + "...",
        )
    console.print(table)


@trainset_app.command("show")
def trainset_show(ref: str = typer.Argument(..., help="row id or unique prefix")) -> None:
    """Show one row's brief, node source, test, and verdict."""
    row = _ledger.find_trainset(ref)
    if row is None:
        console.print(f"[red]no row matching '{ref}'[/]")
        raise typer.Exit(1)
    console.print(
        f"[bold]{_short(row)}[/]  source={row.get('source')}  verdict={row.get('verdict')}"
    )
    console.print(f"brief: {row.get('brief')}")
    console.print(
        f"reads={row.get('reads')}  writes={row.get('writes')}  fixture={row.get('fixture')}"
    )
    _print_pair(
        str(row.get("class_name", "?")), row.get("node_source", ""), row.get("test_source", "")
    )


@trainset_app.command("stats")
def trainset_stats() -> None:
    """Counts by verdict + source, plus the generator drift signal."""
    s = _ledger.trainset_stats()
    table = Table("metric", "value")
    for k, v in s.items():
        table.add_row(k, str(v))
    table.add_row("drift (first-try pass rate)", f"{_ledger.drift_rate():.0%}")
    console.print(table)


@trainset_app.command("label")
def trainset_label(
    ref: str = typer.Argument(...),
    accept: bool = typer.Option(False, "--accept", help="meets your needs"),
    reject: bool = typer.Option(False, "--reject", help="does not meet your needs"),
    reason: str = typer.Option("", "--reason", help="why (stored on the row)"),
) -> None:
    """Attach a human verdict to a row (both verdicts feed the trainset)."""
    if accept == reject:
        console.print("[red]pass exactly one of --accept / --reject[/]")
        raise typer.Exit(1)
    verdict = "accept" if accept else "reject"
    row = _ledger.update_trainset(ref, verdict=verdict, reason=reason)
    if row is None:
        console.print(f"[red]no row matching '{ref}'[/]")
        raise typer.Exit(1)
    console.print(f"{_short(row)} → {_verdict_style(verdict)}")


@trainset_app.command("edit")
def trainset_edit(ref: str = typer.Argument(...)) -> None:
    """Edit-to-gold: open the node + test in $EDITOR, re-gate, store the fix."""
    row = _ledger.find_trainset(ref)
    if row is None:
        console.print(f"[red]no row matching '{ref}'[/]")
        raise typer.Exit(1)

    buffer = _curate.build_edit_buffer(row)
    edited = click.edit(buffer, extension=".py")
    if not edited or edited == buffer:
        console.print("[dim]no changes — nothing stored.[/]")
        return
    ok, msg = _curate.apply_edit(ref, edited)
    if not ok:
        console.print(f"[red]{msg}[/] — not stored, re-run `edit` to try again.")
        raise typer.Exit(1)
    console.print(f"[green]gold[/] — {msg}.")


@trainset_app.command("rm")
def trainset_rm(ref: str = typer.Argument(...)) -> None:
    """Delete a row."""
    if _ledger.delete_trainset(ref):
        console.print(f"deleted {ref}")
    else:
        console.print(f"[red]no row matching '{ref}'[/]")
        raise typer.Exit(1)


@app.command()
def tui() -> None:
    """Launch the interactive Textual console (Generate / Curate / Doctor / Stats)."""
    try:
        from stargraph.skills.nodesmith.tui import run_tui
    except ModuleNotFoundError:
        console.print(
            "[red]textual not installed[/] — `uv sync --extra nodesmith` or `pip install textual`."
        )
        raise typer.Exit(1) from None
    run_tui()


def _print_pair(class_name: str, node_src: str, test_src: str) -> None:
    console.rule(f"[bold]{class_name}[/] — node.py")
    console.print(Syntax(node_src or "(empty)", "python", theme="ansi_dark"))
    console.rule("test_node.py")
    console.print(Syntax(test_src or "(empty)", "python", theme="ansi_dark"))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
