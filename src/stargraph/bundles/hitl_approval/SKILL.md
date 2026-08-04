---
name: hitl-approval
description: Draft a plan for a requested action, pause the run for a human approve/reject decision, and only then record the approved plan -- human-in-the-loop approval.
subgraph: graph.yaml
---

# hitl-approval

Propose → interrupt → apply (benchmark T9). A `reason` node drafts an
explicit plan (changes, risks, rollback), an `interrupt` node pauses
the run until a human responds through serve's respond endpoint, and
the apply step records the approved plan.

Sequential by design: an interrupt is a run-level pause, not a routing
decision, so run this bundle standalone (`stargraph serve` +
`stargraph run`), not as a rule-routed subgraph child.

## State

- Input: `request` -- the action needing approval.
- Outputs: `answer` (the proposed plan), `approved_plan` (set only
  after the human approves and the run resumes).

## Use

`stargraph run graph.yaml` under serve; the paused run surfaces the
`approve` prompt via WebSocket / `GET /runs/{id}`, and the human
responds via the respond endpoint.

## Tuning

Edit `graph.yaml`: the propose instructions or the approval `prompt`.
