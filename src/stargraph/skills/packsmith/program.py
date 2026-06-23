# SPDX-License-Identifier: Apache-2.0
"""PackProgram — the DSPy generator for rule packs, bound to the shared SmithProgram.

The generate/forward/demo plumbing lives in :class:`SmithProgram`; this module supplies
the *rule pack* signature (the fields a pack generation emits) and ``coerce`` (Prediction
→ plain dict). LM construction + ``clarify`` are re-exported from the shared core so
callers import them from here.
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
from stargraph.skills.packsmith import _ledger

# Re-exported from the shared core so callers keep importing them from here.
__all__ = [
    "DEFAULT_OLLAMA_URL",
    "INPUT_FIELDS",
    "PackProgram",
    "PackSignature",
    "clarify",
    "coerce",
    "configure_lm",
    "make_lm",
]


class PackSignature(dspy.Signature):  # pyright: ignore[reportUnknownMemberType]
    """Write one Stargraph Bosun rule pack (a ``rules.clp`` CLIPS module) from a brief.

    A Bosun rule pack is a governance artifact: CLIPS rules that read an input fact and
    assert a decision/action fact, loaded onto a Fathom engine. You emit the rules; the
    gate loads them into a real engine, asserts the fixture's input fact, FIRES the
    engine, and checks the action — then signs + verifies the assembled pack — so every
    piece must actually work. Honor every lesson in ``lessons`` and fix every issue in
    ``last_findings``.

    ``rules_clp`` is saved as ``rules.clp`` and MUST define, as top-level CLIPS:
    - a ``deftemplate`` for ``input_template`` (the fact the pack reads), and
    - a ``deftemplate`` for ``output_template`` (the decision/action fact it asserts), and
    - one or more ``defrule``\\s that, when an ``input_template`` fact matches a condition,
      ``assert`` an ``output_template`` fact with a ``kind`` slot naming the decision.

    Use real CLIPS syntax. Template/slot names may contain dots. Example shape::

        (deftemplate alert.input
          (slot run_id)
          (slot risk_score (default 0)))
        (deftemplate alert.action
          (slot run_id)
          (slot kind)
          (slot reason))
        (defrule escalate-high-risk
          (alert.input (run_id ?r) (risk_score ?s&:(> ?s 7)))
          =>
          (assert (alert.action (run_id ?r) (kind "escalate") (reason "risk over threshold"))))

    ``flavor`` is ``"governance"`` (decision/gate packs) or ``"routing"`` (destination
    packs); governance is the default.

    ``input_template`` / ``output_template`` are the EXACT deftemplate names you defined.

    ``fixture`` drives the contract run:
    - ``input``: the slots of one ``input_template`` fact to assert (e.g.
      ``{"run_id": "r1", "risk_score": 9}``). Numbers stay numeric; strings are quoted.
    - ``expects``: the slots an asserted ``output_template`` fact MUST match (a subset,
      e.g. ``{"kind": "escalate"}``).

    ``test_source`` is saved as ``test_pack.py`` BESIDE ``rules.clp``. It MUST load the
    rules and assert firing with plain ``def test_*()`` + ``assert`` — copy this shape::

        from pathlib import Path

        from fathom import Engine


        def _engine() -> Engine:
            eng = Engine(default_decision="deny")
            eng._env.load(str(Path(__file__).with_name("rules.clp")))
            return eng


        def test_fires() -> None:
            eng = _engine()
            eng._env.assert_string('(alert.input (run_id "r1") (risk_score 9))')
            eng._env.run()
            facts = [dict(f) for f in eng._env.find_template("alert.action").facts()]
            assert any(f["kind"] == "escalate" for f in facts)

    Do NOT ``import pytest`` or import anything unused (an unused import fails the gate).
    """

    brief: str = dspy.InputField(desc="what the pack should govern/decide")  # pyright: ignore[reportUnknownMemberType]
    lessons: list[str] = dspy.InputField(desc="past failures to avoid")  # pyright: ignore[reportUnknownMemberType]
    last_findings: list[dict[str, Any]] = dspy.InputField(desc="prior attempt findings")  # pyright: ignore[reportUnknownMemberType]
    relevant_context: str = dspy.InputField(  # pyright: ignore[reportUnknownMemberType]
        desc="grounding: a real CLIPS pack + the signing contract + accepted examples + web"
    )

    pack_name: str = dspy.OutputField(desc="a short kebab-case id for the pack")  # pyright: ignore[reportUnknownMemberType]
    flavor: str = dspy.OutputField(desc='"governance" or "routing" (default governance)')  # pyright: ignore[reportUnknownMemberType]
    input_template: str = dspy.OutputField(desc="the input fact's deftemplate name")  # pyright: ignore[reportUnknownMemberType]
    output_template: str = dspy.OutputField(desc="the action fact's deftemplate name")  # pyright: ignore[reportUnknownMemberType]
    rules_clp: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="rules.clp: the two deftemplates + the defrule(s) that assert the action"
    )
    fixture: dict[str, Any] = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="{input: {slot: val, ...}, expects: {slot: val, ...}} — fire input, match action"
    )
    test_source: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="test_pack.py: load rules.clp + assert firing; only used imports"
    )


def coerce(pred: Any) -> dict[str, Any]:
    """Normalize a ``dspy.Prediction`` (or any attr-bag) into a plain dict."""
    return {
        "pack_name": str(getattr(pred, "pack_name", "")),
        "flavor": str(getattr(pred, "flavor", "") or "governance"),
        "input_template": str(getattr(pred, "input_template", "")),
        "output_template": str(getattr(pred, "output_template", "")),
        "rules_clp": str(getattr(pred, "rules_clp", "")),
        "fixture": as_dict(getattr(pred, "fixture", {})),
        "test_source": str(getattr(pred, "test_source", "")),
    }


class PackProgram(SmithProgram):
    def __init__(self, *, load_compiled: bool = True) -> None:
        super().__init__(
            signature=PackSignature,
            coerce=coerce,
            load_compiled_demos=_ledger.load_compiled_demos,
            load_compiled=load_compiled,
        )
