# SPDX-License-Identifier: Apache-2.0
"""``Mirror`` boundary-sync marker for ``Annotated[T, Mirror(...)]`` fields.

:class:`Mirror` is a frozen dataclass appended to a Pydantic field's
``Annotated[...]`` chain (US-8, FR-13, FR-14). It marks state fields that
should be mirrored into CLIPS at node/step/run boundaries with an optional
template name override and a lifecycle tag.

The ``__get_pydantic_json_schema__`` hook makes the marker self-describing in
JSON Schema (``stargraph_mirror``, ``stargraph_mirror_template``,
``stargraph_mirror_lifecycle``) so the IR JSON Schema -> Pydantic loader can
re-inject :class:`Mirror` annotations on the round-trip side (AC-8.5).

:class:`ResolvedMirror` is the post-resolution sibling: ``template`` is
non-optional. :func:`mirrored_fields` walks ``model.model_fields`` and
resolves the FR-13 default ("template = field name when not explicit"),
returning one :class:`ResolvedMirror` per mirrored field. Consumers
(``FathomAdapter.mirror_state``, stargraph-engine state-sync) only ever see
resolved templates -- the field-name fallback is a runtime concept and is
deliberately excluded from JSON Schema (only explicit templates are emitted).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, get_args

if TYPE_CHECKING:
    from pydantic import BaseModel
    from pydantic.annotated_handlers import GetJsonSchemaHandler
    from pydantic.json_schema import JsonSchemaValue
    from pydantic_core import CoreSchema

__all__ = ["Lifecycle", "Mirror", "ResolvedMirror", "mirrored_fields"]

Lifecycle = Literal["run", "step", "pinned"]

_LIFECYCLES: tuple[str, ...] = get_args(Lifecycle)


@dataclass(frozen=True, slots=True)
class Mirror:
    """``Annotated[T, Mirror(template=..., lifecycle=...)]`` -- boundary state-sync marker.

    ``template=None`` (default) is the opt-in marker only; consumers resolve the
    effective template name via :func:`mirrored_fields`, which falls back to
    the field name (FR-13). JSON Schema only emits ``stargraph_mirror_template``
    when an explicit template was given -- JSON Schema is generic and the
    field-name fallback is a runtime concept.
    """

    template: str | None = None
    lifecycle: Lifecycle = "run"

    def __post_init__(self) -> None:
        if self.lifecycle not in _LIFECYCLES:
            raise TypeError(f"lifecycle must be one of {_LIFECYCLES}, got {self.lifecycle!r}")

    def __get_pydantic_json_schema__(
        self,
        schema: CoreSchema,
        handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        js = handler(schema)
        js["stargraph_mirror"] = True
        if self.template is not None:  # FR-13: only explicit names go to JSON Schema
            js["stargraph_mirror_template"] = self.template
        js["stargraph_mirror_lifecycle"] = self.lifecycle
        return js


@dataclass(frozen=True, slots=True)
class ResolvedMirror:
    """Post-resolution sibling of :class:`Mirror` with non-optional ``template``.

    Returned by :func:`mirrored_fields` after applying the FR-13 default
    (template = field name when ``Mirror.template is None``). Distinguishes
    "extracted from a model with field-name resolved" from the raw annotation
    marker semantics of :class:`Mirror` itself.
    """

    template: str
    lifecycle: Lifecycle


def mirrored_fields(model: type[BaseModel]) -> dict[str, ResolvedMirror]:
    """Scan ``model.model_fields`` for ``Annotated[..., Mirror(...)]``.

    Resolves ``Mirror.template=None`` to the field name (FR-13). Returns a
    mapping of field name -> :class:`ResolvedMirror` with ``template`` always
    populated. Fields without a :class:`Mirror` marker are omitted.
    """
    out: dict[str, ResolvedMirror] = {}
    for name, info in model.model_fields.items():
        for m in info.metadata:
            if isinstance(m, Mirror):
                template = m.template if m.template is not None else name
                out[name] = ResolvedMirror(template=template, lifecycle=m.lifecycle)
                break
    return out
