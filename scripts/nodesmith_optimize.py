#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Nodesmith idea-2 harness: model bench-off + DSPy compile + drift report.

The optimization metric is *exactly* the gate that ships nodes
(:func:`stargraph.skills.nodesmith.gate.run_full_gate`), so a higher score
means more nodes pass on the first try — the same thing the build node cares
about.

Usage
-----
Drift (no LLM needed) — rolling first-try pass rate; falling = re-optimize::

    uv run python scripts/nodesmith_optimize.py drift

Bench candidate models on the recorded briefs (first-try gate pass rate)::

    uv run python scripts/nodesmith_optimize.py bench \
        --lm-url "$LLM_OLLAMA_URL" --models laguna-xs,gemma3,gpt-oss:20b [--limit 20]

Compile few-shot demos with the winning model → writes compiled.json, which the
build node auto-loads on its next run::

    uv run python scripts/nodesmith_optimize.py compile \
        --lm-url "$LLM_OLLAMA_URL" --lm-model laguna-xs
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

import dspy  # pyright: ignore[reportMissingTypeStubs]

from stargraph.skills.nodesmith import _ledger
from stargraph.skills.nodesmith.gate import all_passed, run_full_gate
from stargraph.skills.nodesmith.program import INPUT_FIELDS, NodeProgram, coerce, configure_lm

_OUTPUT_FIELDS = ("class_name", "reads", "writes", "fixture", "node_source", "test_source")


def _examples(limit: int | None) -> list[Any]:
    rows = _ledger.load_trainset()
    if limit:
        rows = rows[-limit:]
    out: list[Any] = []
    for r in rows:
        ex = dspy.Example(  # pyright: ignore[reportUnknownMemberType]
            brief=r.get("brief", ""),
            lessons=[],
            last_findings=[],
            class_name=r.get("class_name", ""),
            reads=r.get("reads", []),
            writes=r.get("writes", []),
            fixture=r.get("fixture", {}),
            node_source=r.get("node_source", ""),
            test_source=r.get("test_source", ""),
        ).with_inputs(*INPUT_FIELDS)
        out.append(ex)
    return out


def gate_metric(example: Any, pred: Any, trace: Any = None) -> bool:
    """1.0 iff the predicted node passes the full gate. The real ship criterion."""
    gen = coerce(pred)
    files = {"node.py": gen["node_source"], "test_node.py": gen["test_source"]}
    with tempfile.TemporaryDirectory() as d:
        results = run_full_gate(
            Path(d), files, reads=gen["reads"], writes=gen["writes"], fixture=gen["fixture"]
        )
    return all_passed(results)


def _demo_to_dict(demo: Any) -> dict[str, Any]:
    keys = INPUT_FIELDS + _OUTPUT_FIELDS
    try:
        return {k: demo.get(k) for k in keys if demo.get(k) is not None}  # pyright: ignore[reportUnknownMemberType]
    except AttributeError:
        store = getattr(demo, "_store", {}) or {}
        return {k: store[k] for k in keys if k in store}


# --------------------------------------------------------------------------- #
def cmd_drift(args: argparse.Namespace) -> None:
    rate = _ledger.drift_rate(window=args.window)
    n = len(_ledger.load_trainset())
    print(f"first-try pass rate (last {args.window} of {n}): {rate:.0%}")
    if n >= args.window and rate < args.threshold:
        print(f"⚠ below {args.threshold:.0%} — recommend `compile` to re-optimize.")


def cmd_bench(args: argparse.Namespace) -> None:
    examples = _examples(args.limit)
    if not examples:
        print("no trainset yet — run the nodesmith graph to accumulate briefs first.")
        return
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    print(f"benching {len(models)} model(s) over {len(examples)} brief(s)\n")
    table: list[tuple[str, float]] = []
    for model in models:
        configure_lm(args.lm_url, model, args.lm_key)
        program = NodeProgram(load_compiled=False)
        passes = 0
        for ex in examples:
            try:
                pred = program(brief=ex.brief, lessons=[], last_findings=[])
                if gate_metric(ex, pred):
                    passes += 1
            except Exception as e:
                print(f"  {model}: generation error on a brief: {type(e).__name__}")
        rate = passes / len(examples)
        table.append((model, rate))
        print(f"  {model:<24} {passes}/{len(examples)}  ({rate:.0%})")
    table.sort(key=lambda t: t[1], reverse=True)
    print(f"\nwinner: {table[0][0]} ({table[0][1]:.0%}) — use it via --lm-model on the graph.")


def cmd_compile(args: argparse.Namespace) -> None:
    examples = _examples(args.limit)
    if not examples:
        print("no trainset yet — nothing to compile.")
        return
    configure_lm(args.lm_url, args.lm_model, args.lm_key)
    from dspy.teleprompt import BootstrapFewShot  # pyright: ignore[reportMissingImports]

    optimizer = BootstrapFewShot(metric=gate_metric, max_bootstrapped_demos=args.max_demos)
    compiled = optimizer.compile(NodeProgram(load_compiled=False), trainset=examples)  # pyright: ignore[reportUnknownMemberType]

    demos = [_demo_to_dict(d) for d in getattr(compiled.gen, "demos", [])]  # pyright: ignore[reportUnknownMemberType]
    out_path = _ledger.home() / _ledger.COMPILED_FILE
    out_path.write_text(
        json.dumps({"model": args.lm_model, "demos": demos}, indent=2), encoding="utf-8"
    )
    print(f"compiled {len(demos)} demo(s) with {args.lm_model} → {out_path}")
    print("the build node auto-loads these on its next run.")


def main() -> None:
    p = argparse.ArgumentParser(description="Nodesmith model bench + DSPy compile + drift")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("drift", help="rolling first-try pass rate")
    d.add_argument("--window", type=int, default=20)
    d.add_argument("--threshold", type=float, default=0.7)
    d.set_defaults(func=cmd_drift)

    b = sub.add_parser("bench", help="compare candidate models by first-try gate pass rate")
    b.add_argument("--lm-url", required=True)
    b.add_argument("--models", required=True, help="comma-separated model ids")
    b.add_argument("--lm-key", default="placeholder")
    b.add_argument("--limit", type=int, default=None, help="cap eval briefs (default: all)")
    b.set_defaults(func=cmd_bench)

    c = sub.add_parser("compile", help="DSPy BootstrapFewShot → compiled.json")
    c.add_argument("--lm-url", required=True)
    c.add_argument("--lm-model", required=True)
    c.add_argument("--lm-key", default="placeholder")
    c.add_argument("--limit", type=int, default=None)
    c.add_argument("--max-demos", type=int, default=4)
    c.set_defaults(func=cmd_compile)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
