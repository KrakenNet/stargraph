---
name: orchestrator-workers
description: Decompose a task into subtasks with a planner, execute them with a tool-using worker, and synthesize the findings -- the orchestrator/workers pattern, sequential v1.
subgraph: graph.yaml
---

# orchestrator-workers

Plan → work → synthesize (benchmark T5/T10). A `plan` node decomposes
the task, a `react` worker executes the subtask list through the
governed tool pipeline, and a `summarize` node synthesizes the
findings. Sequential v1: one worker walks the whole subtask list;
parallel fan-out lands when the IR's `parallel` action compiles to
live routing.

## State

- Input: `task`.
- Outputs: `tasks` (the plan), `answer` (worker findings),
  `tool_trace`, `summary` (the synthesis).

## Use

- Standalone: `stargraph run graph.yaml` (network tools need the
  `tools` extra; set the LM via `--lm-model`/`--lm-url` or per-node
  `config.model`).
- As a subgraph: `kind: subgraph`, `spec: <path to graph.yaml>`.

## Tuning

Edit `graph.yaml`: the worker's tool allowlist and `max_iters`, or the
plan/synthesize instructions.
