# SPDX-License-Identifier: Apache-2.0
"""ToolProgram — the DSPy generator for tools, bound to the shared SmithProgram.

The generate/forward/demo plumbing lives in :class:`SmithProgram`; this module
supplies the *tool* signature (the fields a tool generation emits) and ``coerce``
(Prediction → plain dict). LM construction + ``clarify`` are re-exported from the
shared core so callers import them from here.
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
from stargraph.skills._smith.program import INPUT_FIELDS, SmithProgram, as_dict
from stargraph.skills.toolsmith import _ledger

# Re-exported from the shared core so callers keep importing them from here.
__all__ = [
    "DEFAULT_OLLAMA_URL",
    "INPUT_FIELDS",
    "ToolProgram",
    "ToolSignature",
    "clarify",
    "coerce",
    "configure_lm",
    "make_lm",
]


class ToolSignature(dspy.Signature):  # pyright: ignore[reportUnknownMemberType]
    """Write one Stargraph tool and a pytest test for it, from a brief.

    A Stargraph tool is a Python callable decorated with
    ``stargraph.tools.decorator.tool(name=, namespace=, version=, side_effects=,
    ...)``, which binds a ``ToolSpec`` and leaves the callable directly callable.
    Use KEYWORD-ONLY parameters and type-annotate every parameter AND the return
    so the input/output JSON Schemas derive correctly. The function must run on
    the provided ``fixture`` with NO network or filesystem access
    (``side_effects=none``), and its return value MUST validate against the
    declared (or derived) output schema. Honor every lesson in ``lessons`` and
    fix every issue in ``last_findings``.

    OUTPUT FILE CONTRACT (the two files are written FLAT into one directory and
    gated together — follow this exactly or the gate rejects a correct tool):

    - ``tool_source`` is saved as ``tool.py`` and must define exactly ONE
      ``@tool``-decorated callable (plus whatever it imports). Put every import
      the function needs at module top level.
    - ``test_source`` is saved as ``test_tool.py`` BESIDE it. It MUST import the
      tool with ``from tool import <tool_name>`` — NOT from its namespace or
      package path (there is no package; the file is literally ``tool.py``). Do
      NOT ``import pytest`` or import anything you do not use — an unused import
      fails the static gate. Write plain ``def test_*()`` functions with
      ``assert``.
    - ``fixture`` is the keyword arguments the tool is invoked with during gating;
      it must satisfy the input schema and drive one real execution.
    """

    brief: str = dspy.InputField(desc="what the tool should do")  # pyright: ignore[reportUnknownMemberType]
    lessons: list[str] = dspy.InputField(desc="past failures to avoid")  # pyright: ignore[reportUnknownMemberType]
    last_findings: list[dict[str, Any]] = dspy.InputField(desc="prior attempt findings")  # pyright: ignore[reportUnknownMemberType]
    relevant_context: str = dspy.InputField(  # pyright: ignore[reportUnknownMemberType]
        desc="grounding: tool contract + similar existing tools + accepted examples + web research"
    )

    tool_name: str = dspy.OutputField(desc="the tool's snake_case name")  # pyright: ignore[reportUnknownMemberType]
    namespace: str = dspy.OutputField(desc="the tool's namespace")  # pyright: ignore[reportUnknownMemberType]
    fixture: dict[str, Any] = dspy.OutputField(desc="sample kwargs to call the tool with")  # pyright: ignore[reportUnknownMemberType]
    tool_source: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="tool.py: one @tool-decorated callable, all imports at top level"
    )
    test_source: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="test_tool.py: import the tool as `from tool import <tool_name>`; only used imports"
    )


def coerce(pred: Any) -> dict[str, Any]:
    """Normalize a ``dspy.Prediction`` (or any attr-bag) into a plain dict."""
    return {
        "tool_name": str(getattr(pred, "tool_name", "")),
        "namespace": str(getattr(pred, "namespace", "")),
        "fixture": as_dict(getattr(pred, "fixture", {})),
        "tool_source": str(getattr(pred, "tool_source", "")),
        "test_source": str(getattr(pred, "test_source", "")),
    }


class ToolProgram(SmithProgram):
    def __init__(self, *, load_compiled: bool = True) -> None:
        super().__init__(
            signature=ToolSignature,
            coerce=coerce,
            load_compiled_demos=_ledger.load_compiled_demos,
            load_compiled=load_compiled,
        )
