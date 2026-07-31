# SPDX-License-Identifier: Apache-2.0
"""Authoring front door end-to-end (P4): run, compile, new.

The capstone: an authoring-format YAML (no ir_version, value routes on
``verdict``) runs through the stock ``stargraph run`` command and its
rules fire live -- ``work`` loops twice before ``finish`` halts, which
a linear walk cannot produce.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from stargraph.cli import app

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

_AUTHORED_LOOP = """\
id: authored-loop
state:
  rounds: int
  message: str
  verdict: {type: str, route: true}
nodes:
  work: {kind: "tests.fixtures.authored_nodes:WorkNode"}
  finish: {kind: "tests.fixtures.authored_nodes:FinishNode"}
routes:
  work: {refine: work, sufficient: finish}
  finish: done
"""


def test_authored_yaml_runs_with_live_routing(tmp_path: Path) -> None:
    graph = tmp_path / "loop.yaml"
    graph.write_text(_AUTHORED_LOOP, encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["run", str(graph), "--checkpoint", str(tmp_path / "ck.sqlite")],
    )

    assert result.exit_code == 0, result.output
    assert "status=done" in result.output
    assert "rounds: 2" in result.output
    assert "finished after 2 rounds" in result.output


def test_compile_prints_lowered_ir(tmp_path: Path) -> None:
    graph = tmp_path / "loop.yaml"
    graph.write_text(_AUTHORED_LOOP, encoding="utf-8")

    result = CliRunner().invoke(app, ["compile", str(graph), "--show-clips"])

    assert result.exit_code == 0, result.output
    assert "ir_version: 1.0.0" in result.output
    assert "graph:authored-loop" in result.output
    assert "_sg_authored_authored_loop:State" in result.output
    assert 'r-work-refine: (node-id (id work)) (verdict (value "refine")) => goto work' in (
        result.output
    )


def test_compile_shape_error_is_loud(tmp_path: Path) -> None:
    graph = tmp_path / "bad.yaml"
    graph.write_text("nodes:\n  n: {kind: echo}\nroutes:\n  ghost: done\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["compile", str(graph)])

    assert result.exit_code == 1
    assert "not a declared node" in result.output


def test_new_research_bot_scaffold_compiles(tmp_path: Path) -> None:
    dest = tmp_path / "research-bot.yaml"
    runner = CliRunner()

    result = runner.invoke(app, ["new", "research-bot", "--dest", str(dest)])
    assert result.exit_code == 0, result.output
    assert dest.is_file()

    # The scaffold must itself lower cleanly -- no broken examples.
    compiled = runner.invoke(app, ["compile", str(dest)])
    assert compiled.exit_code == 0, compiled.output
    assert "graph:research-bot" in compiled.output

    again = runner.invoke(app, ["new", "research-bot", "--dest", str(dest)])
    assert again.exit_code == 1
    assert "already exists" in again.output


def test_new_bundle_copies_bundle(tmp_path: Path) -> None:
    dest = tmp_path / "rag-qa"

    result = CliRunner().invoke(app, ["new", "rag-qa", "--dest", str(dest)])

    assert result.exit_code == 0, result.output
    assert (dest / "graph.yaml").is_file()
    assert (dest / "SKILL.md").is_file()


def test_new_unknown_template_is_loud() -> None:
    result = CliRunner().invoke(app, ["new", "no-such-template"])

    assert result.exit_code == 1
    assert "unknown template" in result.output
