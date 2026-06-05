# SPDX-License-Identifier: Apache-2.0
"""cve-rem Fathom adapter — mirrors flat state + per-step node-id fact.

``FathomAdapter.mirror_state`` ships with the base class only emits
``AssertSpec`` for ``Annotated[..., Mirror(...)]`` fields. The cve-rem
state model carries no Mirror annotations (would require touching every
sub-state in the schema). This subclass synthesizes the asserts from a
fixed allow-list of routing-relevant fields plus the current node id
(passed through ``annotations["node_id"]``).

The asserts route through ``assert_with_provenance`` so the standard
``_origin`` / ``_source`` / ``_run_id`` / ``_step`` / ``_confidence`` /
``_timestamp`` slots are merged. The matching ``deftemplate state`` and
``deftemplate node-id`` declarations live in :mod:`_rule_compiler`.

Public entry points:

* :class:`CveRemFathomAdapter` — drop-in replacement for
  ``harbor.fathom.FathomAdapter``. Constructor accepts the wrapped
  ``fathom.Engine``; routing scaffolding (templates + compiled inline
  rules from ``harbor.yaml``) is installed lazily on first
  :meth:`install_routing_scaffold` call.

* :func:`build_cve_rem_fathom` — convenience constructor that returns
  ``(engine, adapter)`` with all templates + Bosun packs + inline rules
  pre-loaded for a given ``IRDocument`` list.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from harbor.fathom._adapter import FathomAdapter
from harbor.ir._models import IRDocument

from ._rule_compiler import (
    INTEGER_STATE_FIELDS,
    NODE_ID_DEFTEMPLATE,
    RESPONSE_DEFTEMPLATE,
    STATE_DEFTEMPLATE,
    STATE_FIELDS,
    compile_rules_to_clips,
)


__all__ = ["CveRemFathomAdapter", "build_cve_rem_fathom"]


_ROUTING_TEMPLATES = frozenset({"state", "node-id", "response"})


class CveRemFathomAdapter(FathomAdapter):
    """``FathomAdapter`` subclass emitting flat-state + node-id mirror asserts.

    Overrides :meth:`mirror_state` to:

    1. Emit one ``(state ...)`` ``AssertSpec`` per step with all 18
       routing-relevant slots populated from the state model.
    2. Emit one ``(node-id (id <current>))`` ``AssertSpec`` per step,
       reading ``annotations["node_id"]`` (populated by ``dispatch.py``).

    Overrides :meth:`assert_with_provenance` to step-retract any prior
    routing-template fact (``state``, ``node-id``, ``response``) before
    asserting the fresh one — these templates are single-fact per step.
    The typed assert path is then used (slots typed ``SlotType.SYMBOL``
    in the registry are wrapped as ``clips.Symbol`` by
    ``FactManager._coerce_for_clips``, so symbol-form rule predicates
    like ``(state (source_trust trusted))`` match).

    Thread safety: CLIPS is single-threaded and the harbor dispatcher
    fans parallel branches into independent ``asyncio.to_thread`` calls
    that all share this adapter's engine. A per-adapter ``RLock``
    serializes every CLIPS-touching path (assert, evaluate, retract);
    parallel branches still run their Python work concurrently but
    cooperate at the CLIPS boundary.
    """

    def __init__(self, engine: Any) -> None:
        super().__init__(engine)
        # CLIPS is not thread-safe; harbor dispatch parallel branches
        # call assert/evaluate from multiple to_thread workers.
        self._clips_lock = threading.RLock()

    @property
    def clips_lock(self) -> threading.RLock:
        """Expose the CLIPS serialization lock for external code paths
        (e.g. ``harbor.runtime.dispatch._retract_harbor_actions``)
        that touch ``engine._env`` directly.
        """
        return self._clips_lock

    def assert_with_provenance(
        self,
        template: str,
        slots: dict[str, Any],
        provenance: dict[str, Any],
    ) -> None:
        # ``AssertSpec.slots`` is typed ``dict[str, str]``, so
        # :meth:`mirror_state` stringifies INTEGER state fields. Cast
        # them back here so Fathom's INTEGER-typed registry validation
        # passes.
        if template == "state":
            slots = dict(slots)
            for f in INTEGER_STATE_FIELDS:
                if f in slots and isinstance(slots[f], str) and slots[f]:
                    try:
                        slots[f] = int(slots[f])
                    except ValueError:
                        slots.pop(f)
        with self._clips_lock:
            # Routing templates are step-lifecycle: retract any prior fact
            # of the same template before asserting the fresh one.
            # ``engine.retract`` uses Fathom's safe collect-then-retract
            # internally — direct ``env.facts()`` + ``fact.retract()``
            # iteration proved racy and caused CLIPSMEMORY1 corruption.
            if template in _ROUTING_TEMPLATES:
                try:
                    self.engine.retract(template)
                except Exception:  # noqa: BLE001
                    pass
            # Delegate to typed path. ``FactManager._coerce_for_clips``
            # wraps ``SlotType.SYMBOL`` str values in ``clips.Symbol``,
            # so ``(state (source_trust trusted))`` predicates match.
            return super().assert_with_provenance(template, slots, provenance)

    def evaluate(self) -> list[Any]:
        """Lock-wrapped evaluate so parallel dispatch branches don't
        race the CLIPS env."""
        with self._clips_lock:
            return super().evaluate()

    def mirror_state(
        self, state: Any, annotations: dict[str, Any]
    ) -> list[Any]:
        import fathom as _fathom

        node_id = str(annotations.get("node_id") or "")
        specs: list[Any] = []

        # node-id fact — drives ``?n <- (node-id (id X))`` predicates.
        if node_id:
            specs.append(
                _fathom.AssertSpec(template="node-id", slots={"id": node_id})
            )

        # state fact — one slot per routing-relevant field. Slots are
        # SYMBOL-typed CLIPS-side; empty strings are dropped (CLIPS
        # rejects empty symbols, and an absent slot defaults to ``nil``
        # which never matches a value predicate — desired semantics).
        state_slots: dict[str, Any] = {}
        for field in STATE_FIELDS:
            val = getattr(state, field, None)
            if val is None:
                continue
            if field in INTEGER_STATE_FIELDS:
                # AssertSpec.slots is typed dict[str, str]; the int is
                # re-cast in assert_with_provenance before reaching the
                # INTEGER-typed Fathom registry.
                try:
                    state_slots[field] = str(int(val))
                except (TypeError, ValueError):
                    continue
                continue
            if isinstance(val, bool):
                encoded = "true" if val else "false"
            elif hasattr(val, "value"):  # StrEnum
                encoded = str(val.value)
            else:
                encoded = str(val)
            if not encoded:
                continue
            state_slots[field] = encoded
        if state_slots:
            specs.append(
                _fathom.AssertSpec(template="state", slots=state_slots)
            )

        # response fact — populated once a HITL decision exists in state.
        # HITL nodes (HitlPlanReviewNode etc.) write a ``HitlResponse``
        # object onto ``state.response`` with a ``.decision`` field (offline
        # auto-approve sets it inline; the HTTP /respond path sets it on
        # resume). Read the nested object first, then fall back to a flat
        # ``response_decision`` field. Routing templates are retracted +
        # re-asserted each tick, so this reflects the current decision only.
        resp_obj = getattr(state, "response", None)
        decision = str(getattr(resp_obj, "decision", "") or "") if resp_obj else ""
        if not decision:
            decision = str(getattr(state, "response_decision", "") or "")
        if decision:
            specs.append(
                _fathom.AssertSpec(
                    template="response",
                    slots={"decision": decision},
                )
            )

        return specs


def _install_template(engine: Any, ddef: str) -> None:
    """Install one ``deftemplate``; tolerate duplicate registration."""
    try:
        engine._env.build(ddef)
    except Exception as exc:  # noqa: BLE001
        # Duplicate construct (already loaded) is a no-op error from
        # CLIPS. Anything else propagates.
        msg = str(exc).lower()
        if "redefin" in msg or "exists" in msg or "duplicate" in msg:
            return
        raise


def _install_rule(engine: Any, clp: str) -> tuple[bool, str]:
    """Install one ``defrule``; return ``(ok, error_msg)`` for caller logging."""
    try:
        engine._env.build(clp)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def build_cve_rem_fathom(
    ir_docs: list[IRDocument],
    bosun_pack_roots: list[Path] | None = None,
) -> tuple[Any, CveRemFathomAdapter]:
    """Construct + initialize a cve-rem Fathom engine + adapter pair.

    Parameters
    ----------
    ir_docs:
        Loaded ``IRDocument`` graphs whose ``rules`` (inline routing
        rules) should be compiled and installed.
    bosun_pack_roots:
        Optional list of directories containing Bosun ``<pack>/rules.clp``
        files to load. Pre-existing ``cve_rem.*`` packs ship rules.clp in
        ``demos/cve_remediation/graph/rules/`` — pass that path here.

    Returns
    -------
    A ``(engine, CveRemFathomAdapter)`` tuple. The adapter is fully
    initialized — its wrapped engine has the harbor_action template,
    the state/node-id/response templates, every compiled inline rule,
    and every Bosun pack rules.clp loaded.
    """
    import fathom as _fathom
    from fathom.models import SlotDefinition, TemplateDefinition

    engine = _fathom.Engine(default_decision="deny")
    adapter = CveRemFathomAdapter(engine)
    adapter.register_harbor_action_template()

    # Register harbor_action in Fathom's template registry so
    # ``engine.query("harbor_action", None)`` works (SDW pattern).
    harbor_action_def = TemplateDefinition(
        name="harbor_action",
        description="Harbor routing action",
        slots=[
            SlotDefinition(name="kind", type="symbol"),
            SlotDefinition(name="target", type="string"),
            SlotDefinition(name="reason", type="string"),
            SlotDefinition(name="rule_id", type="string"),
            SlotDefinition(name="step", type="integer"),
            SlotDefinition(name="targets", type="string"),  # multislot read as string list
            SlotDefinition(name="join", type="string"),
            SlotDefinition(name="strategy", type="symbol"),
            SlotDefinition(name="backoff_ms", type="integer"),
            SlotDefinition(name="fact", type="string"),
            SlotDefinition(name="slots", type="string"),
            SlotDefinition(name="pattern", type="string"),
            SlotDefinition(name="prompt", type="string"),
            SlotDefinition(name="interrupt_payload", type="string"),
            SlotDefinition(name="requested_capability", type="string"),
            SlotDefinition(name="timeout", type="string"),
            SlotDefinition(name="on_timeout", type="string"),
        ],
    )
    engine.template_registry["harbor_action"] = harbor_action_def

    # Register cve-rem mirror-state templates in the Python-side
    # template_registry too. Routing slots are typed SYMBOL — Fathom's
    # ``_coerce_for_clips`` wraps the value in ``clips.Symbol`` so the
    # CLIPS-side ``(slot ... (type SYMBOL))`` declaration in
    # STATE_DEFTEMPLATE accepts it, and rule predicates of the form
    # ``(state (source_trust trusted))`` match (symbol-to-symbol).
    _prov_slots = [
        SlotDefinition(name="_origin", type="string"),
        SlotDefinition(name="_source", type="string"),
        SlotDefinition(name="_run_id", type="string"),
        SlotDefinition(name="_step", type="integer"),
        SlotDefinition(name="_confidence", type="string"),
        SlotDefinition(name="_timestamp", type="string"),
    ]
    engine.template_registry["state"] = TemplateDefinition(
        name="state",
        description="cve-rem flat-state mirror",
        slots=_prov_slots + [
            SlotDefinition(
                name=f,
                type="integer" if f in INTEGER_STATE_FIELDS else "symbol",
            )
            for f in STATE_FIELDS
        ],
    )
    engine.template_registry["node-id"] = TemplateDefinition(
        name="node-id",
        description="current-step node id",
        slots=_prov_slots + [SlotDefinition(name="id", type="symbol")],
    )
    engine.template_registry["response"] = TemplateDefinition(
        name="response",
        description="HITL response payload",
        slots=_prov_slots + [
            SlotDefinition(name="decision", type="symbol"),
            SlotDefinition(name="caller", type="symbol"),
        ],
    )

    # Install harbor.* audit-pack template stubs (SDW pattern — dots OK in CLIPS).
    _audit_stubs = [
        "(deftemplate harbor.transition (slot _run_id) (slot _step) (slot kind))",
        "(deftemplate harbor.tool_call (slot _run_id) (slot _step) (slot name))",
        "(deftemplate harbor.node_run (slot _run_id) (slot _step) (slot node_id))",
        "(deftemplate harbor.respond (slot _run_id) (slot _step) (slot caller))",
        "(deftemplate harbor.cancel (slot _run_id) (slot _step) (slot reason))",
        "(deftemplate harbor.pause (slot _run_id) (slot _step) (slot reason))",
        "(deftemplate harbor.artifact_write (slot _run_id) (slot _step) (slot artifact_id))",
        # harbor.evidence -- asserted by GraphRun.respond (data slot carrying
        # the HITL response dict) and by harbor.runtime.bus's backpressure
        # path (kind/buffer_used/max/block_seconds slots). Slot set covers
        # both call sites plus the safety_pii pack's (run_id/step/text)
        # pattern -- CLIPS allows extra slot decls regardless of which
        # caller populates which.
        (
            "(deftemplate harbor.evidence "
            "(slot _origin) (slot _source) (slot _run_id) (slot _step) "
            "(slot _confidence) (slot _timestamp) "
            "(slot data) (slot text) (slot kind) "
            "(slot buffer_used) (slot max) (slot block_seconds))"
        ),
    ]
    for stub in _audit_stubs:
        _install_template(engine, stub)
    # Also register harbor.evidence in the Python-side template_registry so
    # ``assert_with_provenance`` finds it via the typed path. Fathom 0.3.1
    # tightened TemplateDefinition.name to reject dots ([A-Za-z_]...), but
    # the framework's HITL respond path (harbor/graph/run.py:559) asserts
    # the literal "harbor.evidence" name. Use model_construct() to bypass
    # the pydantic identifier check — the CLIPS engine already accepts the
    # dotted name via the raw deftemplate string above.
    _evidence_slots = _prov_slots + [
        SlotDefinition(name="data", type="string"),
        SlotDefinition(name="text", type="string"),
        SlotDefinition(name="kind", type="string"),
        SlotDefinition(name="buffer_used", type="integer"),
        SlotDefinition(name="max", type="integer"),
        SlotDefinition(name="block_seconds", type="string"),
    ]
    engine.template_registry["harbor.evidence"] = TemplateDefinition.model_construct(
        name="harbor.evidence",
        description="HITL respond + bus backpressure evidence",
        slots=_evidence_slots,
        ttl=None,
        scope="session",
    )

    # Install routing scaffold (state + node-id + response deftemplates).
    _install_template(engine, STATE_DEFTEMPLATE)
    _install_template(engine, NODE_ID_DEFTEMPLATE)
    _install_template(engine, RESPONSE_DEFTEMPLATE)

    # Compile + install inline rules from every loaded graph.
    rule_ok = 0
    rule_err = 0
    failed: list[tuple[str, str]] = []
    for doc in ir_docs:
        clp_rules = compile_rules_to_clips(doc.rules)
        for rule, clp in zip(doc.rules, clp_rules):
            ok, err = _install_rule(engine, clp)
            if ok:
                rule_ok += 1
            else:
                rule_err += 1
                failed.append((rule.id, err))
    print(
        f"[cve_rem_fathom] compiled inline rules: ok={rule_ok} err={rule_err}"
    )
    if failed:
        for rid, err in failed[:5]:
            print(f"  [cve_rem_fathom] rule {rid!r} failed: {err[:200]}")
        if len(failed) > 5:
            print(f"  [cve_rem_fathom] ... and {len(failed) - 5} more")

    # Load Bosun pack rules.clp files (cve_rem.*, harbor.bosun.*).
    pack_ok = 0
    pack_err = 0
    for root in bosun_pack_roots or []:
        if not root.exists():
            continue
        for pack_dir in sorted(root.iterdir()):
            if not pack_dir.is_dir():
                continue
            rules_path = pack_dir / "rules.clp"
            if not rules_path.exists():
                continue
            src = rules_path.read_text(encoding="utf-8")
            # Strip CLIPS comments and split into top-level constructs
            # (matches SDW loader).
            lines = []
            for line in src.splitlines():
                idx = line.find(";")
                lines.append(line[:idx] if idx >= 0 else line)
            clean = "\n".join(lines)
            constructs, cur, depth = [], [], 0
            for ch in clean:
                if depth == 0 and ch.isspace():
                    continue
                cur.append(ch)
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        constructs.append("".join(cur))
                        cur = []
            for construct in constructs:
                ok, err = _install_rule(engine, construct)
                if ok:
                    pack_ok += 1
                else:
                    pack_err += 1
            print(
                f"[cve_rem_fathom] loaded Bosun pack {pack_dir.name!r} "
                f"(ok={pack_ok} err={pack_err})"
            )

    return engine, adapter
