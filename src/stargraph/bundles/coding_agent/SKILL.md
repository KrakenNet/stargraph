---
name: coding-agent
description: Plan a coding task, generate and execute Python, and loop on reviewer feedback until the code passes -- a plan/code/judge fix-loop with governed execution.
subgraph: graph.yaml
---

# coding-agent

Plan → code → review fix-loop. The `code` node generates a
self-contained Python script and runs it through `std.python_exec@1`
(capability-gated `tools:std:exec`, default-deny); a `judge` node
reviews the code plus its execution result against the rubric. On
`fail`, the reviewer's rationale is re-injected into the brief and the
loop retries; on `pass`, the graph halts.

## State

- Input: `task` -- what to build.
- Outputs: `code` (the approved script), `run_result`
  (stdout/stderr/exit_code envelope), `verdict`, `score`, `rationale`.

## Use

- Standalone: `stargraph run graph.yaml` (grant `tools:std:exec`; set
  the LM via `--lm-model`/`--lm-url` or per-node `config.model`).
- As a subgraph: `kind: subgraph`, `spec: <path to graph.yaml>`;
  `config.max_steps` bounds the fix-loop.

## Tuning

Edit `graph.yaml`: the `review` rubric, the `brief` template, or the
`code` node's `timeout_s`.
