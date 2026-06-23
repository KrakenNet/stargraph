; SPDX-License-Identifier: Apache-2.0
; SOC Triage++ — signed Bosun ``budgets`` governance pack.
;
; Per-alert token / cost / latency caps (soc-triage.md §"DEMO FOOTPRINT":
; ``budgets/ -- per-alert token / $ caps``). Mirrors the builtin
; ``stargraph.bosun.budgets`` reference pack shape: a ``bosun.budget`` fact
; carries the current allowance, and each rule asserts a
; ``bosun.violation severity=halt`` when ``consumed >= allowed`` so the
; engine terminates the triage run before the next tool call.
;
; ``soc.budget.default`` carries the sane demo defaults a SOC manager can
; tune without redeploying the agent (signed-pack value, soc-triage.md
; §"WHY IT LANDS"): 8000 tokens, $0.25, 30s wall-clock per alert.

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

; Committed per-alert defaults (one fact, three slots). Asserted at run
; start by the gate; the rules below compare live spend against these.
(deftemplate soc.budget.default
  (slot tokens (default 8000))
  (slot cost_usd (default 0.25))
  (slot latency_s (default 30)))

(deffacts soc-budget-defaults
  (soc.budget.default (tokens 8000) (cost_usd 0.25) (latency_s 30)))

(defrule budget-exhausted-token
  (bosun.budget (kind "tokens") (allowed ?a) (consumed ?c&:(>= ?c ?a)) (run_id ?r))
  =>
  (assert (bosun.violation
            (kind "budget-exhausted")
            (severity "halt")
            (run_id ?r)
            (reason "per-alert token allowance exceeded"))))

(defrule budget-exhausted-cost
  (bosun.budget (kind "cost") (allowed ?a) (consumed ?c&:(>= ?c ?a)) (run_id ?r))
  =>
  (assert (bosun.violation
            (kind "budget-exhausted")
            (severity "halt")
            (run_id ?r)
            (reason "per-alert cost allowance exceeded"))))

(defrule budget-exhausted-latency
  (bosun.budget (kind "latency") (allowed ?a) (consumed ?c&:(>= ?c ?a)) (run_id ?r))
  =>
  (assert (bosun.violation
            (kind "budget-exhausted")
            (severity "halt")
            (run_id ?r)
            (reason "per-alert latency allowance exceeded"))))
