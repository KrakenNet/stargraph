# SPDX-License-Identifier: Apache-2.0
"""``stargraph simulate`` -- offline rule-firing trace for an IR (FR-9).

Per design §3.10, ``simulate`` validates rule logic against caller-supplied
synthetic node outputs without invoking any tool, LLM, or checkpoint. The
implementation is a thin :class:`Graph.simulate` wrapper that loads two
YAML files (the IR + the fixture mapping) and prints the per-rule firing
trace.
"""

from __future__ import annotations

import asyncio
from pathlib import Path  # noqa: TC003 -- runtime use by typer.Annotated
from typing import Annotated, Any, cast

import typer
import yaml

from stargraph.graph import Graph
from stargraph.ir import IRDocument

__all__ = ["cmd"]


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
    fixtures: Annotated[
        Path,
        typer.Option(
            "--fixtures",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="YAML mapping of node_id -> synthetic output (one entry per IR node).",
        ),
    ],
) -> None:
    """Run :meth:`Graph.simulate` against ``fixtures`` and print the trace."""
    ir = IRDocument.model_validate(yaml.safe_load(graph.read_text(encoding="utf-8")))
    raw = cast("Any", yaml.safe_load(fixtures.read_text(encoding="utf-8")) or {})
    if not isinstance(raw, dict):
        raise typer.BadParameter(f"--fixtures must be a YAML mapping, got {type(raw).__name__}")
    fixture_map = cast("dict[str, Any]", raw)

    g = Graph(ir)
    result = asyncio.run(g.simulate(fixture_map))
    typer.echo(f"graph_hash={g.graph_hash}")
    typer.echo(f"rule_firings={len(result.rule_firings)}")
    for firing in result.rule_firings:
        matched = ",".join(firing.matched_nodes) or "-"
        actions = ",".join(firing.action_kinds) or "-"
        typer.echo(
            f"  rule={firing.rule_id} fired={firing.fired} matched=[{matched}] actions=[{actions}]"
        )
