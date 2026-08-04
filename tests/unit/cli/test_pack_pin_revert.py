# SPDX-License-Identifier: Apache-2.0
"""Unit: ``stargraph pack pin|revert`` (W2 ops surface).

Rule-pack pins live in the graph IR's ``governance:`` PackMount entries
(the only durable pack-version store); the commands are metadata edits
over that YAML. Tests drive a real graph file derived from the shared
``tests/fixtures/sample-graph.yaml`` fixture (per tests/AGENTS.md,
reuse fixtures over hand-rolled IR):

* pin sets ``version`` on the matching mount, keeps the document valid
  IR, preserves the leading comment header, and audits ``pack_pinned``.
* pin refuses for a pack id that is not mounted.
* revert removes the pin and audits ``pack_unpinned``; a second revert
  refuses (not pinned).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from typer.testing import CliRunner

from stargraph.audit.jsonl import unwrap_audit_record
from stargraph.cli import app
from stargraph.ir import IRDocument

_FIXTURE = Path(__file__).parents[2] / "fixtures" / "sample-graph.yaml"

runner = CliRunner()


def _graph_with_mount(tmp_path: Path, *, version: str | None) -> Path:
    """Copy the sample-graph fixture and add a governance PackMount."""
    raw = cast("dict[str, Any]", yaml.safe_load(_FIXTURE.read_text()))
    mount: dict[str, Any] = {"id": "sdw.routing"}
    if version is not None:
        mount["version"] = version
    raw["governance"] = [mount]
    IRDocument.model_validate(raw)  # fixture-derived doc must stay valid IR
    path = tmp_path / "graph.yaml"
    path.write_text(
        "# SPDX-License-Identifier: Apache-2.0\n" + yaml.safe_dump(raw, sort_keys=False)
    )
    return path


def _mount_version(path: Path) -> str | None:
    raw = cast("dict[str, Any]", yaml.safe_load(path.read_text()))
    governance = cast("list[dict[str, Any]]", raw["governance"])
    return cast("str | None", governance[0].get("version"))


def _audit_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    for ln in path.read_text().splitlines():
        if not ln.strip():
            continue
        unwrapped: Any = unwrap_audit_record(json.loads(ln))
        if isinstance(unwrapped, dict) and "fact" in unwrapped:
            events.append(cast("dict[str, Any]", unwrapped))
    return events


@pytest.mark.unit
def test_pin_sets_mount_version_and_audits(tmp_path: Path) -> None:
    graph = _graph_with_mount(tmp_path, version="1.0.0")

    result = runner.invoke(app, ["pack", "pin", "sdw.routing", "2.0.0", "--graph", str(graph)])

    assert result.exit_code == 0, result.output
    assert _mount_version(graph) == "2.0.0"
    # Document survives as valid IR and keeps its SPDX comment header.
    reloaded = cast("dict[str, Any]", yaml.safe_load(graph.read_text()))
    IRDocument.model_validate(reloaded)
    assert graph.read_text().startswith("# SPDX-License-Identifier: Apache-2.0")

    events = _audit_events(tmp_path / "graph.yaml.audit.jsonl")
    assert len(events) == 1
    assert events[0]["fact"]["kind"] == "pack_pinned"
    assert events[0]["fact"]["from_version"] == "1.0.0"
    assert events[0]["fact"]["to_version"] == "2.0.0"


@pytest.mark.unit
def test_pin_refuses_unmounted_pack(tmp_path: Path) -> None:
    graph = _graph_with_mount(tmp_path, version="1.0.0")

    result = runner.invoke(app, ["pack", "pin", "not.mounted", "2.0.0", "--graph", str(graph)])

    assert result.exit_code != 0
    assert _mount_version(graph) == "1.0.0", "refusal must not touch the graph"
    assert not (tmp_path / "graph.yaml.audit.jsonl").exists()


@pytest.mark.unit
def test_revert_unpins_and_second_revert_refuses(tmp_path: Path) -> None:
    graph = _graph_with_mount(tmp_path, version="1.0.0")

    first = runner.invoke(app, ["pack", "revert", "sdw.routing", "--graph", str(graph)])
    assert first.exit_code == 0, first.output
    assert _mount_version(graph) is None
    reloaded = cast("dict[str, Any]", yaml.safe_load(graph.read_text()))
    IRDocument.model_validate(reloaded)

    events = _audit_events(tmp_path / "graph.yaml.audit.jsonl")
    assert len(events) == 1
    assert events[0]["fact"]["kind"] == "pack_unpinned"
    assert events[0]["fact"]["from_version"] == "1.0.0"

    second = runner.invoke(app, ["pack", "revert", "sdw.routing", "--graph", str(graph)])
    assert second.exit_code != 0
    assert len(_audit_events(tmp_path / "graph.yaml.audit.jsonl")) == 1
