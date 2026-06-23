# SPDX-License-Identifier: Apache-2.0
"""TriggerProgram — the DSPy generator for triggers, bound to SmithProgram.

The generate/forward/demo plumbing lives in :class:`SmithProgram`; this module
supplies the *trigger* signature (the fields a trigger generation emits) and
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
from stargraph.skills._smith.program import INPUT_FIELDS, SmithProgram, as_dict
from stargraph.skills.triggersmith import _ledger

# Re-exported from the shared core so callers keep importing them from here.
__all__ = [
    "DEFAULT_OLLAMA_URL",
    "INPUT_FIELDS",
    "TriggerProgram",
    "TriggerSignature",
    "clarify",
    "coerce",
    "configure_lm",
    "make_lm",
]


class TriggerSignature(dspy.Signature):  # pyright: ignore[reportUnknownMemberType]
    """Write one Stargraph MANUAL trigger and a pytest test for it, from a brief.

    A Stargraph trigger is a plain Python class implementing the
    ``stargraph.triggers.Trigger`` lifecycle: ``init(self, deps)`` /
    ``start(self)`` / ``stop(self)`` / ``routes(self)``. The MANUAL variant is
    synchronous and offline (no clock, no socket). In addition to the lifecycle it
    exposes a public ``enqueue(self, graph_id, params, idempotency_key=None)`` that
    DELEGATES to ``deps['scheduler'].enqueue(graph_id=, params=, idempotency_key=)``
    and RETURNS the resulting handle's ``run_id``.

    Hard requirements (the contract gate enforces all of these — follow exactly or
    it rejects a correct-looking trigger):

    - Define exactly ONE zero-arg-constructible trigger class. ``__init__`` takes
      no required args; stash ``None`` for the scheduler until ``init``.
    - ``init(self, deps)`` must capture ``deps['scheduler']`` and RAISE
      ``stargraph.errors.StargraphRuntimeError`` when it is missing (a no-op init
      fails the gate).
    - ``enqueue`` must call ``self._scheduler.enqueue(graph_id=graph_id,
      params=params, idempotency_key=idempotency_key)`` and ``return
      handle.run_id`` — do NOT fabricate a run_id; return what the scheduler gave.
    - ``start``/``stop`` are idempotent no-ops; ``routes`` returns ``[]``.

    Honor every lesson in ``lessons`` and fix every issue in ``last_findings``.

    OUTPUT FILE CONTRACT (the two files are written FLAT into one directory and
    gated together):

    - ``trigger_source`` is saved as ``trigger.py`` and must define exactly ONE
      trigger class (plus whatever it imports). Put every import at module top
      level.
    - ``test_source`` is saved as ``test_trigger.py`` BESIDE it. It MUST import the
      class with ``from trigger import <ClassName>`` — NOT from any package path
      (there is no package; the file is literally ``trigger.py``). Do NOT
      ``import pytest`` or import anything you do not use — an unused import fails
      the static gate. Write plain ``def test_*()`` functions with ``assert``,
      using a tiny in-test recording scheduler stub.
    - ``fixture`` is ``{"graph_id": <str>, "params": <dict>}``: the run the
      contract tier enqueues to prove delegation.
    """

    brief: str = dspy.InputField(desc="what the trigger should do")  # pyright: ignore[reportUnknownMemberType]
    lessons: list[str] = dspy.InputField(desc="past failures to avoid")  # pyright: ignore[reportUnknownMemberType]
    last_findings: list[dict[str, Any]] = dspy.InputField(desc="prior attempt findings")  # pyright: ignore[reportUnknownMemberType]
    relevant_context: str = dspy.InputField(  # pyright: ignore[reportUnknownMemberType]
        desc="grounding: trigger contract + similar triggers + accepted examples + web research"
    )

    class_name: str = dspy.OutputField(desc="the trigger class's PascalCase name")  # pyright: ignore[reportUnknownMemberType]
    fixture: dict[str, Any] = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="{'graph_id': str, 'params': dict} to enqueue during gating"
    )
    trigger_source: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="trigger.py: one trigger class, all imports at top level"
    )
    test_source: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="test_trigger.py: import as `from trigger import <ClassName>`; only used imports"
    )


def coerce(pred: Any) -> dict[str, Any]:
    """Normalize a ``dspy.Prediction`` (or any attr-bag) into a plain dict."""
    return {
        "class_name": str(getattr(pred, "class_name", "")),
        "fixture": as_dict(getattr(pred, "fixture", {})),
        "trigger_source": str(getattr(pred, "trigger_source", "")),
        "test_source": str(getattr(pred, "test_source", "")),
    }


class TriggerProgram(SmithProgram):
    def __init__(self, *, load_compiled: bool = True) -> None:
        super().__init__(
            signature=TriggerSignature,
            coerce=coerce,
            load_compiled_demos=_ledger.load_compiled_demos,
            load_compiled=load_compiled,
        )
