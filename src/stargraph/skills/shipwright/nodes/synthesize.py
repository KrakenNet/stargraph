# SPDX-License-Identifier: Apache-2.0
"""SynthesizeGraph — render templates into artifact_files using filled slots."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import dspy  # pyright: ignore[reportMissingTypeStubs]
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from pydantic import BaseModel


_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"


class _NodeBodiesSignature(dspy.Signature):
    """Generate the body of each requested node as a Python expression."""

    slots: dict[str, object] = dspy.InputField()  # pyright: ignore[reportUnknownMemberType]
    bodies: dict[str, str] = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="dict mapping node-name to a single-line return-statement body",
    )


class SynthesizeGraph(NodeBase):
    """Render the graph artifact files from filled slots + LLM-generated node bodies.

    Marked `must_stub` in topology — replay-deterministic. Tests stub `_call_predictor`.
    """

    def __init__(self) -> None:
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        self._predictor = dspy.Predict(_NodeBodiesSignature)  # pyright: ignore[reportUnknownMemberType]

    def _call_predictor(self, slots: dict[str, Any]) -> dict[str, str]:
        result = self._predictor(slots=slots)  # pyright: ignore[reportUnknownMemberType]
        return dict(result.bodies)  # pyright: ignore[reportUnknownArgumentType,reportUnknownMemberType]

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        kind = getattr(state, "kind", None)
        if kind != "graph":
            return {"artifact_files": {}}

        raw_slots = getattr(state, "slots", {}) or {}
        slots = {n: getattr(s, "value", s) for n, s in raw_slots.items()}

        required = {"name", "purpose", "nodes", "state_fields", "stores", "triggers"}
        missing = required - set(slots)
        if missing:
            raise ValueError(f"missing required slots: {sorted(missing)}")

        node_bodies = self._call_predictor(slots)
        slots["node_bodies"] = node_bodies

        files = {
            "state.py": self._env.get_template("state.py.j2").render(**slots),
            "stargraph.yaml": self._env.get_template("stargraph.yaml.j2").render(**slots),
            "tests/test_smoke.py": self._env.get_template("test_smoke.py.j2").render(**slots),
        }
        return {"artifact_files": files}
