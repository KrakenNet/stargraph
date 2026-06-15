# SPDX-License-Identifier: Apache-2.0
"""``stargraph.cli`` -- typer-based CLI surface for the engine (FR-8, design §3.10).

Phase 3 ships the full design §3.10 table: ``run`` (with ``--inspect``),
``inspect`` (audit-log streaming), ``simulate`` (offline rule trace), and
``counterfactual`` (cf-derived graph hash). The ``pyproject.toml``
``[project.scripts]`` entry binds the ``stargraph`` console script to
:func:`main` here.
"""

from __future__ import annotations

import typer

from stargraph.cli import (
    context,
    counterfactual,
    inspect,
    replay,
    respond,
    run,
    serve,
    simulate,
    verify_audit,
)

__all__ = ["app", "main"]

app = typer.Typer(no_args_is_help=True)


@app.callback()
def _root() -> None:  # pyright: ignore[reportUnusedFunction]
    """Stargraph -- stateful agent-graph framework with deterministic governance."""


app.command("run")(run.cmd)
app.command("inspect")(inspect.cmd)
app.command("simulate")(simulate.cmd)
app.command("counterfactual")(counterfactual.cmd)
app.command("replay")(replay.cmd)
app.command("respond")(respond.cmd)
app.command("serve")(serve.cmd)
app.command("verify-audit")(verify_audit.cmd)
app.command("context")(context.cmd)


def main() -> None:
    """Console-script entry point (``stargraph`` -> :func:`main`)."""
    app()
