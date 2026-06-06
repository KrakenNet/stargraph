; SPDX-License-Identifier: Apache-2.0
; Shipwright gap-detection rules — the interview floor.
;
; Templates:
;   (spec.kind (value <"graph"|"pack">))   ; classification fact
;   (spec.slot (name <name>) (value <v>))  ; one fact per filled slot
;   (spec_gap (kind <"required"|"edge_case">) (slot <name>) (reason <string>))

(deftemplate spec.kind
  (slot value))

(deftemplate spec.slot
  (slot name)
  (slot value))

(deftemplate spec_gap
  (slot kind)
  (slot slot)
  (slot reason))

(defrule gap-graph-purpose
  (spec.kind (value "graph"))
  (not (spec.slot (name "purpose")))
  =>
  (assert (spec_gap (kind "required") (slot "purpose")
            (reason "every graph needs a one-sentence purpose"))))

(defrule gap-graph-nodes
  (spec.kind (value "graph"))
  (not (spec.slot (name "nodes")))
  =>
  (assert (spec_gap (kind "required") (slot "nodes")
            (reason "list at least one node"))))

(defrule gap-graph-state-fields
  (spec.kind (value "graph"))
  (not (spec.slot (name "state_fields")))
  =>
  (assert (spec_gap (kind "required") (slot "state_fields")
            (reason "declare the State schema fields"))))

(defrule gap-graph-stores
  (spec.kind (value "graph"))
  (not (spec.slot (name "stores")))
  =>
  (assert (spec_gap (kind "required") (slot "stores")
            (reason "wire at least the doc + fact stores"))))

(defrule gap-graph-triggers
  (spec.kind (value "graph"))
  (not (spec.slot (name "triggers")))
  =>
  (assert (spec_gap (kind "required") (slot "triggers")
            (reason "declare manual + any cron/webhook triggers"))))

; --- cross-cutting --------------------------------------------------------

(deftemplate spec.profile (slot value))
(deftemplate spec.annotated_count (slot value))
(deftemplate spec.node_missing_side_effects (slot node))

(defrule gap-graph-no-annotated-state
  (spec.kind (value "graph"))
  (spec.annotated_count (value 0))
  =>
  (assert (spec_gap (kind "edge_case") (slot "annotated_state")
            (reason "no Mirror-annotated fields — rules will see no state"))))

(defrule gap-cleared-side-effects
  (spec.kind (value "graph"))
  (spec.profile (value "cleared"))
  (spec.node_missing_side_effects (node ?n))
  =>
  (assert (spec_gap (kind "required") (slot (str-cat "side_effects:" ?n))
            (reason "cleared profile requires every node to declare side_effects"))))
