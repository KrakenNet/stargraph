# SPDX-License-Identifier: Apache-2.0
"""``FathomAdapter`` -- engine wrapper unifying provenance, template, and action layers.

Per AC-6.1 the constructor performs **no eager mutation** of ``engine``: template
registration is deferred to :meth:`FathomAdapter.register_stargraph_action_template`.
Per AC-6.2 :meth:`assert_with_provenance` runs three structural checks on every
encoded slot value (NUL bytes, unbalanced parens, identifier-shape regex on
``_origin``/``_source``) before merging sanitized provenance into caller slots
and forwarding to ``engine.assert_fact``.

:meth:`mirror_state` (AC-8.4) introspects a Pydantic ``BaseModel`` instance via
:func:`stargraph.ir._mirror.mirrored_fields` and returns one
:class:`fathom.AssertSpec` per ``Annotated[..., Mirror(...)]`` field. Each spec
uses the resolved template name and a single ``{"value": str(...)}`` slot
(POC convention -- richer state-to-fact mapping is an engine-spec concern).
``ResolvedMirror.lifecycle`` is not propagated: ``fathom.AssertSpec`` has no
lifecycle field; engine-side scheduling is responsible for honoring it.

:meth:`evaluate` runs the engine, queries ``stargraph_action`` facts, and feeds
them to :func:`extract_actions` (FR-4). ``reload_rules`` forwards untouched to
the underlying Fathom hot-reload API (``engine.reload_rules``, see
``fathom/engine.py:812-994``).
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, cast

from stargraph.errors import ValidationError
from stargraph.ir._mirror import mirrored_fields
from stargraph.ir._models import InterruptAction

from ._action import Action, extract_actions
from ._provenance import ProvenanceBundle, _sanitize_provenance_slot
from ._template import register_stargraph_action_template

if TYPE_CHECKING:
    import fathom
    from pydantic import BaseModel

__all__ = ["FathomAdapter"]


_CLIPS_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")
_IDENT_SLOTS = ("_origin", "_source")


def _check_slot_value(slot: str, value: Any) -> None:
    """Apply AC-6.2 structural checks to one encoded slot value.

    Raises :class:`ValidationError` on NUL bytes, unbalanced parens (string
    values), or identifier-shape violations on ``_origin``/``_source``.
    """
    if isinstance(value, str):
        if "\x00" in value:
            raise ValidationError(
                "slot value contains NUL byte",
                slot=slot,
            )
        if value.count("(") != value.count(")"):
            raise ValidationError(
                "slot value has unbalanced parentheses (could escape s-expression)",
                slot=slot,
            )
        if slot in _IDENT_SLOTS and not _CLIPS_IDENT_RE.match(value):
            raise ValidationError(
                "slot value is not a valid CLIPS identifier",
                slot=slot,
                pattern=_CLIPS_IDENT_RE.pattern,
            )


class FathomAdapter:
    """Adapter exposing Stargraph's contract over a :class:`fathom.Engine`.

    The constructor stores ``engine`` without mutation per AC-6.1; callers must
    invoke :meth:`register_stargraph_action_template` before asserting Stargraph
    actions. All assertions flow through :meth:`assert_with_provenance` which
    enforces AC-6.2 sanitization and AC-6.3 slot encoding.
    """

    def __init__(self, engine: fathom.Engine) -> None:
        self.engine = engine
        #: The most recent ``engine.evaluate()`` result, captured by :meth:`evaluate`.
        #: FR-4 routing consumes only ``stargraph_action`` facts and discards the
        #: decision/trace; retaining the full result lets any consumer read the
        #: governance decision (``allow``/``deny``/…) or rule trace. ``None`` until
        #: the first :meth:`evaluate` call.
        self.last_evaluation: fathom.EvaluationResult | None = None
        #: ``rule_id`` of the defrule behind each action returned by the most recent
        #: :meth:`evaluate` call, positionally aligned with that return value. The IR
        #: ``Action`` models are authoring types and carry no runtime provenance, so
        #: the originating rule travels alongside rather than on them. Empty string
        #: for any action whose fact carries no ``rule_id`` (native Fathom decisions,
        #: hand-written ``.clp`` packs that omit the slot).
        self.last_action_rule_ids: list[str] = []

    def register_stargraph_action_template(self) -> None:
        """Register the ``stargraph_action`` deftemplate on the wrapped engine.

        Idempotent (per :func:`_template.register_stargraph_action_template`).
        """
        register_stargraph_action_template(self.engine)

    def assert_with_provenance(
        self,
        template: str,
        slots: dict[str, Any],
        provenance: ProvenanceBundle,
    ) -> None:
        """Encode + sanitize provenance, merge into ``slots``, and assert.

        Encodes each provenance value via :func:`_sanitize_provenance_slot`
        (AC-6.3), runs the three AC-6.2 structural checks on every encoded
        value, then merges the underscore-prefixed provenance slots with the
        caller's ``slots`` (caller wins on conflict) and forwards to
        ``engine.assert_fact(template, combined)``.
        """
        prov_slots: dict[str, Any] = {
            "_origin": _sanitize_provenance_slot(provenance["origin"]),
            "_source": _sanitize_provenance_slot(provenance["source"]),
            "_run_id": _sanitize_provenance_slot(provenance["run_id"]),
            "_step": _sanitize_provenance_slot(provenance["step"]),
            "_confidence": _sanitize_provenance_slot(provenance["confidence"]),
            "_timestamp": _sanitize_provenance_slot(provenance["timestamp"]),
        }
        for slot, value in prov_slots.items():
            _check_slot_value(slot, value)
        combined = {**prov_slots, **slots}
        self.engine.assert_fact(template, combined)

    def evaluate(self) -> list[Action]:
        """Run the engine and translate ``stargraph_action`` facts into typed actions.

        Calls ``engine.evaluate()`` (returns :class:`fathom.EvaluationResult`),
        stashes it on :attr:`last_evaluation` so a caller can read the governance
        decision or rule trace (out of scope for FR-4 routing, which consumes only
        ``stargraph_action`` facts), then queries ``stargraph_action`` facts and
        feeds the slot dicts to :meth:`extract_actions`, and records the
        originating ``rule_id`` of each action in :attr:`last_action_rule_ids`.
        """
        self.last_evaluation = self.engine.evaluate()
        facts = list(self.engine.query("stargraph_action", None))
        # Extract per-fact so each action stays paired with the rule that asserted
        # it; a single fact may yield zero actions (``allow``/``scope``) or one.
        actions: list[Action] = []
        rule_ids: list[str] = []
        for fact in facts:
            produced = self.extract_actions([fact])
            actions.extend(produced)
            rule_ids.extend([str(fact.get("rule_id", ""))] * len(produced))
        self.last_action_rule_ids = rule_ids
        return actions

    def extract_actions(self, facts: list[dict[str, Any]]) -> list[Action]:
        """Translate ``stargraph_action`` fact slot dicts into typed :data:`Action` instances.

        Wraps the module-level :func:`extract_actions` to add the
        ``kind="interrupt"`` branch (AC-14.1, design §4.4): when CLIPS emits a
        ``stargraph_action`` fact with ``kind="interrupt"``, deserialize the
        ``prompt``, ``interrupt_payload``, ``requested_capability``, ``timeout``,
        and ``on_timeout`` slots and emit an :class:`InterruptAction`. The
        ``interrupt_payload`` slot is JSON-decoded if delivered as a string
        (CLIPS lacks a native dict type) or passed through if already a dict.
        Missing optional fields default to ``None``; the required ``prompt``
        propagates a Pydantic validation error if absent. All other ``kind``
        values are dispatched per-fact through the module-level
        :func:`extract_actions`, preserving original fact ordering.
        """
        actions: list[Action] = []
        for fact in facts:
            if fact.get("kind") == "interrupt":
                actions.append(self._build_interrupt_action(fact))
            else:
                actions.extend(extract_actions([fact]))
        return actions

    @staticmethod
    def _build_interrupt_action(fact: dict[str, Any]) -> InterruptAction:
        """Construct an :class:`InterruptAction` from a CLIPS slot dict.

        Required: ``prompt`` (Pydantic raises if missing). Optional defaults to
        ``None``: ``requested_capability``, ``timeout``. ``interrupt_payload``
        defaults to ``{}`` and accepts JSON-string or dict shapes.
        ``on_timeout`` defaults to ``"halt"``.
        """
        payload_raw: Any = fact.get("interrupt_payload", {})
        payload: dict[str, Any]
        if isinstance(payload_raw, str):
            payload = json.loads(payload_raw) if payload_raw else {}
        else:
            payload = payload_raw
        # Coerce empty strings to None so CLIPS-asserted facts (which
        # cannot carry true NULL slot values) round-trip cleanly.
        cap_raw = fact.get("requested_capability")
        cap = cap_raw if cap_raw else None
        timeout_raw = fact.get("timeout")
        if timeout_raw in (None, "", "None"):
            timeout: Any = None
        else:
            try:
                timeout = float(timeout_raw)
            except (TypeError, ValueError):
                timeout = timeout_raw
        kwargs: dict[str, Any] = {
            "prompt": fact.get("prompt"),
            "interrupt_payload": payload,
            "requested_capability": cap,
            "timeout": timeout,
            "on_timeout": fact.get("on_timeout") or "halt",
        }
        return InterruptAction(**kwargs)

    def mirror_state(
        self, state: BaseModel, annotations: dict[str, Any]
    ) -> list[fathom.AssertSpec]:
        """Build ``AssertSpec`` list reflecting ``Mirror``-marked fields of ``state``.

        Walks ``state.__class__`` via :func:`stargraph.ir._mirror.mirrored_fields`
        and, for each :class:`~stargraph.ir._mirror.ResolvedMirror`, produces a
        :class:`fathom.AssertSpec` with ``template=rm.template`` and a single
        ``{"value": str(getattr(state, name))}`` slot (POC convention; full
        state-to-fact mapping is an engine-spec concern). ``lifecycle`` is not
        propagated -- ``AssertSpec`` has no lifecycle field, and scheduling is
        the engine's responsibility. The ``annotations`` parameter is reserved
        for future extension and currently unused.
        """
        import fathom

        del annotations  # reserved for future extension (AC-8.4 POC)
        return [
            fathom.AssertSpec(
                template=rm.template,
                slots={"value": str(getattr(state, name))},
            )
            for name, rm in mirrored_fields(state.__class__).items()
        ]

    def reload_rules(
        self,
        yaml: bytes | str,
        sig: bytes | None = None,
        pubkey: bytes | None = None,
    ) -> tuple[str, str]:
        """Forward to ``engine.reload_rules`` (Fathom v0.3.1+ hot-reload API).

        Returns Fathom's ``(hash_before, hash_after)`` tuple unchanged.
        """
        return cast("Any", self.engine).reload_rules(yaml, signature=sig, pubkey_pem=pubkey)
