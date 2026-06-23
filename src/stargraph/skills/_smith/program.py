# SPDX-License-Identifier: Apache-2.0
"""SmithProgram - the domain-agnostic DSPy generator shell.

Every smith generates with the same shape: one ``dspy.Predict`` over a
domain signature, fed ``(brief, lessons, last_findings, relevant_context)``,
its raw ``dspy.Prediction`` normalized into a plain dict by a domain ``coerce``.
Compiled few-shot demos (the idea-2 -> idea-1 feedback edge) are loaded at
construction from the smith's ledger.

A smith supplies three things: its ``signature`` (the output fields it emits),
its ``coerce`` (Prediction -> dict), and ``load_compiled_demos`` (its ledger's
demo loader). The generate/forward/demo plumbing is identical everywhere and
lives here.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, cast

import dspy  # pyright: ignore[reportMissingTypeStubs]

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["INPUT_FIELDS", "SmithProgram", "as_dict", "as_list"]

# The predictor's input fields, used when rebuilding compiled demos. Kept to the
# three reflexion inputs (``relevant_context`` is a live signature input but not
# part of a stored demo's input set, so optimized demos stay portable).
INPUT_FIELDS = ("brief", "lessons", "last_findings")


def as_list(value: Any) -> list[str]:
    """Coerce a model output into ``list[str]`` (list, or comma-split string)."""
    if isinstance(value, list):
        return [str(v) for v in cast("list[Any]", value)]
    if isinstance(value, str) and value.strip():
        return [p.strip() for p in value.split(",") if p.strip()]
    return []


def as_dict(value: Any) -> dict[str, Any]:
    """Coerce a model output into ``dict`` (dict, or a JSON object string)."""
    if isinstance(value, dict):
        return cast("dict[str, Any]", value)
    if isinstance(value, str):
        import json

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else {}
    return {}


class SmithProgram(dspy.Module):  # pyright: ignore[reportUnknownMemberType]
    """One ``dspy.Predict`` over a domain signature + reflexion/demo plumbing.

    ``forward`` returns the raw ``dspy.Prediction`` (what an optimizer needs);
    ``generate`` runs ``coerce`` over it to yield the plain dict the build loop
    consumes - so what an optimizer compiles is exactly what the graph runs.
    """

    def __init__(
        self,
        *,
        signature: type[dspy.Signature],
        coerce: Callable[[Any], dict[str, Any]],
        load_compiled_demos: Callable[[], list[dict[str, Any]] | None],
        input_fields: tuple[str, ...] = INPUT_FIELDS,
        load_compiled: bool = True,
    ) -> None:
        super().__init__()  # pyright: ignore[reportUnknownMemberType]
        self._coerce = coerce
        self._load_compiled_demos = load_compiled_demos
        self._input_fields = input_fields
        self.gen = dspy.Predict(signature)  # pyright: ignore[reportUnknownMemberType]
        if load_compiled:
            self._load_demos()

    def _load_demos(self) -> None:
        demos = self._load_compiled_demos()
        if not demos:
            return
        # malformed compiled.json must never break generation
        with contextlib.suppress(TypeError, ValueError):
            built = [dspy.Example(**d).with_inputs(*self._input_fields) for d in demos]  # pyright: ignore[reportUnknownMemberType]
            self.gen.demos = built  # pyright: ignore[reportUnknownMemberType]

    def forward(
        self,
        brief: str,
        lessons: list[str],
        last_findings: list[dict[str, Any]],
        relevant_context: str = "",
    ) -> Any:
        return self.gen(  # pyright: ignore[reportUnknownMemberType]
            brief=brief,
            lessons=lessons,
            last_findings=last_findings,
            relevant_context=relevant_context,
        )

    def generate(
        self,
        brief: str,
        lessons: list[str],
        last_findings: list[dict[str, Any]],
        relevant_context: str = "",
    ) -> dict[str, Any]:
        # Route through __call__ (not forward directly) so the build-time call
        # uses DSPy's traced path — the same one the optimizer drives.
        return self._coerce(
            self(  # pyright: ignore[reportUnknownMemberType]
                brief=brief,
                lessons=lessons,
                last_findings=last_findings,
                relevant_context=relevant_context,
            )
        )
