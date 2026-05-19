; SPDX-License-Identifier: Apache-2.0
; cve_rem.doctrine_trust — Fathom CLIPS pack.
;
; Three responsibilities:
;
;   1. Source-class policy. cve_rem.doctrine_source facts carry a
;      source_class slot. Only "trusted-doctrine" may bypass injection
;      classifier. Any other class on the doctrine ingest path is a
;      halt violation (configuration error — wrong source routed in).
;
;   2. Manifest-hash allowlist. The boot gate maintains a set of
;      cve_rem.allowlist_entry facts. The runtime asserts the active
;      doctrine_manifest hash on every framework_mapping retrieval.
;      If the active hash is NOT in the allowlist, halt.
;
;   3. Pin sha256 immutability. If two doctrine_source facts share the
;      same corpus_version_pin slot but have different corpus_sha256
;      values, halt — supply-chain compromise indicator.

; ------------------------------ templates ------------------------------

(deftemplate cve_rem.doctrine_source
  (slot id)              ; mitre-attack | mitre-atlas | nist-800-40 | nist-800-53 | nist-ai-rmf | cwe-capec
  (slot source_class)    ; trusted-doctrine | semi | untrusted
  (slot corpus_version_pin)
  (slot corpus_sha256))

(deftemplate cve_rem.doctrine_manifest
  (slot manifest_hash)
  (slot signed_at)
  (slot signed_by))

(deftemplate cve_rem.allowlist_entry
  (slot manifest_hash)
  (slot active))         ; "true" | "false" (deactivated, kept for forensic)

(deftemplate bosun.violation
  (slot kind)
  (slot severity)
  (slot run_id)
  (slot reason))

(deftemplate cve_rem.pin_age_days
  (slot corpus_version_pin)
  (slot days))

(deftemplate cve_rem.doctrine_mirror
  (slot corpus_id)
  (slot mirror_url)
  (slot corpus_sha256))

; ------------------------------ rules ------------------------------

(defrule doctrine-source-class-mismatch
  "Doctrine ingest rejected non-trusted source class."
  (cve_rem.doctrine_source (id ?id) (source_class ?cls&:(neq ?cls "trusted-doctrine")))
  =>
  (assert (bosun.violation
            (kind "doctrine-source-class-mismatch")
            (severity "halt")
            (run_id "phase0")
            (reason (str-cat "doctrine source " ?id " has source_class=" ?cls
                             " but only trusted-doctrine may participate")))))

(defrule doctrine-manifest-not-allowlisted
  "Active manifest hash missing from boot-gate allowlist. Halt-new."
  (cve_rem.doctrine_manifest (manifest_hash ?h))
  (not (cve_rem.allowlist_entry (manifest_hash ?h) (active "true")))
  =>
  (assert (bosun.violation
            (kind "doctrine-manifest-unallowlisted")
            (severity "halt")
            (run_id "fleet")
            (reason (str-cat "doctrine manifest hash " ?h " is not in boot-gate allowlist")))))

(defrule doctrine-pin-sha-divergence
  "Same corpus pin with different sha256 across two source facts.
   Indicates supply-chain compromise; halt-new."
  (cve_rem.doctrine_source (id ?a) (corpus_version_pin ?pin) (corpus_sha256 ?h1))
  (cve_rem.doctrine_source (id ?b&:(neq ?b ?a)) (corpus_version_pin ?pin) (corpus_sha256 ?h2&:(neq ?h2 ?h1)))
  =>
  (assert (bosun.violation
            (kind "doctrine-pin-sha-divergence")
            (severity "halt")
            (run_id "fleet")
            (reason (str-cat "corpus pin " ?pin " has divergent sha256: "
                             ?a "=" ?h1 " vs " ?b "=" ?h2)))))

(defrule doctrine-deactivated-manifest-in-use
  "Allowlist explicitly deactivated this manifest. Halt-new."
  (cve_rem.doctrine_manifest (manifest_hash ?h))
  (cve_rem.allowlist_entry (manifest_hash ?h) (active "false"))
  =>
  (assert (bosun.violation
            (kind "doctrine-manifest-deactivated")
            (severity "halt")
            (run_id "fleet")
            (reason (str-cat "doctrine manifest " ?h " marked deactivated; refuse to use")))))

; ------------------------------ extra trust rules (D2) ------------------------------

(defrule doctrine-pin-stale
  "Doctrine pin older than 90 days without refresh — halt-new pending refresh ceremony."
  (cve_rem.doctrine_source (id ?id) (corpus_version_pin ?pin))
  (cve_rem.pin_age_days (corpus_version_pin ?pin) (days ?d&:(>= ?d 90)))
  =>
  (assert (bosun.violation
            (kind "doctrine-pin-stale")
            (severity "halt")
            (run_id "fleet")
            (reason (str-cat "doctrine pin " ?pin " is " ?d " days old; refresh required")))))

(defrule doctrine-mirror-divergence
  "Two mirrors of the same doctrine corpus must publish identical sha256.
   Divergence indicates one mirror was compromised — halt-new."
  (cve_rem.doctrine_mirror (corpus_id ?id) (mirror_url ?ma) (corpus_sha256 ?h1))
  (cve_rem.doctrine_mirror (corpus_id ?id) (mirror_url ?mb&:(neq ?mb ?ma)) (corpus_sha256 ?h2&:(neq ?h2 ?h1)))
  =>
  (assert (bosun.violation
            (kind "doctrine-mirror-divergence")
            (severity "halt")
            (run_id "fleet")
            (reason (str-cat "doctrine corpus " ?id " mirrors disagree: " ?ma "=" ?h1 " vs " ?mb "=" ?h2)))))
