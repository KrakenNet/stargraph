; SPDX-License-Identifier: Apache-2.0
; cve_rem.disposition_policy — Fathom CLIPS pack.
;
; Unpatchable CVE disposition routing. When no upstream fix exists,
; determines whether to recommend disabling the service or isolating
; the host based on KEV status and CVSS severity.

(deftemplate cve_rem.unpatchable_input
  (slot cve_id)
  (slot kev_listed)
  (slot cvss_bp)
  (slot vulnerability_status))

(deftemplate cve_rem.disposition_decision
  (slot cve_id)
  (slot disposition)
  (slot reason))

(defrule disposition-disable-kev
  "KEV-listed unpatchable CVE: recommend disabling affected service."
  (cve_rem.unpatchable_input (cve_id ?id) (kev_listed "TRUE")
                             (vulnerability_status ?vs))
  =>
  (assert (cve_rem.disposition_decision
            (cve_id ?id)
            (disposition "disable_recommended")
            (reason (str-cat "No upstream fix (" ?vs "); CISA KEV listed; recommend disabling affected service")))))

(defrule disposition-disable-high-cvss
  "High-severity unpatchable CVE (CVSS >= 7.0): recommend disabling."
  (cve_rem.unpatchable_input (cve_id ?id) (kev_listed "FALSE")
                             (cvss_bp ?c&:(>= ?c 700))
                             (vulnerability_status ?vs))
  (not (cve_rem.disposition_decision (cve_id ?id)))
  =>
  (assert (cve_rem.disposition_decision
            (cve_id ?id)
            (disposition "disable_recommended")
            (reason (str-cat "No upstream fix (" ?vs "); CVSS=" (/ ?c 100.0) "; recommend disabling affected service")))))

(defrule disposition-isolate-default
  "Low/medium severity unpatchable: recommend network isolation."
  (declare (salience -10))
  (cve_rem.unpatchable_input (cve_id ?id) (vulnerability_status ?vs))
  (not (cve_rem.disposition_decision (cve_id ?id)))
  =>
  (assert (cve_rem.disposition_decision
            (cve_id ?id)
            (disposition "isolate_recommended")
            (reason (str-cat "No upstream fix (" ?vs "); below KEV/high-severity threshold; recommend network isolation")))))
