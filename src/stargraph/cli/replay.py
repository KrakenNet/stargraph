# SPDX-License-Identifier: Apache-2.0
"""``stargraph replay <run_id>`` -- counterfactual replay + diff (FR-26, AC-9.3).

Per design §3.1 (``replay.py`` row), the CLI is a thin wrapper over
:meth:`stargraph.GraphRun.counterfactual` + :func:`stargraph.replay.compare`.
The shape:

    stargraph replay <run_id> --db <path> [--mutation @file.json]
                  [--from-step N] [--diff]

* ``--mutation @file.json`` -- loads a
  :class:`stargraph.replay.CounterfactualMutation` from a JSON file; the
  engine forks a cf-run with the mutation overlay applied at the cf
  step. With no mutation, an empty no-op mutation is used (still
  produces a cf-derived ``graph_hash`` per design §3.8.3).

* ``--from-step N`` -- explicit fork step. Defaults to step 0 (the
  earliest checkpoint) when omitted; callers who care about the
  divergence point set this explicitly.

* ``--diff`` -- after the cf-run is forked, render
  :func:`stargraph.replay.compare` ``RunDiff`` as a JSON document.
  Without ``--diff``, only the cf-run-id is printed so operators can
  pipe it into a follow-up ``stargraph inspect``.

The cf-run id is minted by :meth:`GraphRun.counterfactual` (a fresh
``cf-<uuid>``); the original run's checkpoint rows are
byte-identical post-fork (Temporal "cannot change the past"
invariant).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path  # noqa: TC003 -- runtime use by typer.Annotated
from typing import Annotated, Any, cast

import typer

from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.graph.run import GraphRun
from stargraph.ir import IRBase
from stargraph.ir import dumps as ir_dumps
from stargraph.replay import CounterfactualMutation
from stargraph.replay.compare import compare
from stargraph.replay.history import RunHistory

__all__ = ["cmd"]


def _load_mutation(path: Path | None) -> CounterfactualMutation:
    """Load a :class:`CounterfactualMutation` from JSON or return an empty one."""
    if path is None:
        return CounterfactualMutation()
    payload = cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))
    return CounterfactualMutation.model_validate(payload)


async def _drive_replay(
    db: Path,
    parent_run_id: str,
    mutation: CounterfactualMutation,
    from_step: int,
    *,
    show_diff: bool,
) -> None:
    """Fork a cf-run from ``parent_run_id`` at ``from_step`` and optionally diff."""
    cp = SQLiteCheckpointer(db)
    await cp.bootstrap()
    try:
        cf_run = await GraphRun.counterfactual(
            cp,
            parent_run_id,
            step=from_step,
            mutate=mutation,
        )
        cf_run_id = cf_run.run_id
        typer.echo(f"cf_run_id={cf_run_id}")

        if not show_diff:
            return

        # The cf-run handle is in-memory only at this point -- the
        # GraphRun.counterfactual contract returns a fresh handle that
        # ``await cf_run.wait()`` would drive forward. For the CLI's
        # diff-render path we operate on the parent + cf checkpoint
        # snapshots already on disk: the parent's full history (steps
        # 0..N) and the cf-side's fork-step state (which the engine
        # writes when the cf-run actually executes). When the cf-run
        # has not produced any checkpoints yet, the diff is
        # degenerate (cf side empty), but the renderer still surfaces
        # ``derived_hash`` per design §3.8.6.
        parent = await RunHistory.load(parent_run_id, checkpointer=cp)
        cf = await RunHistory.load(cf_run_id, checkpointer=cp)
        diff = compare(parent, cf)
        # Canonical JSON via stargraph.ir.dumps (FR-15, AC-11.5): compact form,
        # machine-readable, consistent with the rest of the inspect / replay
        # surfaces. Routed through the canonical entry point so the dumps
        # walker (`tests/unit/test_ir_dumps_walker.py`) stays green. ``RunDiff``
        # is structurally an IR-shaped Pydantic model (extra='forbid', JSON-mode
        # fields) but lives under ``stargraph.replay`` rather than ``stargraph.ir``;
        # cast to ``IRBase`` is the documented bridge — ``dumps`` only relies on
        # the ``model_dump(mode='json', exclude_defaults=True)`` Pydantic API
        # which both bases share.
        typer.echo(ir_dumps(cast("IRBase", diff)))
    finally:
        await cp.close()


def cmd(
    run_id: Annotated[
        str,
        typer.Argument(help="Parent run id to fork the counterfactual from."),
    ],
    db: Annotated[
        Path,
        typer.Option(
            "--db",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="SQLite Checkpointer DB containing the parent run's checkpoints.",
        ),
    ],
    mutation: Annotated[
        Path | None,
        typer.Option(
            "--mutation",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help=(
                "JSON file describing a CounterfactualMutation "
                "(state_overrides / facts_assert / etc.). When omitted, "
                "an empty mutation is used."
            ),
        ),
    ] = None,
    from_step: Annotated[
        int,
        typer.Option(
            "--from-step",
            min=0,
            help="Step at which to fork the cf-run (defaults to step 0).",
        ),
    ] = 0,
    show_diff: Annotated[
        bool,
        typer.Option(
            "--diff/--no-diff",
            help=(
                "After forking, render the parent vs cf RunDiff as JSON. "
                "Without --diff, only the cf-run-id is printed."
            ),
        ),
    ] = False,
) -> None:
    """Drive a counterfactual replay of ``run_id`` and optionally render the diff (FR-26, AC-9.3).

    Loads a :class:`CounterfactualMutation` from JSON when ``--mutation``
    is supplied, forks a fresh cf-run from ``run_id`` at ``--from-step``
    via :meth:`GraphRun.counterfactual`, and (with ``--diff``) prints the
    parent vs cf :class:`RunDiff` as a JSON document.
    """
    cf_mutation = _load_mutation(mutation)
    asyncio.run(
        _drive_replay(
            db,
            run_id,
            cf_mutation,
            from_step,
            show_diff=show_diff,
        )
    )
