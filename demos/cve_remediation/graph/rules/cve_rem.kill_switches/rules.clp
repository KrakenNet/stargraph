; SPDX-License-Identifier: Apache-2.0
; cve_rem.kill_switches — Fathom CLIPS pack.
;
; Two responsibilities:
;
;   1. Error-budget rules — read externally-computed cve_rem.metric facts
;      (rollback rate, sandbox-prod mismatch rate, HITL stuck duration,
;      cross-bucket plan reuse) and emit halt-severity bosun.violation
;      facts. The runtime auto-fires the halt-new Temporal signal on any
;      halt-severity violation.
;
;   2. Signal RBAC + 2-of-3 quorum — kill-switch signals are asserted as
;      cve_rem.kill_signal facts by signed CLI. halt-and-rollback-in-flight
;      requires 2-of-3 distinct roles (security-eng, pipeline-owner,
;      netops-lead); this pack emits a cve_rem.quorum_request that the
;      runtime serializes until quorum is met, then proceeds.
;
; All metric thresholds match v6 design block "Fathom Error-Budget Rules":
;   rollback-rate     > 5% / 24h
;   sandbox-mismatch  > 3% / 24h
;   stuck-state       any HITL workflow > 14d   (informational page only)
;   cross-bucket      same plan_hash applied to mismatched tier within 1h

; ------------------------------ templates ------------------------------

(deftemplate cve_rem.metric
  (slot kind)            ; rollback-rate | sandbox-mismatch | stuck-state | cross-bucket
  (slot window_hours)    ; 24 | 1 | etc.
  (slot value)           ; numeric measurement
  (slot threshold)       ; threshold value
  (slot run_id)          ; run-scoped or "fleet"
  (slot computed_at))    ; ISO8601 timestamp

(deftemplate cve_rem.kill_signal
  (slot kind)            ; halt-new | halt-pause-in-flight | halt-rollback-in-flight
  (slot actor)           ; signing principal
  (slot role)            ; security-eng | pipeline-owner | netops-lead
  (slot run_id)          ; "fleet" | specific run_id
  (slot signature_id))   ; CLI signature for audit chain

(deftemplate cve_rem.quorum_request
  (slot signal_kind)
  (slot run_id)
  (slot roles_required)
  (slot roles_present))

(deftemplate bosun.violation
  (slot kind)
  (slot severity)
  (slot run_id)
  (slot reason))

; ------------------------------ error-budget rules ------------------------------

(defrule rollback-rate-exceeded
  "Auto-fire halt-new when fleet rollback rate exceeds 5% in last 24h."
  (cve_rem.metric (kind "rollback-rate") (window_hours 24)
                  (value ?v) (threshold ?t&:(> ?v ?t)) (run_id ?r))
  =>
  (assert (bosun.violation
            (kind "rollback-rate-exceeded")
            (severity "halt")
            (run_id ?r)
            (reason "fleet rollback rate >5%/24h"))))

(defrule sandbox-mismatch-exceeded
  "Auto-fire halt-new when sandbox-prod divergence rate exceeds 3% in 24h."
  (cve_rem.metric (kind "sandbox-mismatch") (window_hours 24)
                  (value ?v) (threshold ?t&:(> ?v ?t)) (run_id ?r))
  =>
  (assert (bosun.violation
            (kind "sandbox-mismatch-exceeded")
            (severity "halt")
            (run_id ?r)
            (reason "sandbox-vs-prod divergence rate >3%/24h"))))

(defrule cross-bucket-violation
  "Same plan_hash applied to mismatched mission tier within 1h. Halt."
  (cve_rem.metric (kind "cross-bucket") (window_hours 1)
                  (value ?v&:(>= ?v 1)) (run_id ?r))
  =>
  (assert (bosun.violation
            (kind "cross-bucket")
            (severity "halt")
            (run_id ?r)
            (reason "same plan_hash applied across mission tiers within 1h"))))

(defrule stuck-state-page
  "Informational only. HITL workflow >14d with no signer activity → page."
  (cve_rem.metric (kind "stuck-state") (window_hours ?w&:(>= ?w 336))
                  (run_id ?r))
  =>
  (assert (bosun.violation
            (kind "stuck-state")
            (severity "info")
            (run_id ?r)
            (reason "HITL workflow >14d without signer activity"))))

; ------------------------------ kill-signal RBAC ------------------------------

