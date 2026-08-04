# SPDX-License-Identifier: Apache-2.0
"""Worked "attestable governance tick" example for the OVARP integration.

This is the concrete scenario the cross-repo end-to-end test attests: a
merge-gate decision. A ``review`` node computes whether the review quorum is
met, and **Fathom (CLIPS) routes** the run to ``merge`` or ``escalate`` over the
mirrored governance facts. That routing decision is what an OVARP
producer-runtime ``replayable`` receipt captures (ADR-0012): OVARP re-derives
the same outcome offline from the tick's facts (verify step 4), and
``ovarp verify --replay`` drives :mod:`stargraph.cli.ovarp_reproduce` to
reproduce the tick bit-for-bit.

The governance predicate — ``tests_passed ∧ reviewers ≥ 2 → merge`` else
``escalate`` — is encoded **twice, independently**, which is the ADR-0010
design (OVARP re-implements the routing semantics rather than trusting the
producer):

* **StarGraph / Fathom** (:data:`GOV_ROUTING_PACK`) routes on the mirrored
  booleans ``tests_passed`` and ``quorum_met`` (the ``reviewers ≥ 2`` numeric
  comparison is done in the node body, then mirrored as a boolean fact — the
  mirror boundary carries ``str(value)``, so booleans route cleanly where a raw
  int would arrive as a string).
* **OVARP** (:data:`OVARP_PACK`) re-derives the outcome directly from the raw
  facts ``{tests_passed, reviewers}`` using its own ``gte`` operator.

Both converge on the same ``outcome`` string (``"goto:merge"`` /
``"goto:escalate"``); if they ever diverge, verify VOIDs — that is the point.
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
    "GOV_GRAPH_IR",
    "GOV_ROUTING_PACK",
    "OVARP_FACTS",
    "OVARP_PACK",
    "MergeGateNode",
    "MergeGateState",
    "merge_gate_attestation_spec",
]


class MergeGateState(BaseModel):
    """Run state for the merge-gate governance tick.

    ``tests_passed`` and ``quorum_met`` cross the Mirror boundary into CLIPS as
    facts (``str(value)`` → ``"True"`` / ``"False"``); ``reviewers`` stays
    Python-side (the node reads it to derive ``quorum_met``). Only the two
    booleans route.
    """

    tests_passed: Annotated[bool, Mirror()] = False
    reviewers: int = 0
    quorum_met: Annotated[bool, Mirror()] = False


class MergeGateNode(NodeBase):
    """Compute ``quorum_met = reviewers >= 2`` (fact derivation, not routing).

    The routing *decision* (merge vs escalate) is Fathom's, over the mirrored
    ``tests_passed`` + ``quorum_met`` booleans; this node only turns the numeric
    reviewer count into the boolean the governance rules match on.
    """

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        del ctx
        reviewers: int = getattr(state, "reviewers")  # noqa: B009 — abstract BaseModel has no field
        return {"quorum_met": reviewers >= 2}


#: Fathom routing pack (YAML). ``module`` must match the focused module the
#: harness builds (see :func:`stargraph.ovarp.harness.build_attestable_run`).
#: Rules match the mirrored ``value`` slot and assert a ``stargraph_action``
#: ``goto`` toward a real node id. Escalate fires when EITHER guard fails, so
#: all four input combinations route (merge only on both-true).
GOV_ROUTING_PACK: str = """\
# SPDX-License-Identifier: Apache-2.0
module: gov_routing
ruleset: merge-gate
version: "1.0"
rules:
  - name: route-merge
    salience: 20
    when:
      - template: tests_passed
        conditions:
          - slot: value
            expression: 'equals("True")'
      - template: quorum_met
        conditions:
          - slot: value
            expression: 'equals("True")'
    then:
      action: allow
      reason: "tests pass and review quorum met -> merge"
      assert:
        - template: stargraph_action
          slots:
            kind: "(sym-cat goto)"
            target: "merge"
  - name: route-escalate-tests
    salience: 10
    when:
      - template: tests_passed
        conditions:
          - slot: value
            expression: 'equals("False")'
    then:
      action: allow
      reason: "tests failing -> escalate"
      assert:
        - template: stargraph_action
          slots:
            kind: "(sym-cat goto)"
            target: "escalate"
  - name: route-escalate-quorum
    salience: 10
    when:
      - template: quorum_met
        conditions:
          - slot: value
            expression: 'equals("False")'
    then:
      action: allow
      reason: "review quorum not met -> escalate"
      assert:
        - template: stargraph_action
          slots:
            kind: "(sym-cat goto)"
            target: "escalate"
