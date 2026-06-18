# SPDX-License-Identifier: Apache-2.0
"""NodeProgram — the DSPy generator, shared by the build node and the optimizer.

A single ``dspy.Module`` so that what the offline optimizer compiles is *exactly*
the program the graph runs. ``forward`` returns the raw ``dspy.Prediction``
(what BootstrapFewShot needs); ``generate`` coerces it into the plain dict the
build node consumes. Compiled few-shot demos from ``compiled.json`` are loaded
at construction — the idea-2 → idea-1 feedback edge.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any, cast

import dspy  # pyright: ignore[reportMissingTypeStubs]

from stargraph.skills.nodesmith import _ledger

# The fields the predictor takes as inputs (used when rebuilding demos).
INPUT_FIELDS = ("brief", "lessons", "last_findings")


def configure_lm(url: str, model: str, key: str = "placeholder") -> None:
    """Point DSPy at an OpenAI-compatible endpoint (e.g. Ollama). Shared by the
    ``nodesmith make`` CLI and the offline optimizer so the wiring is identical."""
    dspy.configure(lm=dspy.LM(f"openai/{model}", api_base=url, api_key=key))  # pyright: ignore[reportUnknownMemberType]


class NodeSignature(dspy.Signature):  # pyright: ignore[reportUnknownMemberType]
    """Write one Stargraph node and a pytest test for it, from a brief.

    A Stargraph node subclasses ``stargraph.nodes.base.NodeBase`` and defines
    exactly one method::

        async def execute(self, state, ctx) -> dict[str, Any]

    It reads inputs with ``getattr(state, "<field>", default)``, never mutates
    state in place, and returns a dict keyed ONLY by the fields it writes. The
    class must be zero-arg constructible. Honor every lesson in ``lessons`` and
    fix every issue in ``last_findings``.
    """

    brief: str = dspy.InputField(desc="what the node should do")  # pyright: ignore[reportUnknownMemberType]
    lessons: list[str] = dspy.InputField(desc="past failures to avoid")  # pyright: ignore[reportUnknownMemberType]
    last_findings: list[dict[str, Any]] = dspy.InputField(desc="prior attempt findings")  # pyright: ignore[reportUnknownMemberType]

    class_name: str = dspy.OutputField(desc="PascalCase class name")  # pyright: ignore[reportUnknownMemberType]
    reads: list[str] = dspy.OutputField(desc="state fields read")  # pyright: ignore[reportUnknownMemberType]
    writes: list[str] = dspy.OutputField(desc="state fields written")  # pyright: ignore[reportUnknownMemberType]
    fixture: dict[str, Any] = dspy.OutputField(desc="sample values covering reads")  # pyright: ignore[reportUnknownMemberType]
    node_source: str = dspy.OutputField(desc="node.py: one NodeBase subclass")  # pyright: ignore[reportUnknownMemberType]
    test_source: str = dspy.OutputField(desc="test_node.py for the node")  # pyright: ignore[reportUnknownMemberType]


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in cast("list[Any]", value)]
    if isinstance(value, str) and value.strip():
        return [p.strip() for p in value.split(",") if p.strip()]
    return []


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return cast("dict[str, Any]", value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else {}
    return {}


def coerce(pred: Any) -> dict[str, Any]:
    """Normalize a ``dspy.Prediction`` (or any attr-bag) into a plain dict."""
    return {
        "class_name": str(getattr(pred, "class_name", "")),
        "reads": _as_list(getattr(pred, "reads", [])),
        "writes": _as_list(getattr(pred, "writes", [])),
        "fixture": _as_dict(getattr(pred, "fixture", {})),
        "node_source": str(getattr(pred, "node_source", "")),
        "test_source": str(getattr(pred, "test_source", "")),
    }


class NodeProgram(dspy.Module):  # pyright: ignore[reportUnknownMemberType]
    def __init__(self, *, load_compiled: bool = True) -> None:
        super().__init__()  # pyright: ignore[reportUnknownMemberType]
        self.gen = dspy.Predict(NodeSignature)  # pyright: ignore[reportUnknownMemberType]
        if load_compiled:
            self._load_demos()

    def _load_demos(self) -> None:
        demos = _ledger.load_compiled_demos()
        if not demos:
            return
        # malformed compiled.json must never break generation
        with contextlib.suppress(TypeError, ValueError):
            built = [dspy.Example(**d).with_inputs(*INPUT_FIELDS) for d in demos]  # pyright: ignore[reportUnknownMemberType]
            self.gen.demos = built  # pyright: ignore[reportUnknownMemberType]

    def forward(self, brief: str, lessons: list[str], last_findings: list[dict[str, Any]]) -> Any:
        return self.gen(brief=brief, lessons=lessons, last_findings=last_findings)  # pyright: ignore[reportUnknownMemberType]

    def generate(
        self, brief: str, lessons: list[str], last_findings: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return coerce(self.forward(brief=brief, lessons=lessons, last_findings=last_findings))
