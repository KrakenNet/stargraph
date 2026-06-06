; SPDX-License-Identifier: Apache-2.0
; Bosun ``safety_pii`` reference pack — Phase-4 implementation (task 4.3).
;
; Pattern-matches PII in ``stargraph.evidence`` facts. Each rule reads the
; ``text`` slot, applies a regex via Fathom's built-in ``fathom-matches``
; user-function (registered by :class:`fathom.Engine`'s init), and emits
; a ``bosun.violation`` when the pattern fires.
;
; **Locked design choice (§16.9): this pack is a starting library, NOT
; a guarantee.** The patterns below cover the most common documented-test
; cases (SSN, email, credit-card-like, US phone). Operators MUST extend
; per their own data classification policy. See
; ``tests/integration/serve/test_safety_pii_patterns.py`` for the
; corpus + false-negative coverage.
;
; Templates declared here:
;   - ``stargraph.evidence``  — payload-bearing fact (run_id, step, text)
;   - ``bosun.violation``  — emitted when a pattern matches
;
; See stargraph-serve-and-bosun design §7.1 + §16.9.

(deftemplate stargraph.evidence
  (slot run_id)
  (slot step)
  (slot text))

(deftemplate bosun.violation
  (slot kind)
  (slot severity)
  (slot run_id)
  (slot reason))

(defrule pii-ssn
  (stargraph.evidence (run_id ?r) (text ?t&:(fathom-matches ?t "[0-9]{3}-[0-9]{2}-[0-9]{4}")))
  =>
  (assert (bosun.violation
            (kind "pii-ssn")
            (severity "halt")
            (run_id ?r)
            (reason "ssn pattern matched"))))

(defrule pii-email
  (stargraph.evidence (run_id ?r) (text ?t&:(fathom-matches ?t "[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}")))
  =>
  (assert (bosun.violation
            (kind "pii-email")
            (severity "warn")
            (run_id ?r)
            (reason "email pattern matched"))))

(defrule pii-credit-card
  (stargraph.evidence (run_id ?r) (text ?t&:(fathom-matches ?t "[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{4}")))
  =>
  (assert (bosun.violation
            (kind "pii-credit-card")
            (severity "halt")
            (run_id ?r)
            (reason "credit-card pattern matched"))))

(defrule pii-phone
  (stargraph.evidence (run_id ?r) (text ?t&:(fathom-matches ?t "(\\+?1[-. ]?)?(\\([0-9]{3}\\)|[0-9]{3})[-. ][0-9]{3}[-. ][0-9]{4}")))
  =>
  (assert (bosun.violation
            (kind "pii-phone")
            (severity "warn")
            (run_id ?r)
            (reason "phone pattern matched"))))
