; SPDX-License-Identifier: Apache-2.0
; Bosun ``budgets`` reference pack — Phase-4 implementation (task 4.1).
;
; Monitors budget facts (token spend, latency, cost) and emits
; ``bosun.violation severity=halt`` when an allowance is exhausted.
; The engine consumes ``bosun.violation`` and terminates the run before
; the next tool call (defense in depth).
;
; Templates declared here are local to this pack:
;   - ``bosun.budget``    — current budget state (kind, allowed, consumed, run_id)
;   - ``bosun.violation`` — emitted when allowance is exceeded
;
; Three rules cover the three budget kinds (tokens, latency, cost).
; Rule shape: when a budget fact's ``consumed`` slot meets-or-exceeds
; ``allowed``, assert a halt-severity violation against the same run_id.
;
; See stargraph-serve-and-bosun design §7.1 + §7.3.

(deftemplate bosun.budget
  (slot kind)
  (slot allowed)
  (slot consumed)
  (slot run_id))

(deftemplate bosun.violation
  (slot kind)
  (slot severity)
  (slot run_id)
  (slot reason))

(defrule budget-exhausted-token
  (bosun.budget (kind "tokens") (allowed ?a) (consumed ?c&:(>= ?c ?a)) (run_id ?r))
  =>
  (assert (bosun.violation
            (kind "budget-exhausted")
            (severity "halt")
            (run_id ?r)
            (reason "token allowance exceeded"))))

(defrule budget-exhausted-latency
  (bosun.budget (kind "latency") (allowed ?a) (consumed ?c&:(>= ?c ?a)) (run_id ?r))
  =>
  (assert (bosun.violation
            (kind "budget-exhausted")
            (severity "halt")
            (run_id ?r)
            (reason "latency allowance exceeded"))))

(defrule budget-exhausted-cost
  (bosun.budget (kind "cost") (allowed ?a) (consumed ?c&:(>= ?c ?a)) (run_id ?r))
  =>
  (assert (bosun.violation
            (kind "budget-exhausted")
            (severity "halt")
            (run_id ?r)
            (reason "cost allowance exceeded"))))
