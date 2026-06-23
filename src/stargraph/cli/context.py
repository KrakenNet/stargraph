# SPDX-License-Identifier: Apache-2.0
"""``stargraph context dump`` -- a machine-readable grounding pack.

Emits a single JSON document an automated author (or a human) can load to
ground itself in the *exact* contracts of this installation instead of
guessing: the public API surface, the node ``kind:`` values the CLI can
build, the IR JSON-Schema location, the typed error catalog, the fact
namespaces, and any runnable examples in the working directory.

Everything is introspected from the live package; the only static datum is
the fact-namespace list, which is the documented contract
(``design-docs/stargraph-facts.md``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

import stargraph
from stargraph import errors as _errors
from stargraph.cli.run import node_kinds
from stargraph.schemas import schema_path, schema_url

__all__ = ["cmd"]

_FACT_NAMESPACES: dict[str, str] = {
    "stargraph.*": "runtime-emitted; rules read only",
    "bosun.*": "governance packs emit/consume",
    "user.*": "application code and rules",
    "<plugin>.*": "plugin authors; prefix must be registered in the manifest",
}


def _error_catalog() -> list[str]:
    return sorted(
        name
        for name, obj in vars(_errors).items()
        if isinstance(obj, type) and issubclass(obj, _errors.StargraphError)
    )


def _examples(root: Path) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for yml in sorted(root.glob("*.yaml")):
        first = ""
        for line in yml.read_text(encoding="utf-8").splitlines():
            stripped = line.strip().lstrip("# ").strip()
            if stripped and not line.startswith("# SPDX"):
                first = stripped
                break
        out.append({"file": str(yml), "summary": first})
    return out


def _build_pack() -> dict[str, Any]:
    return {
        "stargraph_version": stargraph.__version__,
        "public_api": sorted(stargraph.__all__),
        "node_kinds": node_kinds(),
        "node_kinds_note": "Also accepts 'module.path:ClassName' for custom NodeBase subclasses.",
        "ir_schema": {"path": str(schema_path()), "url": schema_url()},
        "errors": _error_catalog(),
        "fact_namespaces": _FACT_NAMESPACES,
        "examples": _examples(Path("examples")),
        "docs": {
            "architecture_map": "docs/architecture-map.md",
            "getting_started": "docs/getting-started.md",
            "how_to": "docs/how-to/",
            "fact_vocabulary": "design-docs/stargraph-facts.md",
        },
    }


def cmd(
    action: Annotated[
        str,
        typer.Argument(help="What to do. Only 'dump' is supported."),
    ] = "dump",
    compact: Annotated[
        bool,
        typer.Option("--compact", help="Emit single-line JSON instead of indented."),
    ] = False,
) -> None:
    """Print a JSON grounding pack for this Stargraph installation."""
    if action != "dump":
        raise typer.BadParameter(f"unknown action {action!r}; expected 'dump'")
    pack = _build_pack()
    typer.echo(json.dumps(pack, separators=(",", ":")) if compact else json.dumps(pack, indent=2))
