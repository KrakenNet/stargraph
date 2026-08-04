# SPDX-License-Identifier: Apache-2.0
"""Worked "attestable governance tick" example — an access-clearance gate.

The concrete scenario the governance end-to-end test attests: an agent requests
read access to a classified resource, and **Fathom (CLIPS) decides** ``allow`` or
``deny`` by comparing the agent's clearance to the resource's classification
(Bell-LaPadula "no read up": read an object only at or below your clearance).
That decision is what an OVARP producer-runtime ``replayable`` receipt captures
(ADR-0012).

Unlike the merge-gate routing example (:mod:`stargraph.ovarp.example`), the offline
evaluator here is **not hand-authored**. The receipt's policy pack is produced by
lowering *this same Fathom pack* with ``ovarp lower-fathom`` (:data:`CLEARANCE_RULES`
→ IR), so the two independent evaluators of ADR-0010 are:

* **StarGraph / Fathom** — CLIPS fires the governance rules over the mirrored
  ``clearance`` / ``classification`` facts; ``Engine.evaluate().decision`` is the
  in-stack ``allow`` / ``deny``.
* **OVARP** — ``evaluate_ir`` re-runs the *lowered* pack over the FactVector
  projected from the same mirrored facts (verify step 4).

Both consume one pack and must converge on the same decision string; if they ever
diverge, verify VOIDs. The pack is Mirror-shaped (one template per mirrored field,
rules join across the ``value`` slots) because StarGraph's Mirror boundary crosses
one fact per ``Annotated[T, Mirror(...)]`` field — and it is a pure governance pack
(``then: {action, reason}``, no ``assert``) because a rule that asserts a fact is
outside OVARP's lowering profile.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel

from stargraph.ir import Mirror
from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.ovarp.harness import (
    FATHOM_ROUTER_DECODING_DIGEST,
    FATHOM_ROUTER_MODEL_DIGEST,
)
from stargraph.ovarp.sink import AttestationSpec

__all__ = [
    "CLEARANCE_GRAPH_IR",
    "CLEARANCE_RULES",
    "ClearanceGateNode",
    "ClearanceGateState",
    "clearance_gate_attestation_spec",
]


class ClearanceGateState(BaseModel):
    """Run state for the access-clearance governance tick.

    ``clearance`` (the requesting agent's level) and ``classification`` (the
    resource's level) cross the Mirror boundary into CLIPS as one fact each
    (``str(value)`` → the ``value`` slot); the governance rules join across them.
    ``agent_id`` and ``resource`` stay Python-side (audit context, not part of the
    lattice decision).
    """

    clearance: Annotated[str, Mirror()] = "unclassified"
    classification: Annotated[str, Mirror()] = "unclassified"
    agent_id: str = ""
    resource: str = ""


def _canonical_label(label: str) -> str:
    """Normalize a clearance/classification label to its canonical lattice symbol.

    Lowercases and collapses spaces/underscores to hyphens, so operator-facing
    labels (``"Top Secret"``, ``"TOP_SECRET"``) match the pack's symbols
    (``top-secret``). A genuine PDP normalization step, not a routing decision.
    """
    return "-".join(label.lower().replace("_", " ").replace("-", " ").split())


class ClearanceGateNode(NodeBase):
    """Normalize the request's clearance/classification labels (fact derivation).

    The access *decision* (allow vs deny) is Fathom's, over the mirrored
    ``clearance`` + ``classification`` facts; this node only canonicalizes the two
    labels so the mirrored facts match the pack's lattice symbols.
    """

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        del ctx
        clearance: str = getattr(state, "clearance")  # noqa: B009 — abstract BaseModel has no field
        classification: str = getattr(state, "classification")  # noqa: B009
        return {
            "clearance": _canonical_label(clearance),
            "classification": _canonical_label(classification),
        }


#: Fathom governance pack (YAML text). Mirror-shaped: rules match the ``value`` slot
#: of the ``clearance`` / ``classification`` mirror facts and decide ``allow`` / ``deny``
#: (no ``assert`` — this pack lowers to the OVARP IR). ``module`` must match the
#: focused module the harness builds. Allow rules get HIGH salience (fire first),
#: deny rules LOW salience (fire last) so last-write-wins is fail-closed; allow/deny
#: sets are mutually exclusive by clearance, so no rule ever overlaps.
CLEARANCE_RULES: str = """\
# SPDX-License-Identifier: Apache-2.0
module: governance
ruleset: clearance-gate
version: "1.0"
rules:
  - name: deny-secret-for-confidential
    salience: 10
    when:
      - template: clearance
        conditions:
          - {slot: value, expression: 'equals("confidential")'}
      - template: classification
        conditions:
          - {slot: value, expression: 'in(["secret", "top-secret"])'}
    then:
      action: deny
      reason: "Confidential clearance cannot read secret or top-secret data"
  - name: deny-top-secret-for-secret
    salience: 10
    when:
      - template: clearance
        conditions:
          - {slot: value, expression: 'equals("secret")'}
      - template: classification
        conditions:
          - {slot: value, expression: 'equals("top-secret")'}
    then:
      action: deny
      reason: "Secret clearance insufficient for top-secret data"
  - name: allow-unclassified-for-anyone
    salience: 100
    when:
      - template: classification
        conditions:
          - {slot: value, expression: 'equals("unclassified")'}
    then:
      action: allow
      reason: "Any clearance may read unclassified data"
  - name: allow-secret-and-below-for-secret
    salience: 100
    when:
      - template: clearance
        conditions:
          - {slot: value, expression: 'equals("secret")'}
      - template: classification
        conditions:
          - {slot: value, expression: 'in(["secret", "confidential"])'}
    then:
      action: allow
      reason: "Secret clearance may read secret and confidential data"
  - name: allow-top-secret-reads-anything
    salience: 100
    when:
      - template: clearance
        conditions:
          - {slot: value, expression: 'equals("top-secret")'}
    then:
      action: allow
      reason: "Top-secret clearance may read any classification"
"""

#: Graph IR (dict form). ``state_class`` points at :class:`ClearanceGateState`.
#: ``grant`` / ``escalate`` are real downstream targets (echo placeholders — the
#: single-tick attestation dispatches only ``clearance-gate``); a decision-driven
#: router that steers to them is future work.
CLEARANCE_GRAPH_IR: dict[str, Any] = {
    "ir_version": "1.0.0",
    "id": "run:ovarp-clearance-gate",
    "state_class": "stargraph.ovarp.clearance_gate:ClearanceGateState",
    "nodes": [
        {"id": "clearance-gate", "kind": "stargraph.ovarp.clearance_gate:ClearanceGateNode"},
        {"id": "grant", "kind": "echo"},
        {"id": "escalate", "kind": "echo"},
    ],
}

#: The runtime tag bound into every clearance-gate receipt + replay bundle.
_RUNTIME = "stargraph@0.3"

#: Fixed 32-byte hex signing seeds — pinned so the attested tick is byte-reproducible
#: (Ed25519 is deterministic). A real deployment supplies operator-held keys.
_SEEDS = {"agent": "01" * 32, "attester": "02" * 32, "beacon": "03" * 32}


def clearance_gate_attestation_spec() -> AttestationSpec:
    """Build the governance :class:`AttestationSpec` for the clearance-gate tick.

    Governance mode (``ovarp_pack`` omitted → ``None``): the sink auto-lowers
    :data:`CLEARANCE_RULES` via ``ovarp lower-fathom`` to derive the offline pack,
    projects the Mirror FactVector, and binds the in-stack Fathom ``allow``/``deny``
    decision — no hand-authored offline pack. Wire the returned spec into an
    :class:`stargraph.ovarp.sink.OvarpReceiptSink`.
    """
    return AttestationSpec(
        runtime=_RUNTIME,
        ir_dict=CLEARANCE_GRAPH_IR,
        fathom_pack=CLEARANCE_RULES,
        policy_id="stargraph/clearance-gate@v1",
        pack_name="clearance-gate",
        authority={
            "id": "dat:sg-clearance-gate",
            "grants": ["decide"],
            "resource_class": "graph-tick",
        },
        verb="decide",
        resource="graph-tick-clearance-gate",
        context={
            "protocol": "a2a",
            "runtime": _RUNTIME,
            "granularity": "per-decision",
        },
        seeds=_SEEDS,
        nonce="11" * 16,
        issued_at="2026-07-10T00:00:00Z",
        model_digest=FATHOM_ROUTER_MODEL_DIGEST,
        decoding_digest=FATHOM_ROUTER_DECODING_DIGEST,
        round=1,
    )
