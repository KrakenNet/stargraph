---
name: triage-router
description: Classify an incoming item into urgent/routine/irrelevant and dispatch it to the matching handler via rules -- the LLM emits a label, Fathom routes.
subgraph: graph.yaml
---

# triage-router

Classify → dispatch (benchmark T4). A `classify` node labels the item
(`verdict` = label + `confidence`); Fathom rules route each label to
its handler -- urgent items get concrete next actions from a `reason`
node, routine items get a `summarize` digest, irrelevant items halt
immediately. The LLM never picks the next node.

## State

- Input: `item` -- the thing to triage.
- Outputs: `verdict` (the label), `confidence`, and per-path `answer`
  (urgent) or `summary` (routine).

## Use

- Standalone: `stargraph run graph.yaml` (set the LM via
  `--lm-model`/`--lm-url` or per-node `config.model`).
- As a subgraph: `kind: subgraph`, `spec: <path to graph.yaml>`.

## Tuning

Edit `graph.yaml`: the label set (keep labels lowercase; rules match
the mirrored label string), the per-label handlers, and their
instructions. Add a label = add a `classify` label, a handler node, and
a goto rule.
