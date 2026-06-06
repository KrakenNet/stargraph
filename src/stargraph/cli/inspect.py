# SPDX-License-Identifier: Apache-2.0
"""``stargraph inspect <run_id>`` -- read-only run inspector (FR-26, FR-27, AC-9.1, AC-9.2).

Three views per design §3.1 (``inspect.py`` row):

* **Timeline** (default) -- one line per checkpointed step with
  ``(step, transition_type, node_id, tool_calls, rule_firings)``
  derived from the Checkpointer + the JSONL audit log filtered by
  ``run_id``. Driven by :func:`stargraph.serve.inspect.build_timeline`.

* **State at step** (``--step N``) -- prints the IR-canonical state
  dict at step ``N`` as one pretty-printed JSON document. Driven by
  :func:`stargraph.serve.inspect.state_at_step`.

* **Fact diff** (``--diff N M``) -- prints CLIPS facts added /
  removed between steps ``N`` and ``M`` from the Checkpointer's
  ``clips_facts`` rows. Driven by :func:`stargraph.serve.inspect.fact_diff`.

* **Audit-log streaming** (``--log-file <jsonl>`` without ``--db``)
  -- Phase-1 legacy mode that streams a JSONL audit log filtered by
  ``run_id`` and prints one record per line. Retained as a
  backwards-compatible mode for operators who have a JSONL log but
  no Checkpointer DB.

Read-only invariant (FR-26): no view writes to the Checkpointer DB
or the JSONL audit log. ``inspect`` only opens these for read.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path  # noqa: TC003 -- runtime use by typer.Annotated
from typing import Annotated, Any, cast

import orjson
import typer

from stargraph.audit.jsonl import unwrap_audit_record
from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.serve import inspect as inspect_body

__all__ = ["cmd"]


def _stream_audit_log(log_file: Path, run_id: str) -> None:
    """Phase-1 audit-log streaming mode -- one record per line, filtered.

    Tolerates bare-event lines (Phase 1 unsigned writer), signed
    envelopes ``{"event": ..., "sig": "<hex>"}`` (Phase 2), and chained
    lines ``{"record": ..., "jws": ...}`` (chained sink).
    """
    matched = 0
    with log_file.open("rb") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            record: Any = orjson.loads(line)
            if not isinstance(record, dict):
                continue
            record_dict = cast("dict[str, Any]", record)
            inner = unwrap_audit_record(record_dict)
            if not isinstance(inner, dict):
                continue
            event_dict = cast("dict[str, Any]", inner)
            if event_dict.get("run_id") != run_id:
                continue
            typer.echo(orjson.dumps(event_dict).decode("utf-8"))
            matched += 1
    if matched == 0:
        # Force-loud (FR-6): an empty filter result is almost always an
        # operator typo (wrong run_id). Surface it as a non-zero exit so
        # shell pipelines can branch on it.
        typer.echo(f"no events matched run_id={run_id!r}", err=True)
        raise typer.Exit(code=1)


async def _run_timeline(db: Path, run_id: str, jsonl_path: Path | None) -> None:
    """Drive the timeline view -- read-only Checkpointer + JSONL walks.

    The JSONL audit log is walked once per invocation (see
    :func:`stargraph.serve.inspect._read_run_events`); the
    ``run_event_offsets`` index from :class:`RunHistory` (design §6.5)
    is reserved for a future ``(first, last)`` offset shape that lets
    the timeline seek the per-step slice rather than full-scan.

    Closes the checkpointer in a ``finally`` so the aiosqlite worker
    thread is shut down before :func:`asyncio.run` tears the loop down
    (otherwise pytest reports
    ``PytestUnhandledThreadExceptionWarning: Event loop is closed`` from
    the leaked ``_connection_worker_thread``).
    """
    cp = SQLiteCheckpointer(db)
    await cp.bootstrap()
    try:
        rows = await inspect_body.build_timeline(cp, run_id, history=None, jsonl_path=jsonl_path)
        typer.echo(inspect_body.format_timeline(rows))
    finally:
        await cp.close()


async def _run_state_at_step(db: Path, run_id: str, step: int) -> None:
    """Drive the state-at-step view -- read-only Checkpointer call."""
    cp = SQLiteCheckpointer(db)
    await cp.bootstrap()
    try:
        state = await inspect_body.state_at_step(cp, run_id, step)
    finally:
        await cp.close()
    if state is None:
        typer.echo(f"no checkpoint at run_id={run_id!r} step={step}", err=True)
        raise typer.Exit(code=1)
    typer.echo(json.dumps(state, sort_keys=True, indent=2))


async def _run_fact_diff(db: Path, run_id: str, step_a: int, step_b: int) -> None:
    """Drive the fact-diff view -- read-only Checkpointer call."""
    cp = SQLiteCheckpointer(db)
    await cp.bootstrap()
    try:
        delta = await inspect_body.fact_diff(cp, run_id, step_a, step_b)
    finally:
        await cp.close()
    if delta is None:
        typer.echo(
            f"missing checkpoint at run_id={run_id!r} step in ({step_a},{step_b})",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(json.dumps(delta, sort_keys=True, indent=2))


def cmd(
    run_id: Annotated[
        str,
        typer.Argument(help="Run id to inspect (matches event.run_id and checkpoint.run_id)."),
    ],
    db: Annotated[
        Path | None,
        typer.Option(
            "--db",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help=(
                "SQLite Checkpointer DB. Required for the timeline, "
                "state-at-step, and fact-diff views."
            ),
        ),
    ] = None,
    log_file: Annotated[
        Path | None,
        typer.Option(
            "--log-file",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help=(
                "JSONL audit log written by JSONLAuditSink. Combined with "
                "--db, used to enrich the timeline view with tool-call + "
                "rule-firing summaries. Without --db, streams events for "
                "run_id one record per line (Phase-1 legacy mode)."
            ),
        ),
    ] = None,
    step: Annotated[
        int | None,
        typer.Option(
            "--step",
            min=0,
            help=(
                "State-at-step view: print the IR-canonical state dict at step N. Requires --db."
            ),
        ),
    ] = None,
    diff: Annotated[
        tuple[int, int] | None,
        typer.Option(
            "--diff",
            help=(
                "Fact-diff view: print CLIPS facts added/removed between "
                "step N and step M. Requires --db."
            ),
        ),
    ] = None,
) -> None:
    """Inspect a run: timeline | state-at-step | fact-diff (read-only).

    Three usage shapes:

    * ``stargraph inspect <run_id> --db <db>`` -- timeline view (default).
    * ``stargraph inspect <run_id> --db <db> --step N`` -- state at step N.
    * ``stargraph inspect <run_id> --db <db> --diff N M`` -- fact delta.

    Plus the Phase-1 legacy mode:

    * ``stargraph inspect <run_id> --log-file <jsonl>`` -- stream events.
    """
    # Mode selection: --diff > --step > timeline. Each requires --db.
    if diff is not None:
        if db is None:
            typer.echo("--diff requires --db", err=True)
            raise typer.Exit(code=2)
        asyncio.run(_run_fact_diff(db, run_id, diff[0], diff[1]))
        return
    if step is not None:
        if db is None:
            typer.echo("--step requires --db", err=True)
            raise typer.Exit(code=2)
        asyncio.run(_run_state_at_step(db, run_id, step))
        return
    if db is not None:
        asyncio.run(_run_timeline(db, run_id, log_file))
        return
    if log_file is not None:
        # Phase-1 legacy mode: stream the JSONL audit log filtered by run_id.
        _stream_audit_log(log_file, run_id)
        return
    typer.echo("at least one of --db or --log-file is required", err=True)
    raise typer.Exit(code=2)