(defrule halt-new-rbac
  "halt-new requires pipeline-owner OR security-eng role. Single signer."
  (cve_rem.kill_signal (kind "halt-new") (role ?role&:(or (eq ?role "pipeline-owner")
                                                          (eq ?role "security-eng")))
                       (run_id ?r))
  =>
  (assert (bosun.violation
            (kind "kill-signal-halt-new")
            (severity "halt")
            (run_id ?r)
            (reason "halt-new fired by authorized role"))))

(defrule halt-pause-rbac
  "halt-and-pause-in-flight requires pipeline-owner OR security-eng. Single signer."
  (cve_rem.kill_signal (kind "halt-pause-in-flight") (role ?role&:(or (eq ?role "pipeline-owner")
                                                                     (eq ?role "security-eng")))
                       (run_id ?r))
  =>
  (assert (bosun.violation
            (kind "kill-signal-halt-pause")
            (severity "halt")
            (run_id ?r)
            (reason "halt-and-pause-in-flight fired by authorized role"))))

; halt-and-rollback-in-flight: 2-of-3 quorum required. Multi-rule pattern.

(defrule rollback-quorum-collect-pipeline-owner
  (cve_rem.kill_signal (kind "halt-rollback-in-flight") (role "pipeline-owner") (run_id ?r))
  (cve_rem.kill_signal (kind "halt-rollback-in-flight") (role "security-eng") (run_id ?r))
  =>
  (assert (cve_rem.quorum_request
            (signal_kind "halt-rollback-in-flight")
            (run_id ?r)
            (roles_required "2-of-3")
            (roles_present "pipeline-owner+security-eng")))
  (assert (bosun.violation
            (kind "kill-signal-rollback-quorum")
            (severity "halt")
            (run_id ?r)
            (reason "halt-and-rollback quorum: pipeline-owner + security-eng"))))

(defrule rollback-quorum-collect-pipeline-netops
  (cve_rem.kill_signal (kind "halt-rollback-in-flight") (role "pipeline-owner") (run_id ?r))
  (cve_rem.kill_signal (kind "halt-rollback-in-flight") (role "netops-lead") (run_id ?r))
  =>
  (assert (cve_rem.quorum_request
            (signal_kind "halt-rollback-in-flight")
            (run_id ?r)
            (roles_required "2-of-3")
            (roles_present "pipeline-owner+netops-lead")))
  (assert (bosun.violation
            (kind "kill-signal-rollback-quorum")
            (severity "halt")
            (run_id ?r)
            (reason "halt-and-rollback quorum: pipeline-owner + netops-lead"))))

(defrule rollback-quorum-collect-security-netops
  (cve_rem.kill_signal (kind "halt-rollback-in-flight") (role "security-eng") (run_id ?r))
  (cve_rem.kill_signal (kind "halt-rollback-in-flight") (role "netops-lead") (run_id ?r))
  =>
  (assert (cve_rem.quorum_request
            (signal_kind "halt-rollback-in-flight")
            (run_id ?r)
            (roles_required "2-of-3")
            (roles_present "security-eng+netops-lead")))
  (assert (bosun.violation
            (kind "kill-signal-rollback-quorum")
            (severity "halt")
            (run_id ?r)
            (reason "halt-and-rollback quorum: security-eng + netops-lead"))))

; ------------------------------ extra error budgets (D2) ------------------------------

(defrule apply-rate-exceeded
  "Halt fleet apply when concurrent runs/min exceeds the static safety bound (50/min)."
  (cve_rem.metric (kind "apply-rate") (window_hours 1)
                  (value ?v) (threshold ?t&:(> ?v ?t)) (run_id ?r))
  =>
  (assert (bosun.violation
            (kind "apply-rate-exceeded")
            (severity "halt")
            (run_id ?r)
            (reason "concurrent-apply rate exceeds fleet safety bound"))))

(defrule queue-depth-saturated
  "Pause new runs when scheduler queue depth exceeds the safety bound."
  (cve_rem.metric (kind "queue-depth") (window_hours 1)
                  (value ?v) (threshold ?t&:(> ?v ?t)) (run_id ?r))
  =>
  (assert (bosun.violation
            (kind "queue-depth-saturated")
            (severity "halt")
            (run_id ?r)
            (reason "scheduler queue depth saturates HITL/sandbox capacity"))))

(defrule bulk-fanout-window
  "Page on bulk-cve fanout outside change-control windows (off-hours surge)."
  (cve_rem.metric (kind "bulk-fanout") (window_hours 1)
                  (value ?v&:(>= ?v 5)) (run_id ?r))
  =>
  (assert (bosun.violation
            (kind "bulk-fanout-off-hours")
            (severity "info")
            (run_id ?r)
            (reason "5+ CVE pipeline starts within 1h outside change window"))))
