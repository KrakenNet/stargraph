; SPDX-License-Identifier: Apache-2.0
; SOC Triage++ — signed Bosun ``soc-policy`` governance pack.
;
; The four CLIPS rules below encode the governance policy from
; ``demos/soc-triage/soc-triage.md`` (the "Fathom governance gate" box):
;
;   1. asset.tier == prod  &&  disposition == auto_remediate
;        -> require human sign-off (InterruptAction / HITL analyst_gate)
;   2. asset.owner == exec &&  severity >= high (risk band 2)
;        -> escalate (RouteToTier3)
;   3. risk_confidence < 0.6
;        -> require a second opinion (re-run with a different model)
;   4. EVERY rule firing emits a ``bosun.provenance`` fact carrying the
;        rule id + run_id/step + the decision it produced (audit trail —
;        backs the Ed25519 JSONL chain so an auditor can replay *why*).
;
; The gate node (``soc_policy``, passthrough) mirrors the RunState fields
; the policy reads into a single ``soc.policy.input`` fact. Each policy
; rule asserts a ``soc.policy.action`` fact (consumed by the graph's
; routing rules in stargraph.yaml) and, per rule 4, a paired
; ``bosun.provenance`` fact (origin=rule — see smart-stargraph §"Provenance-
; typed Facts": every fact carries origin/source/run_id/step).
;
; Templates are local to this pack (same self-contained convention as
; ``bosun/budgets/rules.clp`` + ``bosun/audit/rules.clp``), so the pack
; loads + parses standalone through CLIPS.

; ---------------------------------------------------------------------------
; Templates
; ---------------------------------------------------------------------------

; The policy input — the soc_policy gate node asserts one of these per run,
; mirroring the RunState slots the rules read. ``asset_owner`` defaults to
; "" so the exec-escalate rule simply does not fire when ownership metadata
; is absent (graceful: the builtin MLNode pipeline may not populate it).
(deftemplate soc.policy.input
  (slot run_id (default ""))
  (slot step (default 0))
  (slot asset_tier (default dev))
  (slot asset_owner (default ""))
  (slot disposition (default needs_human))
  (slot risk (default 0))
  (slot risk_confidence (default 1.0)))

; The routing action the graph's stargraph.yaml rules read (interrupt /
; escalate / second_opinion). ``reason`` is the human-readable line.
(deftemplate soc.policy.action
  (slot run_id (default ""))
  (slot kind)
  (slot reason))

; The provenance fact every firing emits (rule 4). ``origin`` is always
; ``rule`` per the provenance-typed-fact contract; ``rule_id`` names the
; firing rule; ``decision`` echoes the action kind for the audit chain.
(deftemplate bosun.provenance
  (slot run_id (default ""))
  (slot step (default 0))
  (slot origin (default rule))
  (slot rule_id)
  (slot decision)
  (slot detail (default "")))

; ---------------------------------------------------------------------------
; Rule 1 — prod asset + auto_remediate disposition → HITL interrupt.
; ---------------------------------------------------------------------------
(defrule soc-policy-prod-autoremediate-interrupt
  (soc.policy.input (run_id ?r) (step ?s)
                    (asset_tier prod) (disposition auto_remediate))
  =>
  (assert (soc.policy.action
            (run_id ?r)
            (kind "interrupt")
            (reason "prod asset auto-remediation requires analyst sign-off")))
  (assert (bosun.provenance
            (run_id ?r) (step ?s) (origin rule)
            (rule_id "soc-policy-prod-autoremediate-interrupt")
            (decision "interrupt")
            (detail "asset_tier=prod disposition=auto_remediate"))))

; ---------------------------------------------------------------------------
; Rule 2 — exec-owned asset + high severity (risk band 2) → escalate.
; ---------------------------------------------------------------------------
(defrule soc-policy-exec-high-escalate
  (soc.policy.input (run_id ?r) (step ?s)
                    (asset_owner exec) (risk ?risk&:(>= ?risk 2)))
  =>
  (assert (soc.policy.action
            (run_id ?r)
            (kind "escalate")
            (reason "exec-owned asset at high severity — route to Tier 3")))
  (assert (bosun.provenance
            (run_id ?r) (step ?s) (origin rule)
            (rule_id "soc-policy-exec-high-escalate")
            (decision "escalate")
            (detail "asset_owner=exec risk>=high"))))

; ---------------------------------------------------------------------------
; Rule 3 — model confidence below 0.6 → require a second opinion.
; ---------------------------------------------------------------------------
(defrule soc-policy-low-confidence-second-opinion
  (soc.policy.input (run_id ?r) (step ?s)
                    (risk_confidence ?c&:(< ?c 0.6)))
  =>
  (assert (soc.policy.action
            (run_id ?r)
            (kind "second_opinion")
            (reason "model confidence below 0.6 — re-run with a second model")))
  (assert (bosun.provenance
            (run_id ?r) (step ?s) (origin rule)
            (rule_id "soc-policy-low-confidence-second-opinion")
            (decision "second_opinion")
            (detail "risk_confidence<0.6"))))
