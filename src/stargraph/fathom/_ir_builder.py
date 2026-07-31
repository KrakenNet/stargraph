# SPDX-License-Identifier: Apache-2.0
"""Build a live routing :class:`FathomAdapter` from an IR document's rules.

The CLI's GraphRun previously always ran with ``fathom=None`` — the POC
linear driver — so IR ``rules`` only influenced ``--inspect`` simulation
and live runs walked the node list in declaration order.
:func:`build_ir_routing` compiles the IR's routing rules into a live CLIPS
engine so ``stargraph run`` takes the full design §3.1.2 dispatch path
(mirror → assert → evaluate → route). Wiring mirrors
:func:`stargraph.authoring._build_fathom`, generalized from the façade's
single ``sg_route`` template to the IR vocabulary:

* deftemplates: ``node-id`` (symbol ``id`` slot), ``stargraph_action``
  (``kind``/``target``/``reason``), and one template per Mirror-annotated
  state field (string ``value`` slot plus the six provenance slots
  :meth:`FathomAdapter.assert_with_provenance` merges in). Templates are
  loaded via :meth:`fathom.Engine.load_templates` so the engine's fact
  registry can validate asserts and serve
  :meth:`FathomAdapter.evaluate`'s ``stargraph_action`` query.
* one defrule per goto/halt rule, asserting ``stargraph_action`` facts.
* :class:`_IRRoutingFathom` asserts the per-tick ``(node-id (id <current>))``
  fact the rule patterns match on — the engine loop passes the current node
  id in the ``annotations`` of :meth:`FathomAdapter.mirror_state` but never
  asserted it — retracting the previous tick's routing facts first so
  cyclic rules (research → research) get fresh activations instead of
  being refraction-suppressed.

Rules with actions that are not yet routable live (parallel / retry /
assert / retract / interrupt) are skipped with a warning. A skipped or
never-matching rule keeps the loop's linear next-node fallback, so
rule-less graphs and governance-fact rules behave exactly as before.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from stargraph.ir._mirror import mirrored_fields
from stargraph.ir._models import GotoAction, HaltAction

from ._adapter import FathomAdapter

if TYPE_CHECKING:
    import fathom
    from pydantic import BaseModel

    from stargraph.ir._models import IRDocument, RuleSpec

__all__ = ["build_ir_routing"]

logger = logging.getLogger(__name__)

#: Slot names ``FathomAdapter.assert_with_provenance`` merges into every
#: mirrored fact; each mirror template must declare them.
_PROVENANCE_SLOTS: tuple[str, ...] = (
    "_origin",
    "_source",
    "_run_id",
    "_step",
    "_confidence",
    "_timestamp",
)


def _clips_string(value: str) -> str:
    """Escape ``value`` for interpolation inside a CLIPS string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _defrule(rule: RuleSpec) -> str | None:
    """Compile one IR rule to a defrule string, or ``None`` if not live-routable."""
    rhs: list[str] = []
    for action in rule.then:
        if isinstance(action, GotoAction):
            rhs.append(
                f'(assert (stargraph_action (kind goto) (target "{_clips_string(action.target)}")))'
            )
        elif isinstance(action, HaltAction):
            rhs.append(
                f'(assert (stargraph_action (kind halt) (reason "{_clips_string(action.reason)}")))'
            )
        else:
            logger.warning(
                "rule %r: action kind %r is not routable live yet; "
                "skipping rule (falls back to linear next-node)",
                rule.id,
                action.kind,
            )
            return None
    return f"(defrule {rule.id} {rule.when} => {' '.join(rhs)})"


def _clips_name(template: str) -> str:
    """CLIPS-safe encoding of a dotted fact name (``stargraph.tool-call``
    -> ``stargraph-tool-call``).

    Fathom restricts template identifiers to ``[A-Za-z_][A-Za-z0-9_-]*``,
    so the dotted names of the documented fact vocabulary cannot register
    verbatim. The routing engine stores and asserts them dot-mangled;
    rule ``when`` patterns must use the mangled spelling.
    """
    return template.replace(".", "-")


