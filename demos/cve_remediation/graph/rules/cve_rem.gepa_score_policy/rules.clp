; SPDX-License-Identifier: Apache-2.0
; cve_rem.gepa_score_policy — Fathom CLIPS pack.
;
; Score formula (v6-locked):
;   score = 0.35*validation
;         + 0.25*sandbox
;         + 0.15*cr_approved
;         + 0.15*no_drift_7d
;         + 0.10*no_rollback_30d
;
; Each component fact carries a value in [0,1]. The consolidated
; cve_rem.gepa_score fact is asserted when all 5 components are present.
; The strictly-better gate compares candidate vs current and emits
; cve_rem.gepa_decision (accept | reject) plus an audit trail.
;
; Operators feed the pack by asserting cve_rem.score_component facts
; (one per component) and the cve_rem.gepa_inputs fact (current score,
; epsilon margin). Rules consume these and emit downstream facts.

; ------------------------------ templates ------------------------------

(deftemplate cve_rem.score_component
  (slot artifact_hash)         ; ties components together to one candidate
  (slot kind)                  ; validation | sandbox | cr_approved | no_drift_7d | no_rollback_30d
  (slot value))                ; in [0,1]

(deftemplate cve_rem.gepa_inputs
  (slot artifact_hash)
  (slot current_score)
  (slot epsilon))              ; default 0.02

(deftemplate cve_rem.gepa_score
  (slot artifact_hash)
  (slot value))

(deftemplate cve_rem.gepa_decision
  (slot artifact_hash)
  (slot decision)              ; accept | reject
  (slot candidate_score)
  (slot current_score)
  (slot epsilon)
  (slot delta))

(deftemplate bosun.violation
  (slot kind)
  (slot severity)
  (slot run_id)
  (slot reason))

(deftemplate cve_rem.holdout_metadata
  (slot artifact_hash)
  (slot retro_count))

(deftemplate cve_rem.score_component_current
  (slot kind)
  (slot value))

(deftemplate cve_rem.shamir_status
  (slot artifact_hash)
  (slot quorum))

; ------------------------------ rules ------------------------------

(defrule score-component-out-of-range
  "Component value outside [0,1]. Halt — data-quality fail-loud."
  (cve_rem.score_component (artifact_hash ?h) (kind ?k)
                           (value ?v&:(or (< ?v 0.0) (> ?v 1.0))))
  =>
  (assert (bosun.violation
            (kind "score-component-out-of-range")
            (severity "halt")
            (run_id "phase6")
            (reason (str-cat "score component " ?k " for artifact " ?h
                             " has value " ?v " — must be in [0,1]")))))

(defrule gepa-score-compute
  "All 5 components present for an artifact_hash. Compute weighted score."
  (cve_rem.score_component (artifact_hash ?h) (kind "validation")        (value ?v1))
  (cve_rem.score_component (artifact_hash ?h) (kind "sandbox")           (value ?v2))
  (cve_rem.score_component (artifact_hash ?h) (kind "cr_approved")       (value ?v3))
  (cve_rem.score_component (artifact_hash ?h) (kind "no_drift_7d")       (value ?v4))
  (cve_rem.score_component (artifact_hash ?h) (kind "no_rollback_30d")   (value ?v5))
  (not (cve_rem.gepa_score (artifact_hash ?h)))
  =>
  (bind ?score (+ (* 0.35 ?v1) (* 0.25 ?v2) (* 0.15 ?v3) (* 0.15 ?v4) (* 0.10 ?v5)))
  (assert (cve_rem.gepa_score (artifact_hash ?h) (value ?score))))

(defrule gepa-decision-accept
  "Candidate strictly better by epsilon margin → accept."
  (cve_rem.gepa_inputs (artifact_hash ?h) (current_score ?cs) (epsilon ?eps))
  (cve_rem.gepa_score  (artifact_hash ?h) (value ?candidate))
  (test (>= (- ?candidate ?cs) ?eps))
  =>
  (assert (cve_rem.gepa_decision
            (artifact_hash ?h)
            (decision "accept")
            (candidate_score ?candidate)
            (current_score ?cs)
            (epsilon ?eps)
            (delta (- ?candidate ?cs)))))

(defrule gepa-decision-reject
  "Candidate not strictly better by epsilon margin → reject."
  (cve_rem.gepa_inputs (artifact_hash ?h) (current_score ?cs) (epsilon ?eps))
  (cve_rem.gepa_score  (artifact_hash ?h) (value ?candidate))
  (test (< (- ?candidate ?cs) ?eps))
  =>
  (assert (cve_rem.gepa_decision
            (artifact_hash ?h)
            (decision "reject")
            (candidate_score ?candidate)
            (current_score ?cs)
            (epsilon ?eps)
            (delta (- ?candidate ?cs)))))

; Refusal-on-rejected-artifact is enforced by the IR (Phase 6 rule
; r-shamir-emit only fires when shamir_quorum=reached, and the IR
; r-emit-ship rule reads the gepa_decision fact). No CLIPS rule needed
; here — keeping the policy in the IR avoids a phantom template
; reference and matches the v6 design.

; ------------------------------ extra GEPA policy rules (D2) ------------------------------

(defrule gepa-holdout-too-small
  "Reject the candidate if the holdout retro count is below the minimum sample size (50). A score from a tiny sample is statistically meaningless."
  (cve_rem.gepa_inputs (artifact_hash ?h))
  (cve_rem.holdout_metadata (artifact_hash ?h) (retro_count ?n&:(< ?n 50)))
  =>
  (assert (bosun.violation
            (kind "gepa-holdout-too-small")
            (severity "halt")
            (run_id "phase6")
            (reason (str-cat "holdout retro_count=" ?n " < 50 minimum sample size; reject")))))

(defrule gepa-regression-on-component
  "Halt accept-decisions that regress on any single component vs current by more than 5%. Even with positive delta on the weighted score, a regression on validation or sandbox is not acceptable."
  (cve_rem.score_component (artifact_hash ?h) (kind ?k) (value ?cand))
  (cve_rem.score_component_current (kind ?k) (value ?curr))
  (test (or (eq ?k "validation") (eq ?k "sandbox")))
  (test (< (- ?cand ?curr) -0.05))
  =>
  (assert (bosun.violation
            (kind "gepa-component-regression")
            (severity "halt")
            (run_id "phase6")
            (reason (str-cat "component " ?k " regressed by >5%: candidate=" ?cand " current=" ?curr)))))

(defrule gepa-shamir-required-on-accept
  "Accept-decision must be paired with a Shamir quorum=reached fact. Emits a quorum-required signal for the IR to gate ship_to_prompts_dir."
  (cve_rem.gepa_decision (artifact_hash ?h) (decision "accept"))
  (not (cve_rem.shamir_status (artifact_hash ?h) (quorum "reached")))
  =>
  (assert (bosun.violation
            (kind "gepa-accept-without-shamir")
            (severity "halt")
            (run_id "phase6")
            (reason (str-cat "accept decision for " ?h " has no shamir_status quorum=reached")))))
