# SPDX-License-Identifier: Apache-2.0
"""SynthesizeGraph — slot-driven Jinja synthesis of a graph artifact."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from harbor.skills.shipwright.nodes.synthesize import SynthesizeGraph
from harbor.skills.shipwright.state import SpecSlot, State

if TYPE_CHECKING:
    from harbor.nodes.base import ExecutionContext


@pytest.mark.integration
async def test_synthesize_emits_three_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_node_bodies(self: SynthesizeGraph, slots: dict[str, Any]) -> dict[str, str]:
        return {
            "classify": "return {'intent': 'unknown'}",
            "act": "return {'result': state.intent}",
        }

    monkeypatch.setattr(SynthesizeGraph, "_call_predictor", fake_node_bodies)

    state = State(
        kind="graph",
        slots={
            "name": SpecSlot(name="name", value="triage", origin="user"),
            "purpose": SpecSlot(name="purpose", value="triage SOC alerts", origin="user"),
            "nodes": SpecSlot(name="nodes", value=["classify", "act"], origin="user"),
            "state_fields": SpecSlot(
                name="state_fields",
                value=[
                    {"name": "intent", "type": "str", "annotated": True},
                    {"name": "result", "type": "str", "annotated": False},
                ],
                origin="user",
            ),
            "stores": SpecSlot(name="stores", value={"doc": "sqlite:./.docs"}, origin="user"),
            "triggers": SpecSlot(name="triggers", value=[{"type": "manual"}], origin="user"),
        },
    )

    out = await SynthesizeGraph().execute(
        state, cast("ExecutionContext", SimpleNamespace(run_id="r-test"))
    )
    files = out["artifact_files"]

    assert set(files) == {"state.py", "harbor.yaml", "tests/test_smoke.py"}
    assert "class State" in files["state.py"]
    assert "Annotated[str, Mirror()]" in files["state.py"]
    assert "name: triage" in files["harbor.yaml"]
    assert "classify" in files["harbor.yaml"]
    assert "test_smoke" in files["tests/test_smoke.py"]


@pytest.mark.integration
async def test_synthesize_skips_when_kind_unset() -> None:
    out = await SynthesizeGraph().execute(
        State(), cast("ExecutionContext", SimpleNamespace(run_id="r-test"))
    )
    assert out == {"artifact_files": {}}
