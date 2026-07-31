# SPDX-License-Identifier: Apache-2.0
"""``stargraph run`` subcommand -- POC graph runner (FR-8, design §3.10).

Loads an IR YAML, builds an :class:`stargraph.ir.IRDocument`, constructs an
:class:`stargraph.graph.Graph`, and drives a fresh :class:`stargraph.graph.GraphRun`
through :func:`stargraph.graph.loop.execute` to completion. The IR's routing
rules are compiled into a live Fathom adapter via
:func:`stargraph.fathom.build_ir_routing`, so goto/halt rules (including
cyclic routes on mirrored state facts) fire at runtime; when no rule is
live-routable the run keeps the linear next-node fallback. A SQLite
checkpointer is wired (default: ``./.stargraph/run.sqlite``); a JSONL audit
sink is wired only when ``--log-file`` is supplied.

Phase 3 ``--inspect`` mode (design §3.10 table -- ``run`` row, FR-8/9):
when ``--inspect`` is supplied, ``cmd`` skips checkpointer + audit-sink
construction entirely, builds the :class:`Graph`, calls
:meth:`Graph.simulate` with synthetic zero-value fixtures (one entry per
IR node), and prints the per-rule firing trace. No node executes; no
file is written; exit is ``0`` on a clean simulation and non-zero on
any :class:`SimulationError` (e.g. fixture-coverage violation).

Interactive mode (Plan 1):
``--inputs key=value`` seeds typed initial state; live progress is rendered
via :class:`ProgressPrinter`; ``WaitingForInputEvent`` events are resolved
by :class:`HITLHandler` (or fail under ``--non-interactive``); the
end-of-run :class:`SummaryRenderer` prints status + writes artifacts.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import anyio
import typer
import yaml
from fathom.chained_log import load_or_create_key
from rich.console import Console

from stargraph.audit.jsonl import (
    AuditSink,
    ChainedJSONLAuditSink,
    JSONLAuditSink,
    is_chained_log,
)
from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.cli._inputs import parse_inputs, parse_inputs_for_model
from stargraph.cli._progress import ProgressPrinter
from stargraph.cli._prompts import HITLHandler
from stargraph.cli._summary import SummaryRenderer
from stargraph.errors import StargraphError
from stargraph.fathom import build_ir_routing
from stargraph.graph import Graph, GraphRun
from stargraph.ir import IRDocument
from stargraph.ir._ids import new_run_id

# Node-kind resolution moved to stargraph.nodes.registry (the CLI is one
# consumer among several). Private aliases retained: demo servers, serve,
# and the smith gate import them from this module.
from stargraph.nodes.registry import (
    _build_subgraph as _build_subgraph,  # pyright: ignore[reportPrivateUsage]
)
from stargraph.nodes.registry import (
    build_node_registry,
    node_kinds,
)

if TYPE_CHECKING:
    from stargraph.checkpoint.protocol import RunSummary

__all__ = ["cmd", "node_kinds"]

_build_node_registry = build_node_registry


def _configure_lm(
    lm_url: str | None,
    lm_model: str | None,
    lm_key: str,
    lm_timeout: int,
) -> None:
    """Configure dspy.LM if both --lm-url and --lm-model are set.

    Failing-loud if exactly one is set: pairing is mandatory. Skipping the
    call entirely when both are None lets graphs without DSPy nodes run
    without dragging in dspy at all.
    """
    if (lm_url is None) != (lm_model is None):
        raise typer.BadParameter("--lm-url and --lm-model must be specified together (or neither)")
    if lm_url is None:
        return
    import dspy  # pyright: ignore[reportMissingTypeStubs]

    dspy.configure(  # pyright: ignore[reportUnknownMemberType]
        lm=dspy.LM(  # pyright: ignore[reportUnknownMemberType]
            f"openai/{lm_model}",
            api_base=lm_url,
            api_key=lm_key,
            timeout=lm_timeout,
        )
    )


def _build_audit_sink(log_file: Path) -> AuditSink:
    """Choose chained vs legacy sink for ``log_file`` (chain-write, dual-read).

    New/empty logs and existing chained logs get the hash-chained,
    JWS-signed :class:`ChainedJSONLAuditSink` -- the Ed25519 signing key is
    auto-created beside the log as ``<log>.key`` (atomic, 0600) and the
    public half exported as ``<log>.pub.pem`` for ``stargraph verify-audit``.

    An existing non-empty unchained log falls back to the legacy
    :class:`JSONLAuditSink` with a warning: appending chained lines after
    unchained ones would leave a file neither format can verify.
    """
    if log_file.exists() and log_file.stat().st_size > 0 and not is_chained_log(log_file):
        typer.echo(
            f"warning: {log_file} is an existing unchained audit log; "
            "appending in legacy format (move it aside to start a chained log)",
            err=True,
        )
        return JSONLAuditSink(log_file)
    key_path = log_file.with_name(log_file.name + ".key")
    return ChainedJSONLAuditSink(log_file, load_or_create_key(key_path))


async def _drive_interactive(
    run: GraphRun,
    audit_sink: AuditSink | None,
    progress: ProgressPrinter,
    hitl: HITLHandler | None,
    console: Console,
) -> RunSummary:
    """Tee bus events to: audit_sink (jsonl), progress (stdout), hitl (input prompts).

    Returns the :class:`RunSummary` produced by :meth:`GraphRun.start`.
    """
    summary_holder: dict[str, Any] = {}

    async def _reader() -> None:
        with contextlib.suppress(anyio.EndOfStream, anyio.ClosedResourceError):
            while True:
                ev: Any = await run.bus.receive()
                if audit_sink is not None:
                    await audit_sink.write(ev)
                if ev.type == "waiting_for_input":
                    if hitl is None:
                        console.print("[red]✗ run paused for HITL but --non-interactive set[/red]")
                        raise typer.Exit(2)
                    await hitl.handle(ev, run)
                progress.feed(ev)
                if ev.type == "result":
                    progress.finalize(ev.ts)
                    return

    async with anyio.create_task_group() as tg:
        tg.start_soon(_reader)
        try:
            summary_holder["summary"] = await run.start()
        finally:
            await run.bus.aclose()
    return summary_holder["summary"]


def cmd(
    graph: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Path to an IR YAML graph definition.",
        ),
    ],
    log_file: Annotated[
        Path | None,
        typer.Option(
            "--log-file",
            help="Append per-event JSONL records to this path (default: no log).",
        ),
    ] = None,
    checkpoint: Annotated[
        Path | None,
        typer.Option(
            "--checkpoint",
            help="SQLite checkpoint DB path (default: ./.stargraph/run.sqlite).",
        ),
    ] = None,
    inspect: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            "--inspect",
            help=(
                "Print rule-firing trace without executing nodes (FR-8/9). "
                "Disambiguates from the separate `stargraph inspect <ckpt>` "
                "checkpoint inspector; --inspect kept as backward-compat alias."
            ),
        ),
    ] = False,
    inputs: Annotated[
        list[str] | None,
        typer.Option(
            "--inputs",
            "-i",
            help="key=value initial state field (repeatable; key must match IR state_schema)",
        ),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="suppress per-step progress output"),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="print tool result payloads inline"),
    ] = False,
    no_summary: Annotated[
        bool,
        typer.Option("--no-summary", help="skip end-of-run summary block"),
    ] = False,
    summary_json: Annotated[
        bool,
        typer.Option("--summary-json", help="emit summary as JSON instead of text"),
    ] = False,
    non_interactive: Annotated[
        bool,
        typer.Option(
            "--non-interactive",
            help="fail on awaiting-input instead of prompting",
        ),
    ] = False,
    live_broker: Annotated[
        bool,
        typer.Option(
            "--live-broker",
            help=(
                "Wire the lifespan-singleton Nautilus Broker around the run "
                "(reads <stargraph-config>/nautilus.yaml). Soft-fails when the "
                "YAML is absent -- BrokerNode/broker_request demo intents "
                "fall back to offline envelopes."
            ),
        ),
    ] = False,
    lm_url: Annotated[
        str | None,
        typer.Option(
            "--lm-url",
            help="LLM endpoint URL for DSPy nodes (OpenAI-compatible). Pair with --lm-model.",
        ),
    ] = None,
    lm_model: Annotated[
        str | None,
        typer.Option(
            "--lm-model",
            help="LLM model identifier (e.g. gpt-oss:20b). Required if --lm-url is set.",
        ),
    ] = None,
    lm_key: Annotated[
        str,
        typer.Option(
            "--lm-key",
            help="API key for the LLM endpoint. Defaults to 'placeholder' (works for ollama).",
        ),
    ] = "placeholder",
    lm_timeout: Annotated[
        int,
        typer.Option(
            "--lm-timeout",
            help="LLM call timeout in seconds.",
        ),
    ] = 60,
) -> None:
    """Run a Stargraph graph end-to-end (FR-8 POC).

    Loads ``graph`` as IR YAML, validates it, constructs a SQLite-backed
    :class:`GraphRun`, and drives the single-node execution loop to terminal
    state. Exits ``0`` on ``done`` and non-zero on ``failed``.

    With ``--inspect`` the function instead invokes :meth:`Graph.simulate`
    against synthetic zero-value fixtures and prints the rule-firing
    trace. No checkpoint or log file is touched in this mode.
    """
    if quiet and verbose:
        raise typer.BadParameter("--quiet and --verbose are mutually exclusive")

    _configure_lm(lm_url, lm_model, lm_key, lm_timeout)

    ir_dict = yaml.safe_load(graph.read_text(encoding="utf-8"))
    ir = IRDocument.model_validate(ir_dict)

    g = Graph(ir)

    if inspect:
        # ``simulate`` requires one fixture per IR node; synthesize empty
        # dict outputs so the trace is callable on any IR. No tools or
        # nodes execute.
        fixtures: dict[str, object] = {n.id: {} for n in ir.nodes}
        result = asyncio.run(g.simulate(fixtures))
        typer.echo(f"graph_hash={g.graph_hash}")
        typer.echo(f"rule_firings={len(result.rule_firings)}")
        for firing in result.rule_firings:
            matched = ",".join(firing.matched_nodes) or "-"
            actions = ",".join(firing.action_kinds) or "-"
            typer.echo(
                f"  rule={firing.rule_id} fired={firing.fired} "
                f"matched=[{matched}] actions=[{actions}]"
            )
        return

    run_id = new_run_id()

    ckpt_path = checkpoint or Path(".stargraph") / "run.sqlite"
    ckpt_path.parent.mkdir(parents=True, exist_ok=True)
    checkpointer = SQLiteCheckpointer(ckpt_path)

    audit_sink: AuditSink | None = None
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        audit_sink = _build_audit_sink(log_file)

    artifacts_dir = Path(".stargraph") / "runs" / run_id
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    if ir.state_class is not None:
        initial_values = parse_inputs_for_model(inputs or [], g.state_schema)
    else:
        initial_values = parse_inputs(inputs or [], ir.state_schema)
    initial_state = g.state_schema(**initial_values)
    try:
        node_registry = build_node_registry(ir.nodes, ir_dir=graph.parent.resolve())
    except StargraphError as e:
        # Registry failures are graph-authoring mistakes (unknown kind, bad
        # module ref, missing sub-IR) — surface them as parameter errors.
        raise typer.BadParameter(str(e)) from e
    run = GraphRun(
        run_id=run_id,
        graph=g,
        initial_state=initial_state,
        node_registry=node_registry,
        checkpointer=checkpointer,
        fathom=build_ir_routing(ir, g.state_schema),
    )

    console = Console()
    progress = ProgressPrinter(console, quiet=quiet, verbose=verbose)
    hitl: HITLHandler | None = None if non_interactive else HITLHandler(console)

    async def _bootstrap_and_drive() -> RunSummary:
        await checkpointer.bootstrap()
        try:
            if live_broker:
                from stargraph.serve.lifecycle import broker_lifespan

                async with broker_lifespan():
                    return await _drive_interactive(run, audit_sink, progress, hitl, console)
            return await _drive_interactive(run, audit_sink, progress, hitl, console)
        finally:
            await checkpointer.close()
            if audit_sink is not None:
                await audit_sink.close()

    try:
        summary = asyncio.run(_bootstrap_and_drive())
    except KeyboardInterrupt:
        console.print("[yellow]cancelled[/yellow]")
        raise typer.Exit(code=130) from None

    if not no_summary:
        # Reconstruct final state model from the ResultEvent's snapshot.
        final_state_dict = progress.final_state_dict() or {}
        try:
            final_state = g.state_schema(**final_state_dict)
        except Exception:
            # If the schema can't validate (e.g. on failure paths), fall back
            # to the run's initial state so the renderer still has something
            # to dump non-default fields from.
            final_state = initial_state
        renderer = SummaryRenderer(console, json_mode=summary_json, suppress=no_summary)
        renderer.render(
            summary=summary,
            final_state=final_state,
            stats=progress.stats(),
            artifacts_dir=artifacts_dir,
            run_id=run.run_id,
            checkpoint=ckpt_path,
            duration_ms_override=progress.run_duration_ms(),
        )

    # Stable single-line marker, last line of stdout — downstream parsers
    # (test_cli_inspect, test_counterfactual_e2e) split on this.
    typer.echo(f"run_id={run.run_id} status={summary.status}")

    if summary.status != "done":
        raise typer.Exit(code=1)
