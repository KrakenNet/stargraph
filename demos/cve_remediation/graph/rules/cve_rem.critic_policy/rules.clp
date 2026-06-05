; SPDX-License-Identifier: Apache-2.0
; cve_rem.critic_policy — Fathom CLIPS pack.
;
; Schema-validation critic for Phase 1 extracted data. Evaluates
; extraction quality and injection risk to produce a verdict
; (veto/feedback/approved) that gates downstream processing.

(deftemplate cve_rem.critic_input
  (slot cve_id)
  (slot cwe_class)
  (slot injection_class)
  (slot attempt))

(deftemplate cve_rem.critic_decision
  (slot verdict)
  (slot feedback)
  (slot rule_id))

(defrule critic-veto-no-cve
  "Missing CVE ID from extraction — veto (cannot proceed)."
  (declare (salience 100))
  (cve_rem.critic_input (cve_id ""))
  =>
  (assert (cve_rem.critic_decision
            (verdict "veto")
            (feedback "missing cve_id from extracted advisory")
            (rule_id "critic-veto-no-cve"))))

(defrule critic-feedback-injection
  "Injection classifier flagged content — require re-extraction."
  (declare (salience 90))
  (cve_rem.critic_input (cve_id ?id&:(neq ?id ""))
                        (injection_class ?ic&:(neq ?ic "clean")))
  (not (cve_rem.critic_decision))
  =>
  (assert (cve_rem.critic_decision
            (verdict "feedback")
            (feedback (str-cat "injection_class=" ?ic "; rerun extract"))
            (rule_id "critic-feedback-injection"))))

(defrule critic-feedback-no-cwe
  "Missing CWE classification — request re-extraction."
  (declare (salience 80))
  (cve_rem.critic_input (cve_id ?id&:(neq ?id "")) (cwe_class "")
                        (injection_class "clean"))
  (not (cve_rem.critic_decision))
  =>
  (assert (cve_rem.critic_decision
            (verdict "feedback")
            (feedback "cwe missing; rerun extract")
            (rule_id "critic-feedback-no-cwe"))))

(defrule critic-approved
  "All checks pass — approve extraction."
  (declare (salience 50))
  (cve_rem.critic_input (cve_id ?id&:(neq ?id "")) (cwe_class ?cwe&:(neq ?cwe ""))
                        (injection_class "clean"))
  (not (cve_rem.critic_decision))
  =>
  (assert (cve_rem.critic_decision
            (verdict "approved")
            (feedback "")
            (rule_id "critic-approved"))))