"""

#: Graph IR (dict form). ``state_class`` points at :class:`MergeGateState` so
#: both the producer run and the reproducer subprocess rebuild an identical
#: state model. ``merge`` / ``escalate`` are real routing targets (echo
#: placeholders — the single-tick attestation dispatches only ``review``).
GOV_GRAPH_IR: dict[str, Any] = {
    "ir_version": "1.0.0",
    "id": "run:ovarp-merge-gate",
    "state_class": "stargraph.ovarp.example:MergeGateState",
    "nodes": [
        {"id": "review", "kind": "stargraph.ovarp.example:MergeGateNode"},
        {"id": "merge", "kind": "echo"},
        {"id": "escalate", "kind": "echo"},
    ],
}

#: OVARP policy pack (v0 stand-in grammar) — the OFFLINE re-implementation of
#: the same governance predicate, over the raw ``{tests_passed, reviewers}``
#: facts. verify step 4 evaluates this and asserts the outcome equals the
#: receipt's. ``then`` / ``default`` are free strings, so they carry the
#: ``"goto:<node>"`` routing outcome verbatim.
OVARP_PACK: dict[str, Any] = {
    "id": "stargraph/merge-gate@v1",
    "rules": [
        {
            "when": {
                "all": [
                    {"op": "eq", "field": "tests_passed", "value": True},
                    {"op": "gte", "field": "reviewers", "value": 2},
                ]
            },
            "then": "goto:merge",
        }
    ],
    "default": "goto:escalate",
}

#: The raw governance facts OVARP re-evaluates. These are the demo instance:
#: tests failing, one reviewer → ``goto:escalate`` on both evaluators.
OVARP_FACTS: dict[str, Any] = {"tests_passed": False, "reviewers": 1}

#: The runtime tag bound into every merge-gate receipt + replay bundle.
_RUNTIME = "stargraph@0.3"

#: Fixed 32-byte hex signing seeds. Pinned so an attested tick is byte-reproducible
#: (Ed25519 is deterministic) — the demo/e2e mint identical receipts every run. A
#: real deployment would supply operator-held keys instead.
_SEEDS = {"agent": "01" * 32, "attester": "02" * 32, "beacon": "03" * 32}


def merge_gate_attestation_spec() -> AttestationSpec:
    """Build the :class:`AttestationSpec` that attests the merge-gate ``review`` tick.

    Pairs the in-stack Fathom routing (:data:`GOV_ROUTING_PACK`) with OVARP's
    offline re-implementation (:data:`OVARP_PACK`) over the ``{tests_passed,
    reviewers}`` facts projected from state, under a fixed authority + pinned
    seeds so the emitted receipt is deterministic. Wire the returned spec into an
    :class:`stargraph.ovarp.sink.OvarpReceiptSink`.
    """
    return AttestationSpec(
        runtime=_RUNTIME,
        ir_dict=GOV_GRAPH_IR,
        fathom_pack=GOV_ROUTING_PACK,
        policy_id="stargraph/merge-gate@v1",
        ovarp_pack=OVARP_PACK,
        authority={
            "id": "dat:sg-merge-gate",
            "grants": ["route"],
            "resource_class": "graph-tick",
        },
        verb="route",
        resource="graph-tick-review",
        context={
            "protocol": "a2a",
            "runtime": _RUNTIME,
            "granularity": "per-decision",
        },
        facts_fields=("tests_passed", "reviewers"),
        seeds=_SEEDS,
        nonce="11" * 16,
        issued_at="2026-07-09T00:00:00Z",
        model_digest=FATHOM_ROUTER_MODEL_DIGEST,
        decoding_digest=FATHOM_ROUTER_DECODING_DIGEST,
        round=1,
    )
