# SPDX-License-Identifier: Apache-2.0
"""NodeProgram — the DSPy generator, shared by the build node and the optimizer.

A single ``dspy.Module`` so that what the offline optimizer compiles is *exactly*
the program the graph runs. ``forward`` returns the raw ``dspy.Prediction``
(what BootstrapFewShot needs); ``generate`` coerces it into the plain dict the
build node consumes. Compiled few-shot demos from ``compiled.json`` are loaded
at construction — the idea-2 → idea-1 feedback edge.
"""

from __future__ import annotations

from typing import Any

import dspy  # pyright: ignore[reportMissingTypeStubs]

from stargraph.skills._smith.lm import (
    DEFAULT_OLLAMA_URL,
    clarify,
    configure_lm,
    make_lm,
)
from stargraph.skills._smith.program import INPUT_FIELDS, SmithProgram, as_dict, as_list
from stargraph.skills.nodesmith import _ledger

# Re-exported from the shared core so callers keep importing them from here.
__all__ = [
    "DEFAULT_OLLAMA_URL",
    "INPUT_FIELDS",
    "NodeProgram",
    "NodeSignature",
    "clarify",
    "coerce",
    "configure_lm",
    "make_lm",
]


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
    relevant_context: str = dspy.InputField(  # pyright: ignore[reportUnknownMemberType]
        desc="grounding: similar existing nodes + accepted examples + web research"
    )

    class_name: str = dspy.OutputField(desc="PascalCase class name")  # pyright: ignore[reportUnknownMemberType]
    reads: list[str] = dspy.OutputField(desc="state fields read")  # pyright: ignore[reportUnknownMemberType]
    writes: list[str] = dspy.OutputField(desc="state fields written")  # pyright: ignore[reportUnknownMemberType]
    fixture: dict[str, Any] = dspy.OutputField(desc="sample values covering reads")  # pyright: ignore[reportUnknownMemberType]
    node_source: str = dspy.OutputField(desc="node.py: one NodeBase subclass")  # pyright: ignore[reportUnknownMemberType]
    test_source: str = dspy.OutputField(desc="test_node.py for the node")  # pyright: ignore[reportUnknownMemberType]


def coerce(pred: Any) -> dict[str, Any]:
    """Normalize a ``dspy.Prediction`` (or any attr-bag) into a plain dict."""
    return {
        "class_name": str(getattr(pred, "class_name", "")),
        "reads": as_list(getattr(pred, "reads", [])),
        "writes": as_list(getattr(pred, "writes", [])),
        "fixture": as_dict(getattr(pred, "fixture", {})),
        "node_source": str(getattr(pred, "node_source", "")),
        "test_source": str(getattr(pred, "test_source", "")),
    }


class NodeProgram(SmithProgram):
    def __init__(self, *, load_compiled: bool = True) -> None:
        super().__init__(
            signature=NodeSignature,
            coerce=coerce,
            load_compiled_demos=_ledger.load_compiled_demos,
            load_compiled=load_compiled,
        )
