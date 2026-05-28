# SPDX-License-Identifier: Apache-2.0
"""Compile IRDocument inline ``rules:`` to CLIPS ``defrule`` constructs.

The Harbor IR carries routing rules as ``RuleSpec`` (``when:`` + ``then:``)
under ``IRDocument.rules``. The runtime evaluator (``dispatch.py``) expects
those rules to be live in the wrapped Fathom Engine as CLIPS ``defrule``
constructs that assert ``harbor_action`` facts on RHS.

Nothing in ``harbor.bosun`` / ``harbor.fathom`` performs that translation
today — Bosun packs ship pre-written ``rules.clp`` files. This module is
the missing translator for graphs (cve-rem, sdw) whose primary routing
lives inline in ``harbor.yaml`` rather than in a packaged ``rules.clp``.

The compiler accepts only the action verbs cve-rem's inline rules use
(``goto``, ``halt``, ``parallel``, ``interrupt``). ``assert`` / ``retract``
are not implemented — they belong in Bosun packs, not the routing topology.
"""

from __future__ import annotations

from typing import Any, Iterable

from harbor.ir._models import (
    GotoAction,
    HaltAction,
    InterruptAction,
    ParallelAction,
    RuleSpec,
)


__all__ = ["compile_rules_to_clips", "STATE_FIELDS", "INTEGER_STATE_FIELDS",
           "STATE_DEFTEMPLATE", "NODE_ID_DEFTEMPLATE", "RESPONSE_DEFTEMPLATE"]


# Routing-relevant state fields referenced by harbor.yaml inline rule
# predicates of the form ``(state (FIELD VALUE))``. Mirrored into CLIPS
# as one ``state`` deftemplate with all slots declared.
STATE_FIELDS: tuple[str, ...] = (
    "critic_attempt",
    "critic_verdict",
    "disposition",
    "fleet_passed",
    "halt_new_active",
    "plan_quarantined",
    "rollback_triggered",
    "sandbox_prod_divergence",
    "sandbox_runtime",
    "sandbox_status",
    "skip_sandbox",
    "source_trust",
    "ssvc_tier",
    "template_lookup_hit",
    "unpatchable_disposition",
    "untrusted_text_influenced",
    "validation_passed",
    "verify_outcome",
)


# Provenance slots auto-merged by ``FathomAdapter.assert_with_provenance``.
# Every deftemplate that receives mirror-state asserts must declare them.
_PROV_SLOTS = (
    "(slot _origin)",
    "(slot _source)",
    "(slot _run_id)",
    "(slot _step)",
    "(slot _confidence)",
    "(slot _timestamp)",
)


def _build_deftemplate(name: str, slots: Iterable[str]) -> str:
    body = "\n  ".join(list(_PROV_SLOTS) + list(slots))
    return f"(deftemplate {name}\n  {body}\n)"


# Routing slots typed SYMBOL: rule predicates like
# ``(state (source_trust trusted))`` match symbols only — string values
# (CLIPS-side) never match. ``id`` / ``decision`` likewise read as
# symbols by their consumers.
#
# Exception: ``critic_attempt`` is used as an integer counter (rules
# match ``(critic_attempt 1 | 2)``), so it stays INTEGER.
INTEGER_STATE_FIELDS: frozenset[str] = frozenset({"critic_attempt"})


def _state_slot_decl(field: str) -> str:
    if field in INTEGER_STATE_FIELDS:
        return f"(slot {field} (type INTEGER))"
    return f"(slot {field} (type SYMBOL))"


STATE_DEFTEMPLATE: str = _build_deftemplate(
    "state",
    [_state_slot_decl(f) for f in STATE_FIELDS],
)

NODE_ID_DEFTEMPLATE: str = _build_deftemplate(
    "node-id",
    ["(slot id (type SYMBOL))"],
)

RESPONSE_DEFTEMPLATE: str = _build_deftemplate(
    "response",
    ["(slot decision (type SYMBOL))", "(slot caller (type SYMBOL))"],
)


def _escape_clips_string(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _emit_goto(action: GotoAction, rule_id: str) -> str:
    target = _escape_clips_string(action.target)
    rid = _escape_clips_string(rule_id)
    return (
        f'(assert (harbor_action (kind goto) (target "{target}") '
        f'(rule_id "{rid}")))'
    )


def _emit_halt(action: HaltAction, rule_id: str) -> str:
    reason = _escape_clips_string(action.reason)
    rid = _escape_clips_string(rule_id)
    return (
        f'(assert (harbor_action (kind halt) (reason "{reason}") '
        f'(rule_id "{rid}")))'
    )


def _emit_parallel(action: ParallelAction, rule_id: str) -> str:
    rid = _escape_clips_string(rule_id)
    join = _escape_clips_string(action.join or "")
    strategy = action.strategy or "all"
    targets_clips = " ".join(f'"{_escape_clips_string(t)}"' for t in action.targets)
    return (
        f'(assert (harbor_action (kind parallel) (targets {targets_clips}) '
        f'(join "{join}") (strategy {strategy}) (rule_id "{rid}")))'
    )


def _emit_interrupt(action: InterruptAction, rule_id: str) -> str:
    """Emit an ``interrupt`` harbor_action with prompt/payload/etc. as named slots."""
    import json
    rid = _escape_clips_string(rule_id)
    prompt = _escape_clips_string(action.prompt)
    payload_json = _escape_clips_string(json.dumps(action.interrupt_payload or {}))
    cap = _escape_clips_string(action.requested_capability or "")
    timeout = "" if action.timeout is None else str(action.timeout.total_seconds())
    on_to = _escape_clips_string(action.on_timeout or "halt")
    return (
        f'(assert (harbor_action (kind interrupt) (prompt "{prompt}") '
        f'(interrupt_payload "{payload_json}") (requested_capability "{cap}") '
        f'(timeout "{timeout}") (on_timeout "{on_to}") (rule_id "{rid}")))'
    )


_EMITTERS: dict[type, Any] = {
    GotoAction: _emit_goto,
    HaltAction: _emit_halt,
    ParallelAction: _emit_parallel,
    InterruptAction: _emit_interrupt,
}


def _compile_one(rule: RuleSpec) -> str:
    """Translate one ``RuleSpec`` to a CLIPS ``defrule`` source string.

    ``rule.when`` is already CLIPS pattern syntax (the IR field is
    free-form CLIPS-shaped text). ``rule.then`` actions are rendered as
    ``(assert (harbor_action ...))`` lines on RHS.

    Rules with no supported actions emit a no-op defrule body so they
    still compile (CLIPS rejects empty RHS).
    """
    body_lines: list[str] = []
    for action in rule.then:
        emitter = _EMITTERS.get(type(action))
        if emitter is None:
            continue
        body_lines.append("  " + emitter(action, rule.id))
    if not body_lines:
        # CLIPS requires at least one RHS expression. Emit a no-op
        # printout that won't pollute working memory.
        body_lines.append('  (printout t "")')
    return (
        f"(defrule {rule.id}\n"
        f"  {rule.when}\n"
        f"  =>\n"
        + "\n".join(body_lines) + "\n"
        ")"
    )


def compile_rules_to_clips(rules: Iterable[RuleSpec]) -> list[str]:
    """Translate an iterable of ``RuleSpec`` to CLIPS ``defrule`` strings."""
    return [_compile_one(r) for r in rules]
