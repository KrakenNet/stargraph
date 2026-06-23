; SPDX-License-Identifier: Apache-2.0
; Triage rule pack — classify an incoming item and pick a route.
;
; Facts asserted by the node from run state:
;   (item.signal (name <name>) (value <value>))  ; one per entry in signals
;   (item.keyword (value <token>))                ; one per token of subject+body
;
; Rules assert the decision plus a self-naming marker so the node can read
; back both the chosen route and which rules fired:
;   (triage (category <c>) (route <r>) (priority <p>) (rule <rule-name>))
;   (triage.matched)   ; presence suppresses the default fallthrough

(deftemplate item.signal
  (slot name)
  (slot value))

(deftemplate item.keyword
  (slot value))

(deftemplate triage
  (slot category)
  (slot route)
  (slot priority)
  (slot rule))

(deftemplate triage.matched)

; A high-severity security signal escalates immediately.
(defrule triage-security-high-severity
  (item.signal (name "severity") (value "high"))
  (item.signal (name "source") (value "edr"))
  =>
  (assert (triage (category "security") (route "escalate") (priority "p1")
            (rule "triage-security-high-severity")))
  (assert (triage.matched)))

; A billing keyword routes to the finance queue.
(defrule triage-billing-keyword
  (item.keyword (value "billing"))
  =>
  (assert (triage (category "billing") (route "finance-queue") (priority "p3")
            (rule "triage-billing-keyword")))
  (assert (triage.matched)))

; An invoice keyword is also a billing concern.
(defrule triage-invoice-keyword
  (item.keyword (value "invoice"))
  =>
  (assert (triage (category "billing") (route "finance-queue") (priority "p3")
            (rule "triage-invoice-keyword")))
  (assert (triage.matched)))

; Fallthrough: nothing specific matched — general queue.
(defrule triage-default-queue
  (not (triage.matched))
  =>
  (assert (triage (category "general") (route "queue") (priority "p3")
            (rule "triage-default-queue"))))
