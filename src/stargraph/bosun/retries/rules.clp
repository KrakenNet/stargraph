; SPDX-License-Identifier: Apache-2.0
; Bosun ``retries`` reference pack — Phase-4 implementation (task 4.4).
;
; Detects recoverable Stargraph errors and emits ``action.retry`` facts
; with exponential-backoff delay. After the configured cap (5 attempts)
; the pack escalates to a ``bosun.violation kind="retry-exhausted"``
; (severity halt) so the engine terminates rather than spinning.
;
; Backoff curve (cap-checked at the rule body):
;   attempt=1  → delay= 2s
;   attempt=2  → delay= 4s
;   attempt=3  → delay= 8s
;   attempt=4  → delay=16s
;   attempt=5  → delay=32s   (last attempt, replay-deterministic)
;   attempt=6+ → ``bosun.violation kind="retry-exhausted"`` (halt)
;
; Templates declared here:
;   - ``stargraph.error``    — recoverable error fact (run_id, step, reason, recoverable)
;   - ``action.retry``    — retry directive emitted to the engine
;   - ``bosun.violation`` — emitted when the cap is reached (single shape across packs)
;
; The ``attempt`` slot on ``stargraph.error`` defaults to 1 when omitted;
; callers re-asserting after a failed retry are expected to bump it.
;
; See stargraph-serve-and-bosun design §7.1 + NFR-20.

(deftemplate stargraph.error
  (slot run_id)
  (slot step)
  (slot reason)
  (slot recoverable)
  (slot attempt (default 1)))

(deftemplate action.retry
  (slot run_id)
  (slot step)
  (slot delay_seconds)
  (slot attempt))

(deftemplate bosun.violation
  (slot kind)
  (slot severity)
  (slot run_id)
  (slot reason))

(defrule retry-on-recoverable
  (stargraph.error
    (run_id ?r)
    (step ?s)
    (recoverable TRUE)
    (attempt ?n&:(<= ?n 5)))
  =>
  (assert (action.retry
            (run_id ?r)
            (step ?s)
            (delay_seconds (** 2 ?n))
            (attempt ?n))))

(defrule retry-exhausted
  (stargraph.error
    (run_id ?r)
    (recoverable TRUE)
    (attempt ?n&:(> ?n 5)))
  =>
  (assert (bosun.violation
            (kind "retry-exhausted")
            (severity "halt")
            (run_id ?r)
            (reason "exceeded max retry attempts (5)"))))
