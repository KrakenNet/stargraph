; SPDX-License-Identifier: Apache-2.0
; cve_rem.offline_isolation — Fathom CLIPS pack.
;
; Three responsibilities:
;
;   1. No inbound from production. cve_rem.network_edge facts have a
;      direction slot ("inbound"|"outbound") and source_zone slot
;      ("production"|"replica"|"approved-drop"|"localhost"). Any inbound
;      edge with source_zone="production" is a halt violation.
;
;   2. Egress only on the approved drop. Outbound edges must target
;      "approved-drop" zone. Any other destination on outbound is halt.
;
;   3. Replica load with redaction. cve_rem.replica_load facts must
;      reference a redaction_pack_hash that matches the currently-signed
;      cve_rem.redaction_pack fact. Mismatch or missing → halt.
;
; The pack relies on operators (or the broker layer) asserting the
; network_edge / replica_load / redaction_pack facts on every
; corresponding action. These rules don't introspect the host —
; they enforce based on declared facts, fail-loud on violation.

; ------------------------------ templates ------------------------------

(deftemplate cve_rem.network_edge
  (slot edge_id)
  (slot direction)       ; inbound | outbound
  (slot source_zone)     ; production | replica | approved-drop | localhost
  (slot dest_zone)
  (slot port)
  (slot opened_at))

(deftemplate cve_rem.replica_load
  (slot load_id)
  (slot replica_schema)         ; "eval"
  (slot redaction_pack_hash)
  (slot loaded_at))

(deftemplate cve_rem.redaction_pack
  (slot pack_hash)
  (slot signed_by)
  (slot active))                ; "true" | "false"

(deftemplate bosun.violation
  (slot kind)
  (slot severity)
  (slot run_id)
  (slot reason))

(deftemplate cve_rem.trusted_signer
  (slot signer))

(deftemplate cve_rem.metric
  (slot kind)
  (slot window_hours)
  (slot value))

; ------------------------------ rules ------------------------------

(defrule isolation-no-inbound-from-prod
  "Phase-6 host: any inbound edge from production zone halts."
  (cve_rem.network_edge (edge_id ?id) (direction "inbound")
                        (source_zone "production"))
  =>
  (assert (bosun.violation
            (kind "isolation-inbound-from-production")
            (severity "halt")
            (run_id "phase6")
            (reason (str-cat "inbound edge " ?id " originates in production zone")))))

(defrule isolation-egress-only-to-approved-drop
  "Phase-6 host: any outbound edge to non-approved-drop zone halts."
  (cve_rem.network_edge (edge_id ?id) (direction "outbound")
                        (dest_zone ?z&:(neq ?z "approved-drop")))
  =>
  (assert (bosun.violation
            (kind "isolation-egress-unauthorized")
            (severity "halt")
            (run_id "phase6")
            (reason (str-cat "outbound edge " ?id " targets " ?z
                             " — only approved-drop is permitted")))))

(defrule isolation-replica-load-without-redaction-pack
  "Replica load with no redaction_pack_hash declared. Halt."
  (cve_rem.replica_load (load_id ?id) (redaction_pack_hash ""))
  =>
  (assert (bosun.violation
            (kind "isolation-replica-no-redaction")
            (severity "halt")
            (run_id "phase6")
            (reason (str-cat "replica load " ?id " has empty redaction_pack_hash")))))

(defrule isolation-replica-load-with-stale-redaction-pack
  "Replica load referencing a redaction pack that is not the active one. Halt."
  (cve_rem.replica_load (load_id ?id) (redaction_pack_hash ?h))
  (not (cve_rem.redaction_pack (pack_hash ?h) (active "true")))
  =>
  (assert (bosun.violation
            (kind "isolation-replica-stale-redaction")
            (severity "halt")
            (run_id "phase6")
            (reason (str-cat "replica load " ?id " references redaction pack " ?h
                             " but no active redaction_pack with that hash exists")))))

; ------------------------------ extra isolation rules (D2) ------------------------------

(defrule isolation-replica-load-untrusted-signer
  "Replica load with a redaction pack signed by an untrusted signer. Halt."
  (cve_rem.replica_load (load_id ?id) (redaction_pack_hash ?h))
  (cve_rem.redaction_pack (pack_hash ?h) (active "true") (signed_by ?signer))
  (cve_rem.trusted_signer (signer ?trusted))
  (not (test (eq ?signer ?trusted)))
  =>
  (assert (bosun.violation
            (kind "isolation-replica-untrusted-signer")
            (severity "halt")
            (run_id "phase6")
            (reason (str-cat "redaction pack " ?h " signed by " ?signer
                             " not on trusted-signer list")))))

(defrule isolation-egress-attempt-rate-cap
  "Phase-6 host: more than 1 outbound burst per hour to approved-drop is suspect. Even legitimate egress should be rate-limited, >1/h pages."
  (cve_rem.metric (kind "approved-drop-egress-rate") (window_hours 1)
                  (value ?v&:(>= ?v 2)))
  =>
  (assert (bosun.violation
            (kind "isolation-egress-rate-burst")
            (severity "info")
            (run_id "phase6")
            (reason "approved-drop egress rate >1/h — review for repeat compile attempts"))))
