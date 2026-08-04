# SPDX-License-Identifier: Apache-2.0
"""``stargraph skills compile|list`` -- the SKILL.md authoring surface (P2).

* ``stargraph skills compile <path>`` compiles one SKILL.md (or a skill
  directory containing one) in strict mode and prints a JSON envelope:
  ``{"ok": true, "spec": {...}, "warnings": [...]}`` or
  ``{"ok": false, "errors": [{path, expected, actual, hint}]}`` with
  exit code 1. The envelope is machine-readable on purpose -- editors
  and CI hooks consume it.
* ``stargraph skills list`` walks the discovery roots
  (``$STARGRAPH_SKILLS_DIR``, ``./skills/``, ``~/.stargraph/skills/``)
  and prints every discovered skill (invalid files are reported, not
  fatal -- mirroring the serve-time seeding behavior).
"""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003 -- runtime use by typer.Annotated
from typing import Annotated, Any

import typer

from stargraph.errors import ValidationError
from stargraph.skills.markdown import compile_skill_md, discover_skill_files

__all__ = ["skills_app"]

skills_app = typer.Typer(no_args_is_help=True, help="Markdown (SKILL.md) skill operations.")


def _error_row(exc: ValidationError) -> dict[str, Any]:
    ctx = exc.context
    return {
        "path": ctx.get("path", ""),
        "expected": ctx.get("expected", ""),
        "actual": ctx.get("actual", ""),
        "hint": ctx.get("hint", ""),
    }


@skills_app.command("compile")
def compile_cmd(
    path: Annotated[Path, typer.Argument(help="A SKILL.md file or a directory containing one.")],
) -> None:
    """Compile one SKILL.md and print the JSON envelope."""
    md_path = path / "SKILL.md" if path.is_dir() else path
    if not md_path.is_file():
        row = {
            "path": str(md_path),
            "expected": "an existing SKILL.md",
            "actual": "missing file",
            "hint": "",
        }
        typer.echo(json.dumps({"ok": False, "errors": [row]}))
        raise typer.Exit(code=1)
    try:
        compiled = compile_skill_md(md_path)
    except ValidationError as exc:
        typer.echo(json.dumps({"ok": False, "errors": [_error_row(exc)]}))
        raise typer.Exit(code=1) from exc
    typer.echo(
        json.dumps(
            {
                "ok": True,
                "spec": compiled.spec.model_dump(mode="json"),
                "system_prompt_chars": len(compiled.spec.system_prompt or ""),
                "warnings": list(compiled.warnings),
            }
        )
    )


@skills_app.command("list")
def list_cmd(
    skills_dir: Annotated[
        Path | None,
        typer.Option(
            "--skills-dir",
            help="Extra discovery root (overrides $STARGRAPH_SKILLS_DIR).",
        ),
    ] = None,
) -> None:
    """List every markdown skill on the discovery roots."""
    rows: list[dict[str, Any]] = []
    for md_path in discover_skill_files(extra_dir=skills_dir):
        try:
            compiled = compile_skill_md(md_path)
        except ValidationError as exc:
            rows.append({"path": str(md_path), "ok": False, "error": _error_row(exc)})
            continue
        spec = compiled.spec
        rows.append(
            {
                "path": str(md_path),
                "ok": True,
                "id": f"{spec.namespace}/{spec.name}@{spec.version}",
                "kind": spec.kind,
                "description": spec.description,
            }
        )
    typer.echo(json.dumps({"skills": rows, "count": len(rows)}))
