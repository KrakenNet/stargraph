; SPDX-License-Identifier: Apache-2.0
; cve_rem.quarantine_policy — Fathom CLIPS pack.
;
; Severity-triaged quarantine evaluation. Replaces the hardcoded
; "any divergence = quarantine" with a rule-based severity classification.
;
; Phase fields classified as critical quarantine the run.
; Warn fields log but continue. Info fields are ignored.

(deftemplate cve_rem.probe_divergence
  (slot phase)
  (slot field_class)
  (slot observed)
  (slot expected))

(deftemplate cve_rem.quarantine_decision
  (slot quarantine)
  (slot severity)
  (slot phase)
  (slot reason))

; Critical divergences: version or status mismatches.
(defrule quarantine-critical-version
  "Version mismatch is always critical — quarantine immediately."
  (cve_rem.probe_divergence (phase ?p) (field_class "version")
                            (observed ?obs) (expected ?exp))
  =>
  (assert (cve_rem.quarantine_decision
            (quarantine "TRUE")
            (severity "critical")
            (phase ?p)
            (reason (str-cat "version mismatch: observed=" ?obs " expected=" ?exp)))))

(defrule quarantine-critical-status
  "Status mismatch (vulnerable/patched) is critical."
  (cve_rem.probe_divergence (phase ?p) (field_class "status")
                            (observed ?obs) (expected ?exp))
  =>
  (assert (cve_rem.quarantine_decision
            (quarantine "TRUE")
            (severity "critical")
            (phase ?p)
            (reason (str-cat "status mismatch: observed=" ?obs " expected=" ?exp)))))

; Warn-level divergences: config and service state.
(defrule quarantine-warn-config
  "Config divergence is a warning — log but continue."
  (cve_rem.probe_divergence (phase ?p) (field_class "config")
                            (observed ?obs) (expected ?exp))
  =>
  (assert (cve_rem.quarantine_decision
            (quarantine "FALSE")
            (severity "warn")
            (phase ?p)
            (reason (str-cat "config divergence: observed=" ?obs " expected=" ?exp)))))

(defrule quarantine-warn-service-state
  "Service state divergence is a warning."
  (cve_rem.probe_divergence (phase ?p) (field_class "service_state")
                            (observed ?obs) (expected ?exp))
  =>
  (assert (cve_rem.quarantine_decision
            (quarantine "FALSE")
            (severity "warn")
            (phase ?p)
            (reason (str-cat "service_state divergence: observed=" ?obs " expected=" ?exp)))))

; Info-level: timestamp, log level — ignore.
(defrule quarantine-info-timestamp
  "Timestamp drift is informational only."
  (cve_rem.probe_divergence (phase ?p) (field_class "timestamp"))
  =>
  (assert (cve_rem.quarantine_decision
            (quarantine "FALSE")
            (severity "info")
            (phase ?p)
            (reason "timestamp drift — informational"))))

; Default: unclassified fields are critical (fail-closed).
(defrule quarantine-unclassified-critical
  "Unclassified field divergence defaults to critical (fail-closed)."
  (declare (salience -10))
  (cve_rem.probe_divergence (phase ?p) (field_class ?fc)
                            (observed ?obs) (expected ?exp))
  (not (cve_rem.quarantine_decision (phase ?p)))
  =>
  (assert (cve_rem.quarantine_decision
            (quarantine "TRUE")
            (severity "critical")
            (phase ?p)
            (reason (str-cat "unclassified field " ?fc ": observed=" ?obs " expected=" ?exp)))))
