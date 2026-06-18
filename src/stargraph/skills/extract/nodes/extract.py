# SPDX-License-Identifier: Apache-2.0
"""Extract — unstructured text → a validated set of named fields.

A ``utility`` skill: pure transformation, no external side effects beyond the
LLM read. The model call sits behind the injectable ``extractor`` seam (the
nodesmith ``Build._program`` pattern), so the node's value-add — pulling the
requested fields out, flagging the ones the model failed to produce, and
deciding whether the extraction is complete — is exercised in tests with no
live model. ``_default_extractor`` is the production DSPy path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    # (text, target_fields) -> raw {field: value} the model produced.
    Extractor = Callable[[str, list[str]], dict[str, Any]]


class Extract(NodeBase):
    def __init__(self, extractor: Extractor | None = None) -> None:
        self._extractor = extractor or _default_extractor

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        del ctx  # no per-run context needed for a pure transform
        text = str(getattr(state, "text", "") or "")
        target = list(getattr(state, "target_fields", []) or [])
        if not text.strip():
            raise ValueError("text is required: nothing to extract from")
        if not target:
            raise ValueError("target_fields is required: name at least one field to extract")

        raw = self._extractor(text, target)
        # Keep only the requested fields; a value is "present" if it is truthy.
        fields = {k: raw.get(k) for k in target if raw.get(k) not in (None, "")}
        missing = [k for k in target if k not in fields]
        return {"fields": fields, "missing": missing, "valid": not missing}


def _default_extractor(text: str, target_fields: list[str]) -> dict[str, Any]:
    """Production extractor — one DSPy call returning the requested fields.

    Imported lazily so the skill (and its tests, which inject a stub) never pull
    in DSPy unless a real extraction runs.
    """
    import dspy  # pyright: ignore[reportMissingTypeStubs]

    predictor = dspy.Predict("text, target_fields -> fields")  # pyright: ignore[reportUnknownMemberType]
    result = predictor(text=text, target_fields=", ".join(target_fields))  # pyright: ignore[reportUnknownVariableType]
    raw = getattr(result, "fields", {})
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
