; SPDX-License-Identifier: Apache-2.0
; Bosun ``audit`` reference pack — Phase-4 implementation (task 4.2).
;
; Emits ``bosun.audit`` facts on every audit-relevant Stargraph fact.
; The fact-watcher seam (``stargraph.bosun.audit.promote_audit_facts``)
; converts each ``bosun.audit`` assertion into a ``BosunAuditEvent``
; (Pydantic v2 variant declared in ``stargraph.runtime.events``) which
; flows through the existing single-sink ``JSONLAuditSink`` (design §7.2,
; FR-38, Resolved Decision #5 — single-sink invariant preserved).
;
; ``bosun.audit`` slot vocabulary:
;   - ``run_id`` — engine run identifier (string)
;   - ``step``   — engine step number (integer)
;   - ``kind``   — one of ``transition`` | ``tool_call`` | ``node_run`` |
;                  ``respond`` | ``cancel`` | ``pause`` | ``artifact_write``
;   - ``detail`` — short human-readable context (string)
;
; The seven rules below mirror the seven kinds. Each rule reads a
; canonical Stargraph fact and asserts a ``bosun.audit`` fact bound to
; the same run_id/step.
;
; See stargraph-serve-and-bosun design §7.1 + §7.2.

(deftemplate bosun.audit
  (slot run_id)
  (slot step)
  (slot kind)
  (slot detail))

(defrule audit-on-transition
  (stargraph.transition (_run_id ?r) (_step ?s) (kind ?k))
  =>
  (assert (bosun.audit (run_id ?r) (step ?s) (kind "transition") (detail ?k))))

(defrule audit-on-tool-call
  (stargraph.tool_call (_run_id ?r) (_step ?s) (name ?n))
  =>
  (assert (bosun.audit (run_id ?r) (step ?s) (kind "tool_call") (detail ?n))))

(defrule audit-on-node-run
  (stargraph.node_run (_run_id ?r) (_step ?s) (node_id ?n))
  =>
  (assert (bosun.audit (run_id ?r) (step ?s) (kind "node_run") (detail ?n))))

(defrule audit-on-respond
  (stargraph.respond (_run_id ?r) (_step ?s) (caller ?c))
  =>
  (assert (bosun.audit (run_id ?r) (step ?s) (kind "respond") (detail ?c))))

(defrule audit-on-cancel
  (stargraph.cancel (_run_id ?r) (_step ?s) (reason ?why))
  =>
  (assert (bosun.audit (run_id ?r) (step ?s) (kind "cancel") (detail ?why))))

(defrule audit-on-pause
  (stargraph.pause (_run_id ?r) (_step ?s) (reason ?why))
  =>
  (assert (bosun.audit (run_id ?r) (step ?s) (kind "pause") (detail ?why))))

(defrule audit-on-artifact-write
  (stargraph.artifact_write (_run_id ?r) (_step ?s) (artifact_id ?a))
  =>
  (assert (bosun.audit (run_id ?r) (step ?s) (kind "artifact_write") (detail ?a))))
