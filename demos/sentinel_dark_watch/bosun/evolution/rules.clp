; SPDX-License-Identifier: Apache-2.0
; SDW evolution governance pack — tiered autonomy for pipeline self-improvement.
;
; Templates:
;   - ``sdw.proposal``   — an evolution proposal with category, risk, delta
;   - ``sdw.cooldown``   — tracks recent structural changes
;   - ``sdw.gate``       — governance decision output
;
; Rules implement tiered autonomy:
;   - Low-risk + positive delta → auto-approve
;   - Medium-risk + significant delta (>10%) → auto-approve
;   - Medium-risk + modest delta → require human approval
;   - High-risk / structural → require human approval
;   - Negative delta → auto-reject
;   - Cooldown: block structural changes within 24h of last one

(deftemplate sdw.proposal
  (slot proposal_id)
  (slot category)
  (slot risk)
  (slot delta_pct (type FLOAT))
  (slot run_id))

(deftemplate sdw.cooldown
  (slot last_structural_change_ts)
  (slot hours_since (type FLOAT))
  (slot run_id))

(deftemplate sdw.gate
  (slot decision)
  (slot reason)
  (slot run_id))

; Auto-reject: negative improvement
; Asserts both sdw.gate (audit) and stargraph_action (routing) facts.
(defrule evolution-reject-negative
  (sdw.proposal (delta_pct ?d&:(<= ?d 0.0)) (run_id ?r))
  =>
  (assert (sdw.gate
            (decision "reject")
            (reason "negative or zero improvement")
            (run_id ?r)))
  (assert (stargraph_action
            (kind goto)
            (target "curate_training_data")
            (reason "governance-rejected: negative or zero improvement"))))

; Auto-approve: low risk + positive delta
(defrule evolution-approve-low-risk
  (sdw.proposal (risk "low") (delta_pct ?d&:(> ?d 0.0)) (run_id ?r))
  =>
  (assert (sdw.gate
            (decision "approve")
            (reason "low-risk with positive improvement")
            (run_id ?r)))
  (assert (stargraph_action
            (kind goto)
            (target "apply_change")
            (reason "governance-approved: low-risk positive improvement"))))

; Auto-approve: medium risk + significant delta
(defrule evolution-approve-medium-significant
  (sdw.proposal (risk "medium") (delta_pct ?d&:(> ?d 10.0)) (run_id ?r))
  =>
  (assert (sdw.gate
            (decision "approve")
            (reason "medium-risk with significant improvement")
            (run_id ?r)))
  (assert (stargraph_action
            (kind goto)
            (target "apply_change")
            (reason "governance-approved: medium-risk significant improvement"))))

; Human review: medium risk + modest delta
(defrule evolution-human-medium-modest
  (sdw.proposal (risk "medium") (delta_pct ?d&:(> ?d 0.0)&:(<= ?d 10.0)) (run_id ?r))
  =>
  (assert (sdw.gate
            (decision "human_required")
            (reason "medium-risk with modest improvement needs review")
            (run_id ?r))))

; Human review: high risk always
(defrule evolution-human-high-risk
  (sdw.proposal (risk "high") (delta_pct ?d&:(> ?d 0.0)) (run_id ?r))
  =>
  (assert (sdw.gate
            (decision "human_required")
            (reason "high-risk structural change requires approval")
            (run_id ?r))))

; Cooldown block: structural change within 24h
(defrule evolution-cooldown-block
  (sdw.cooldown (hours_since ?h&:(< ?h 24.0)) (run_id ?r))
  (sdw.proposal (category ?c&:(or (eq ?c "node_addition") (eq ?c "node_removal") (eq ?c "flow_change"))) (run_id ?r))
  =>
  (assert (sdw.gate
            (decision "reject")
            (reason "structural change cooldown — less than 24h since last")
            (run_id ?r)))
  (assert (stargraph_action
            (kind goto)
            (target "curate_training_data")
            (reason "governance-rejected: structural cooldown"))))
