; SPDX-License-Identifier: Apache-2.0
; Shipwright edits pack — fix routing for verify failures.
;
; Plan-1 scope: a verifier failure routes back to synthesize_graph for
; another attempt, until fix.attempts reaches 3, when the run escalates
; to the human_input node so the operator can correct the spec.

(deftemplate verify.failed
  (slot kind))

(deftemplate fix.attempts
  (slot value))

(deftemplate fix.target
  (slot node))

(defrule fix-static-failure
  (verify.failed (kind "static"))
  (fix.attempts (value ?n&:(< ?n 3)))
  =>
  (assert (fix.target (node "synthesize_graph"))))

(defrule fix-tests-failure
  (verify.failed (kind "tests"))
  (fix.attempts (value ?n&:(< ?n 3)))
  =>
  (assert (fix.target (node "synthesize_graph"))))

(defrule fix-smoke-failure
  (verify.failed (kind "smoke"))
  (fix.attempts (value ?n&:(< ?n 3)))
  =>
  (assert (fix.target (node "synthesize_graph"))))

(defrule fix-bound-escalate
  (verify.failed (kind ?))
  (fix.attempts (value ?n&:(>= ?n 3)))
  =>
  (assert (fix.target (node "human_input"))))
