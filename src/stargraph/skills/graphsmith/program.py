# SPDX-License-Identifier: Apache-2.0
"""GraphProgram — the DSPy generator for graph bundles, bound to SmithProgram.

The generate/forward/demo plumbing lives in :class:`SmithProgram`; this module
supplies the *graph* signature (the fields a bundle generation emits) and
``coerce`` (Prediction → plain dict). LM construction + ``clarify`` are
re-exported from the shared core so callers import them from here.
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
from stargraph.skills.graphsmith import _ledger

# Re-exported from the shared core so callers keep importing them from here.
__all__ = [
    "DEFAULT_OLLAMA_URL",
    "INPUT_FIELDS",
    "GraphProgram",
    "GraphSignature",
    "clarify",
    "coerce",
    "configure_lm",
    "make_lm",
]


class GraphSignature(dspy.Signature):  # pyright: ignore[reportUnknownMemberType]
    """Design one runnable Stargraph graph (a multi-file bundle) from a brief.

    A Stargraph graph is a ``State`` model + one or more ``NodeBase`` classes wired
    in a linear pipeline. You emit the pieces; the smith auto-wires ``graph.yaml``
    (you do NOT write it). The gate then loads the bundle into a real graph and RUNS
    it on your ``fixture`` — so the nodes must connect end-to-end, not just pass a
    unit test. Honor every lesson in ``lessons`` and fix every issue in
    ``last_findings``.

    A ``NodeBase`` (``stargraph.nodes.base.NodeBase``) is constructed zero-arg and
    defines ``async def execute(self, state, ctx) -> dict[str, Any]`` returning the
    state fields it writes (merged into the next state, last-write-wins). Nodes run
    in the order you list them in ``node_classes``; a later node reads what an
    earlier node wrote off ``state``.

    OUTPUT FILE CONTRACT (the files are written FLAT into one directory and gated
    together — follow this exactly or the gate rejects a correct graph):

    - ``state_source`` is saved as ``state.py`` and must define exactly ONE
      ``class State(pydantic.BaseModel)`` whose fields are the graph's channels.
      EVERY field must have a default so the run can start from partial inputs.
    - ``nodes_source`` is saved as ``nodes.py`` BESIDE it and must define every node
      class in ``node_classes`` (each a ``NodeBase`` subclass, zero-arg, async
      ``execute``). Put every import the nodes need at module top level. Import
      ``NodeBase`` with ``from stargraph.nodes.base import NodeBase``. Do NOT import
      ``state`` — read fields off the passed-in ``state`` argument
      (``state.<field>``).
    - ``node_classes`` is the ORDERED list of class names defined in ``nodes.py``,
      in execution order (≥2 for a real pipeline). The earliest reads the run
      ``inputs``; each later one reads a field an earlier node wrote.
    - ``test_source`` is saved as ``test_nodes.py``. It MUST import the node classes
      with ``from nodes import <ClassName>`` (NOT a package path) and unit-test each
      node's ``execute`` by driving it with ``asyncio.run`` against a tiny stand-in
      state object. Do NOT ``import pytest`` or import anything unused (the static
      gate rejects unused imports). Plain ``def test_*()`` with ``assert``.
    - ``fixture`` drives the integration run: ``inputs`` is a dict of initial State
      field values (the first node reads these), and ``expects`` is a dict of
      final-State field → expected value (use null to mean "must be populated").
      Pick an ``expects`` that only holds if the nodes actually wired together.
    """

    brief: str = dspy.InputField(desc="what the graph should do")  # pyright: ignore[reportUnknownMemberType]
    lessons: list[str] = dspy.InputField(desc="past failures to avoid")  # pyright: ignore[reportUnknownMemberType]
    last_findings: list[dict[str, Any]] = dspy.InputField(desc="prior attempt findings")  # pyright: ignore[reportUnknownMemberType]
    relevant_context: str = dspy.InputField(  # pyright: ignore[reportUnknownMemberType]
        desc="grounding: NodeBase contract + similar existing graphs + accepted examples + web"
    )

    graph_id: str = dspy.OutputField(desc="a short kebab-case id for the graph")  # pyright: ignore[reportUnknownMemberType]
    node_classes: list[str] = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="ordered node class names defined in nodes.py (execution order, >=2)"
    )
    state_source: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="state.py: one `class State(BaseModel)`, every field defaulted"
    )
    nodes_source: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="nodes.py: the NodeBase classes named in node_classes, imports at top"
    )
    fixture: dict[str, Any] = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="{inputs: {field: value}, expects: {field: value-or-null}}"
    )
    test_source: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="test_nodes.py: import via `from nodes import <ClassName>`; only used imports"
    )


def coerce(pred: Any) -> dict[str, Any]:
    """Normalize a ``dspy.Prediction`` (or any attr-bag) into a plain dict."""
    return {
        "graph_id": str(getattr(pred, "graph_id", "")),
        "node_classes": as_list(getattr(pred, "node_classes", [])),
        "state_source": str(getattr(pred, "state_source", "")),
        "nodes_source": str(getattr(pred, "nodes_source", "")),
        "fixture": as_dict(getattr(pred, "fixture", {})),
        "test_source": str(getattr(pred, "test_source", "")),
    }


class GraphProgram(SmithProgram):
    def __init__(self, *, load_compiled: bool = True) -> None:
        super().__init__(
            signature=GraphSignature,
            coerce=coerce,
            load_compiled_demos=_ledger.load_compiled_demos,
            load_compiled=load_compiled,
        )
