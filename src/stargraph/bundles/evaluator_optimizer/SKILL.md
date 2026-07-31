---
name: evaluator-optimizer
description: Draft an answer, judge it against a rubric, and refine on the judge's written feedback until it passes -- the generate/evaluate/optimize loop.
subgraph: graph.yaml
---

# evaluator-optimizer

Draft → judge → refine loop (benchmark T11). A `reason` node drafts, a
`judge` node scores the draft against the rubric, and on `fail` the
judge's rationale is re-injected into the brief so the next draft
addresses the actual objection. On `pass`, the graph halts.

## State

- Input: `task` -- what to produce.
- Outputs: `answer` (the accepted draft), `verdict`, `score`,
  `rationale`.

## Use

- Standalone: `stargraph run graph.yaml` (set the LM via
  `--lm-model`/`--lm-url` or per-node `config.model`).
- As a subgraph: `kind: subgraph`, `spec: <path to graph.yaml>`;
  `config.max_steps` bounds the refine loop.

## Tuning

Edit `graph.yaml`: the `evaluate` rubric is the whole contract -- make
it name the qualities that matter for your task.
