# SPDX-License-Identifier: Apache-2.0
"""``stargraph compile`` + ``stargraph new`` -- the authoring front door (P4).

``compile`` lowers an authoring-format YAML to the strict IR and prints
it (``--show-clips`` adds the generated rule LHS/actions), so authors
can see exactly what the sugar becomes. ``new`` scaffolds a starting
point: the ``research-bot`` authoring template, or any shipped bundle
(``stargraph new rag-qa`` copies its ``graph.yaml`` + ``SKILL.md``).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated

import typer
import yaml

from stargraph.authoring import authoring_clips, compile_authoring, is_authoring_format
from stargraph.bundles import BUNDLE_NAMES, bundle_path
from stargraph.errors import StargraphError
from stargraph.ir import IRDocument

__all__ = ["compile_cmd", "new_cmd"]

_RESEARCH_BOT_YAML = """\
# research-bot: web research with a completeness judge and a feedback loop.
# Run: stargraph run research-bot.yaml --lm-url <url> --lm-model <model> \\
#        --inputs question="your question"
id: research-bot
state:
  question: str
  brief: str
  answer: str
  rationale: str
  score: str
  verdict: {type: str, route: true}
nodes:
  brief:
    kind: template
    template: "{question}\\n\\nJudge-identified gaps (empty on first pass):\\n{rationale}"
    out: brief
  research:
    kind: react
    input: brief
    tools: [std.web_search, std.fetch_page]
  judge:
    kind: judge
    input: answer
    rubric: "The answer fully addresses the question with specific, sourced facts."
routes:
  judge: {fail: brief, pass: done}
"""


def compile_cmd(
    graph: Annotated[Path, typer.Argument(help="Authoring-format (or IR) YAML file.")],
    show_clips: Annotated[
        bool,
        typer.Option("--show-clips", help="Also print each generated rule's CLIPS LHS."),
    ] = False,
) -> None:
    """Lower authoring YAML to strict IR and print it (learning/debug)."""
    if not graph.is_file():
        typer.echo(f"error: {graph} not found", err=True)
        raise typer.Exit(code=1)
    doc = yaml.safe_load(graph.read_text(encoding="utf-8"))
    try:
        if is_authoring_format(doc):
            ir = compile_authoring(doc, default_id=graph.stem)
        else:
            typer.echo("# input already declares ir_version; validating as IR", err=True)
            ir = IRDocument.model_validate(doc)
    except StargraphError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(yaml.safe_dump(ir.model_dump(mode="json", exclude_none=True), sort_keys=False))
    if show_clips:
        typer.echo("# generated rules (CLIPS LHS => actions):")
        for line in authoring_clips(ir):
            typer.echo(f"#   {line}")


def new_cmd(
    template: Annotated[
        str,
        typer.Argument(help="'research-bot' or a bundle name (see `stargraph new --help`)."),
    ],
    dest: Annotated[
        Path | None,
        typer.Option("--dest", help="Destination (default: ./<template>.yaml or ./<template>/)."),
    ] = None,
) -> None:
    """Scaffold a new graph from a template or shipped bundle."""
    if template == "research-bot":
        target = dest if dest is not None else Path(f"{template}.yaml")
        if target.exists():
            typer.echo(f"error: {target} already exists", err=True)
            raise typer.Exit(code=1)
        target.write_text(_RESEARCH_BOT_YAML, encoding="utf-8")
        typer.echo(f"wrote {target}")
        typer.echo(f"next: stargraph run {target} --lm-url <url> --lm-model <model>")
        return
    if template in BUNDLE_NAMES:
        src = bundle_path(template)
        target_dir = dest if dest is not None else Path(template)
        if target_dir.exists():
            typer.echo(f"error: {target_dir} already exists", err=True)
            raise typer.Exit(code=1)
        target_dir.mkdir(parents=True)
        for name in ("graph.yaml", "SKILL.md"):
            shutil.copyfile(src / name, target_dir / name)
        typer.echo(f"wrote {target_dir}/graph.yaml and {target_dir}/SKILL.md")
        typer.echo(f"next: stargraph run {target_dir}/graph.yaml")
        return
    options = ", ".join(["research-bot", *BUNDLE_NAMES])
    typer.echo(f"error: unknown template {template!r}; one of: {options}", err=True)
    raise typer.Exit(code=1)