class _IRRoutingFathom(FathomAdapter):
    """Assert the current node-id fact per tick; retract stale routing facts."""

    def __init__(self, engine: fathom.Engine, mirror_templates: list[str]) -> None:
        super().__init__(engine)
        self._mirror_templates = mirror_templates

    def assert_with_provenance(
        self,
        template: str,
        slots: dict[str, Any],
        provenance: Any,
    ) -> None:
        # Runtime emitters (``runtime.tool_exec``) use the documented dotted
        # names; translate to the registered CLIPS-safe spelling.
        super().assert_with_provenance(_clips_name(template), slots, provenance)

    def mirror_state(
        self, state: BaseModel, annotations: dict[str, Any]
    ) -> list[fathom.AssertSpec]:
        self.engine.retract("node-id")
        for template in self._mirror_templates:
            self.engine.retract(_clips_name(template))
        self.engine.assert_fact("node-id", {"id": str(annotations.get("node_id", ""))})
        return super().mirror_state(state, annotations)


def build_ir_routing(ir: IRDocument, state_cls: type[BaseModel]) -> FathomAdapter | None:
    """Compile ``ir.rules`` into a routing adapter for :class:`GraphRun`.

    Returns ``None`` when no rule is live-routable (including rule-less
    IRs) so callers keep the exact pre-existing linear behavior by passing
    the result straight to ``GraphRun(fathom=...)``.
    """
    import fathom

    defrules = [d for d in (_defrule(rule) for rule in ir.rules) if d is not None]
    if not defrules:
        return None

    mirror_templates = sorted({rm.template for rm in mirrored_fields(state_cls).values()})
    prov_slots = [{"name": name, "type": "string"} for name in _PROVENANCE_SLOTS]
    templates: list[dict[str, Any]] = [
        {"name": "node-id", "slots": [{"name": "id", "type": "symbol"}]},
        {
            "name": "stargraph_action",
            "slots": [
                {"name": "kind", "type": "symbol"},
                {"name": "target", "type": "string"},
                {"name": "reason", "type": "string"},
            ],
        },
        # Tool-execution runtime vocabulary (design §3.4.4 steps 4/8/9):
        # ``runtime.tool_exec`` emits these through the adapter for every
        # ``kind: tool`` node, so the routing engine must know the shapes
        # even when no rule matches them. Registered dot-mangled (see
        # :func:`_clips_name`); rules match e.g. ``(stargraph-tool-result ...)``.
        {
            "name": _clips_name("stargraph.tool-call"),
            "slots": [
                {"name": "tool_id", "type": "string"},
                {"name": "args_count", "type": "integer"},
                *prov_slots,
            ],
        },
        {
            "name": _clips_name("stargraph.tool-result"),
            "slots": [
                {"name": "tool_id", "type": "string"},
                {"name": "fields", "type": "integer"},
                *prov_slots,
            ],
        },
        {
            "name": _clips_name("stargraph.tokens-used"),
            "slots": [
                {"name": "tool_id", "type": "string"},
                {"name": "total", "type": "integer"},
                *prov_slots,
            ],
        },
        *(
            {
                "name": _clips_name(template),
                "slots": [{"name": "value", "type": "string"}, *prov_slots],
            }
            for template in mirror_templates
        ),
    ]

    templates_path = Path(tempfile.mkdtemp(prefix="stargraph_ir_fathom_")) / "templates.yaml"
    templates_path.write_text(yaml.safe_dump({"templates": templates}), encoding="utf-8")
    engine = fathom.Engine(default_decision="allow")
    engine.load_templates(str(templates_path))
    for defrule in defrules:
        engine.load_clips_function(defrule)
    # (reset) asserts ``(initial-fact)`` so canonical first-hop rules fire;
    # deftemplates and defrules survive a CLIPS reset.
    engine.reset()
    return _IRRoutingFathom(engine, mirror_templates)
