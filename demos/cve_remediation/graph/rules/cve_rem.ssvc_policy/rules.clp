; SPDX-License-Identifier: Apache-2.0
; cve_rem.ssvc_policy — Fathom CLIPS pack.
;
; SSVC tier classification via rule evaluation instead of hardcoded
; Python if/elif thresholds. Reads cve_rem.ssvc_input facts and emits
; cve_rem.ssvc_decision with the computed tier.
;
; Thresholds match policy.yaml defaults but live in CLIPS rules so
; Bosun governance can override them via rule priority or pack layering.

(deftemplate cve_rem.ssvc_input
  (slot cvss_bp)
  (slot epss_bp)
  (slot kev_listed)
  (slot blast_radius))

(deftemplate cve_rem.ssvc_decision
  (slot tier)
  (slot rule_id)
  (slot reason))

; Rules ordered by salience (priority) — first match wins.

(defrule ssvc-act-auto-kev
  "KEV-listed CVE always routes to act_auto regardless of scores."
  (declare (salience 100))
  (cve_rem.ssvc_input (kev_listed "TRUE"))
  =>
  (assert (cve_rem.ssvc_decision
            (tier "act_auto")
            (rule_id "ssvc-act-auto-kev")
            (reason "CISA KEV listed"))))

(defrule ssvc-act-auto-critical-blast
  "CVSS >= 9.0 AND blast radius >= 100 nodes."
  (declare (salience 90))
  (cve_rem.ssvc_input (cvss_bp ?c&:(>= ?c 900)) (blast_radius ?b&:(>= ?b 100))
                      (kev_listed "FALSE"))
  =>
  (assert (cve_rem.ssvc_decision
            (tier "act_auto")
            (rule_id "ssvc-act-auto-critical-blast")
            (reason "CVSS>=9.0 with blast>=100"))))

(defrule ssvc-act-hitl
  "CVSS >= 7.0 AND EPSS >= 0.05 (500 bp)."
  (declare (salience 80))
  (cve_rem.ssvc_input (cvss_bp ?c&:(>= ?c 700)) (epss_bp ?e&:(>= ?e 500))
                      (kev_listed "FALSE"))
  (not (cve_rem.ssvc_decision))
  =>
  (assert (cve_rem.ssvc_decision
            (tier "act_hitl_required")
            (rule_id "ssvc-act-hitl")
            (reason "CVSS>=7.0 with EPSS>=0.05"))))

(defrule ssvc-attend
  "CVSS >= 4.0 (not caught by higher tiers)."
  (declare (salience 70))
  (cve_rem.ssvc_input (cvss_bp ?c&:(>= ?c 400)) (kev_listed "FALSE"))
  (not (cve_rem.ssvc_decision))
  =>
  (assert (cve_rem.ssvc_decision
            (tier "attend")
            (rule_id "ssvc-attend")
            (reason "CVSS>=4.0"))))

(defrule ssvc-defer
  "CVSS < 4.0 AND zero blast radius."
  (declare (salience 60))
  (cve_rem.ssvc_input (cvss_bp ?c&:(< ?c 400)) (blast_radius 0)
                      (kev_listed "FALSE"))
  (not (cve_rem.ssvc_decision))
  =>
  (assert (cve_rem.ssvc_decision
            (tier "defer")
            (rule_id "ssvc-defer")
            (reason "CVSS<4.0 with zero blast"))))

(defrule ssvc-track-default
  "Default tier when no other rule fires."
  (declare (salience 50))
  (cve_rem.ssvc_input)
  (not (cve_rem.ssvc_decision))
  =>
  (assert (cve_rem.ssvc_decision
            (tier "track")
            (rule_id "ssvc-track-default")
            (reason "default tier"))))
