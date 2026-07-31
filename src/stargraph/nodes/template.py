# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes.template -- ``kind: template``, deterministic state formatting.

Renders a ``str.format``-style template over the run state's fields and
writes the result to one output field. Zero LM, zero I/O -- pure state
transform, so it replays identically. The load-bearing use is feedback
re-injection in rule-routed loops: a judge writes ``rationale``, a
template node re-renders ``"{task}\\n\\nFix this feedback: {rationale}"``
into the field the generator reads, and the goto rule targets the
template node -- the retry sees *why* it failed instead of re-rolling
the dice.

Placeholders are ``{field}`` names resolved via ``getattr(state, ...)``;
a placeholder naming a missing field fails loudly at run time (FR-6).
Attribute/index sub-paths (``{a.b}`` / ``{a[0]}``) are rejected at build
time -- fields only, so the read surface stays auditable.
"""

from __future__ import annotations

import string
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel as _PydanticBaseModel
from pydantic import ConfigDict
from pydantic import ValidationError as _PydanticValidationError

from stargraph.errors import IRValidationError, StargraphRuntimeError
from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from pydantic import BaseModel

    from stargraph.ir._models import NodeSpec

__all__ = ["TemplateNode", "TemplateNodeConfig", "template_node_from_config"]


class TemplateNodeConfig(_PydanticBaseModel):
    """``NodeSpec.config`` schema for ``kind: template`` (extra keys rejected)."""

    model_config = ConfigDict(extra="forbid")

    template: str
    out: str


def _template_fields(template: str) -> list[str]:
    """Placeholder field names in ``template`` (build-time validation).

    Rejects positional placeholders (``{}`` / ``{0}``) and sub-paths
    (``{a.b}`` / ``{a[0]}``); conversions/format-specs (``{x!r}`` /
    ``{x:>8}``) are allowed -- they format the read value without
    widening the read surface.
    """
    fields: list[str] = []
    for _literal, field_name, _spec, _conv in string.Formatter().parse(template):
        if field_name is None:
            continue
        if not field_name.isidentifier():
            raise IRValidationError(
                f"template placeholder {{{field_name}}} must be a bare state "
                "field name (no positional, dotted, or indexed placeholders)"
            )
        fields.append(field_name)
    return fields


class TemplateNode(NodeBase):
    """Render ``config.template`` from state fields into ``config.out``."""

    def __init__(self, *, config: TemplateNodeConfig, fields: list[str]) -> None:
        self.config = config
        self._fields = fields

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del ctx
        values: dict[str, Any] = {}
        for field in self._fields:
            if not hasattr(state, field):
                raise StargraphRuntimeError(
                    f"template node reads state field {field!r} which does "
                    "not exist on the run state",
                )
            values[field] = getattr(state, field)
        return {self.config.out: self.config.template.format(**values)}


def template_node_from_config(spec: NodeSpec) -> TemplateNode:
    """Build a :class:`TemplateNode` from ``NodeSpec.config`` (``kind: template``)."""
    try:
        cfg = TemplateNodeConfig.model_validate(spec.config)
    except _PydanticValidationError as e:
        raise IRValidationError(f"template node {spec.id!r}: invalid config: {e}") from e
    if not cfg.out.isidentifier():
        raise IRValidationError(
            f"template node {spec.id!r}: out {cfg.out!r} must be a valid "
            "identifier (it names the output state field)"
        )
    try:
        fields = _template_fields(cfg.template)
    except IRValidationError as e:
        raise IRValidationError(f"template node {spec.id!r}: {e.message}") from e
    return TemplateNode(config=cfg, fields=fields)
