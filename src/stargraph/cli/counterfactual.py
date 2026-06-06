# SPDX-License-Identifier: Apache-2.0
"""``stargraph counterfactual`` -- compute cf-derived graph hash from a YAML mutation (FR-27).

Per design §3.10, ``counterfactual`` is the operator-side dry-run for a
counterfactual fork: load the IR, load a YAML mutation file, validate it
through :class:`stargraph.replay.counterfactual.CounterfactualMutation`, and
print the cf-derived graph hash from
:func:`stargraph.replay.counterfactual.derived_graph_hash`. The full cf-replay
loop (resume from step ``N`` with the mutation applied) is delegated to
the engine in later phases; this surface lets operators verify the
mutation YAML round-trips and pins the new ``graph_hash`` they should
expect to see in the resulting checkpoint.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 -- runtime use by typer.Annotated
from typing import Annotated, Any, cast

import typer
import yaml

from stargraph.graph import Graph
from stargraph.ir import IRDocument
from stargraph.replay.counterfactual import CounterfactualMutation, derived_graph_hash

__all__ = ["cmd"]


def cmd(
    graph: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Path to the parent run's IR YAML graph definition.",
        ),
    ],
    step: Annotated[
        int,
        typer.Option(
            "--step",
            min=0,
            help="Checkpoint step index at which to fork (recorded in output).",
        ),
    ],
    mutate: Annotated[
        Path,
        typer.Option(
            "--mutate",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="YAML file describing a CounterfactualMutation (design §3.10).",
        ),
    ],
) -> None:
    """Validate a cf mutation and print the derived graph hash (FR-27)."""
    ir = IRDocument.model_validate(yaml.safe_load(graph.read_text(encoding="utf-8")))
    mutation_payload = cast(
        "dict[str, Any]",
        yaml.safe_load(mutate.read_text(encoding="utf-8")) or {},
    )
    # ``model_validate`` rejects unknown keys (extra='forbid' on
    # CounterfactualMutation) so typos in the mutation YAML surface here.
    mutation = CounterfactualMutation.model_validate(mutation_payload)

    g = Graph(ir)
    derived = derived_graph_hash(g.graph_hash, mutation)
    typer.echo(f"original_graph_hash={g.graph_hash}")
    typer.echo(f"cf_step={step}")
    typer.echo(f"derived_graph_hash={derived}")
