# SPDX-License-Identifier: Apache-2.0
"""SkillProgram — the DSPy generator for skill bundles, bound to SmithProgram.

The generate/forward/demo plumbing lives in :class:`SmithProgram`; this module
supplies the *skill* signature (the fields a skill generation emits) and ``coerce``
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
from stargraph.skills._smith.program import INPUT_FIELDS, SmithProgram, as_dict, as_list
from stargraph.skills.skillsmith import _ledger

# Re-exported from the shared core so callers keep importing them from here.
__all__ = [
    "DEFAULT_OLLAMA_URL",
    "INPUT_FIELDS",
    "SkillProgram",
    "SkillSignature",
    "clarify",
    "coerce",
    "configure_lm",
    "make_lm",
]


class SkillSignature(dspy.Signature):  # pyright: ignore[reportUnknownMemberType]
    """Design one registerable Stargraph skill (a multi-file bundle) from a brief.

    A Stargraph skill is a runnable subgraph (a ``State`` model + ``NodeBase``
    classes wired in a linear pipeline) plus a manifest declaring how it registers:
    its ``kind``, a one-line ``description``, the capabilities it ``requires``, and
    (for an agent) a ``system_prompt``. You emit the pieces; the smith auto-wires
    both ``graph.yaml`` and ``manifest.yaml`` (you do NOT write them). The gate then
    LOADS the subgraph into a real graph and RUNS it on your ``fixture`` AND
    constructs the ``Skill`` manifest — so the nodes must connect end-to-end and the
    manifest must be a valid, registerable skill. Honor every lesson in ``lessons``
    and fix every issue in ``last_findings``.

    A ``NodeBase`` (``stargraph.nodes.base.NodeBase``) is constructed zero-arg and
    defines ``async def execute(self, state, ctx) -> dict[str, Any]`` returning the
    state fields it writes (merged into the next state, last-write-wins). Nodes run
    in the order you list them in ``node_classes``; a later node reads what an
    earlier node wrote off ``state``.

    OUTPUT FILE CONTRACT (the files are written FLAT into one directory and gated
    together — follow this exactly or the gate rejects a correct skill):

    - ``state_source`` is saved as ``state.py`` and must define exactly ONE
      ``class State(pydantic.BaseModel)`` whose fields are the skill's output
      channels. EVERY field must have a default. NEVER type a field as ``set`` /
      ``set[...]`` (replay-safe state must use ``frozenset``); use ``list`` for
      collections.
    - ``nodes_source`` is saved as ``nodes.py`` BESIDE it and must define every node
      class in ``node_classes`` (each a ``NodeBase`` subclass, zero-arg, async
      ``execute``). Put every import at module top level. Import ``NodeBase`` with
      ``from stargraph.nodes.base import NodeBase``. Do NOT import ``state`` — read
      fields off the passed-in ``state`` argument (``state.<field>``).
    - ``node_classes`` is the ORDERED list of class names defined in ``nodes.py``,
      in execution order (≥2 for a real pipeline).
    - ``test_source`` is saved as ``test_nodes.py``. It MUST import the node classes
      with ``from nodes import <ClassName>`` (NOT a package path) and unit-test each
      node's ``execute`` by driving it with ``asyncio.run`` against a tiny stand-in
      state object. Do NOT ``import pytest`` or import anything unused. Plain
      ``def test_*()`` with ``assert``.
    - ``skill_name`` is a short kebab-case id for the skill.
    - ``kind`` is exactly one of: ``agent``, ``workflow``, ``utility``.
    - ``description`` is one line describing what the skill does.
    - ``requires`` is a list of capability strings the skill needs (e.g.
      ``["llm.generate"]``); use ``[]`` if none.
    - ``system_prompt`` is the agent instruction (only for ``kind=agent``; ``""``
      otherwise).
    - ``fixture`` drives the run: ``inputs`` is a dict of initial State field values
      and ``expects`` is a dict of final-State field → expected value (null ⇒ "must
      be populated"). Pick an ``expects`` that only holds if the nodes wired together.
    """

    brief: str = dspy.InputField(desc="what the skill should do")  # pyright: ignore[reportUnknownMemberType]
    lessons: list[str] = dspy.InputField(desc="past failures to avoid")  # pyright: ignore[reportUnknownMemberType]
    last_findings: list[dict[str, Any]] = dspy.InputField(desc="prior attempt findings")  # pyright: ignore[reportUnknownMemberType]
    relevant_context: str = dspy.InputField(  # pyright: ignore[reportUnknownMemberType]
        desc="grounding: Skill + NodeBase contracts + similar skills + accepted examples + web"
    )

    skill_name: str = dspy.OutputField(desc="a short kebab-case id for the skill")  # pyright: ignore[reportUnknownMemberType]
    kind: str = dspy.OutputField(desc="one of: agent, workflow, utility")  # pyright: ignore[reportUnknownMemberType]
    description: str = dspy.OutputField(desc="one line describing the skill")  # pyright: ignore[reportUnknownMemberType]
    node_classes: list[str] = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="ordered node class names defined in nodes.py (execution order, >=2)"
    )
    state_source: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="state.py: one `class State(BaseModel)`, every field defaulted, no set fields"
    )
    nodes_source: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="nodes.py: the NodeBase classes named in node_classes, imports at top"
    )
    requires: list[str] = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="capability strings the skill needs, or [] if none"
    )
    system_prompt: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="agent instruction (only for kind=agent; empty string otherwise)"
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
        "skill_name": str(getattr(pred, "skill_name", "")),
        "kind": str(getattr(pred, "kind", "")),
        "description": str(getattr(pred, "description", "")),
        "node_classes": as_list(getattr(pred, "node_classes", [])),
        "state_source": str(getattr(pred, "state_source", "")),
        "nodes_source": str(getattr(pred, "nodes_source", "")),
        "requires": as_list(getattr(pred, "requires", [])),
        "system_prompt": str(getattr(pred, "system_prompt", "")),
        "fixture": as_dict(getattr(pred, "fixture", {})),
        "test_source": str(getattr(pred, "test_source", "")),
    }


class SkillProgram(SmithProgram):
    def __init__(self, *, load_compiled: bool = True) -> None:
        super().__init__(
            signature=SkillSignature,
            coerce=coerce,
            load_compiled_demos=_ledger.load_compiled_demos,
            load_compiled=load_compiled,
        )
