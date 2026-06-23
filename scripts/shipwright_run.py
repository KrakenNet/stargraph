#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run the Shipwright meta-graph end-to-end against a brief.

Mirrors `tests/integration/shipwright/test_e2e_with_ollama.py` but as a
real driver: takes a brief from argv, prints what each phase produces,
writes the synthesized artifacts to a work dir so you can inspect them.

Usage:
    LLM_OLLAMA_URL=http://localhost:41001/v1 \\
    LLM_OLLAMA_MODEL=gpt-oss:20b \\
    uv run python scripts/shipwright_run.py "a triage graph that ..."

Flags:
    --work-dir PATH     where to write synthesized files (default: ./.shipwright-out)
    --keep-required     skip the canned extras-injection step (run will halt at
                        the interview phase with required slots still unfilled —
                        useful for inspecting what gap_check produced)
    --no-verify         skip verify_static + verify_tests (just print artifacts)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import dspy  # pyright: ignore[reportMissingTypeStubs]

from stargraph.skills.shipwright.nodes.fix import FixLoop
from stargraph.skills.shipwright.nodes.interview import GapCheck, ProposeQuestions
from stargraph.skills.shipwright.nodes.parse import ParseBrief
from stargraph.skills.shipwright.nodes.synthesize import SynthesizeGraph
from stargraph.skills.shipwright.nodes.triage import TriageGate
from stargraph.skills.shipwright.nodes.verify import VerifyStatic, VerifyTests
from stargraph.skills.shipwright.state import SpecSlot, State

# Canned answers used when --keep-required is NOT set. Mirrors the e2e test.
_DEFAULT_EXTRAS: dict[str, object] = {
    "name": "generated_graph",
    "nodes": ["classify", "act"],
    "state_fields": [{"name": "input", "type": "str", "annotated": True}],
    "stores": {"doc": "sqlite:./.docs"},
    "triggers": [{"type": "manual"}],
}


def _hr(label: str) -> None:
    print(f"\n=== {label} " + "=" * max(0, 60 - len(label)))


def _configure_lm() -> None:
    url = os.environ.get("LLM_OLLAMA_URL", "http://localhost:11434/v1")
    model = os.environ.get("LLM_OLLAMA_MODEL", "llama3.1:8b")
    timeout = int(os.environ.get("LLM_OLLAMA_TIMEOUT_S", "60"))
    lm = dspy.LM(  # pyright: ignore[reportUnknownMemberType]
        f"openai/{model}", api_base=url, api_key="ollama", timeout=timeout
    )
    dspy.configure(lm=lm)  # pyright: ignore[reportUnknownMemberType]
    print(f"LM configured: {model} @ {url} (timeout={timeout}s)")


async def _run(brief: str, work_dir: Path, keep_required: bool, do_verify: bool) -> None:
    _configure_lm()
    ctx = SimpleNamespace(run_id="cli-run")
    state = State(mode="new", brief=brief)

    _hr("triage_gate")
    state = state.model_copy(update=await TriageGate().execute(state, ctx))
    print(f"mode={state.mode}  kind={state.kind}")

    _hr("parse_brief (LLM call #1)")
    state = state.model_copy(update=await ParseBrief().execute(state, ctx))
    if "kind" in state.slots:
        state = state.model_copy(update={"kind": state.slots["kind"].value})
    for name, slot in state.slots.items():
        print(f"  slot {name!r:20s} = {slot.value!r}  (origin={slot.origin})")

    _hr("gap_check (Bosun rules)")
    state = state.model_copy(update=await GapCheck().execute(state, ctx))
    required = [q for q in state.open_questions if q.kind == "required"]
    other = [q for q in state.open_questions if q.kind != "required"]
    print(f"required gaps: {len(required)}")
    for q in required:
        print(f"  [required] slot={q.slot!r:20s} — {q.prompt}")
    if other:
        print(f"other gaps: {len(other)}")
        for q in other:
            print(f"  [{q.kind}]  slot={q.slot!r:20s} — {q.prompt}")

    _hr("propose_questions (LLM call #2)")
    state = state.model_copy(update=await ProposeQuestions().execute(state, ctx))
    llm_qs = [q for q in state.open_questions if q.origin == "llm"]
    print(f"llm-proposed questions: {len(llm_qs)}")
    for q in llm_qs:
        print(f"  [{q.kind}]  slot={q.slot!r:20s} — {q.prompt}")

    if keep_required:
        _hr("HALT: --keep-required set, skipping synth")
        print("Required slots still unfilled. Re-run without --keep-required to continue.")
        return

    _hr("inject canned extras (simulating HITL answers)")
    merged = dict(state.slots)
    for n, v in _DEFAULT_EXTRAS.items():
        if n not in merged:
            merged[n] = SpecSlot(name=n, value=v, origin="user")
            print(f"  injected {n!r} = {v!r}")
    state = state.model_copy(update={"slots": merged})

    state = state.model_copy(update=await GapCheck().execute(state, ctx))
    still_req = [q.slot for q in state.open_questions if q.kind == "required"]
    if still_req:
        print(f"WARN: still missing required slots after injection: {still_req}")

    _hr("synthesize_graph (LLM call #3)")
    state = state.model_copy(update=await SynthesizeGraph().execute(state, ctx))
    work_dir.mkdir(parents=True, exist_ok=True)
    for relpath, content in state.artifact_files.items():
        target = work_dir / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        print(f"  wrote {target}  ({len(content)} bytes)")

    if not do_verify:
        _hr("verify SKIPPED (--no-verify)")
        return

    _hr("verify_static")
    state = state.model_copy(update=await VerifyStatic(work_dir=work_dir).execute(state, ctx))
    static_result = next(r for r in reversed(state.verifier_results) if r.kind == "static")
    print(f"  passed={static_result.passed}  duration_ms={static_result.duration_ms}")
    for f in static_result.findings:
        print(f"  finding: {f}")

    _hr("verify_tests")
    state = state.model_copy(update=await VerifyTests(work_dir=work_dir).execute(state, ctx))
    tests_result = next(r for r in reversed(state.verifier_results) if r.kind == "tests")
    print(f"  passed={tests_result.passed}  duration_ms={tests_result.duration_ms}")
    for f in tests_result.findings:
        print(f"  finding: {f}")

    _hr("verify_smoke")
    if shutil.which("stargraph") is None:
        print("  SKIPPED — stargraph CLI not on PATH")
    else:
        # NOTE: synthesize emits user-surface YAML; stargraph simulate consumes IR
        # YAML. This currently fails until Plan 2 ships an IR translation.
        from stargraph.skills.shipwright.nodes.verify import VerifySmoke

        state = state.model_copy(update=await VerifySmoke(work_dir=work_dir).execute(state, ctx))
        smoke = next(r for r in reversed(state.verifier_results) if r.kind == "smoke")
        print(f"  passed={smoke.passed}  duration_ms={smoke.duration_ms}")
        for f in smoke.findings:
            print(f"  finding: {f}")

    _hr("fix_loop")
    fix_out = await FixLoop().execute(state, ctx)
    print(f"  next_node = {fix_out.get('next_node')}")
    if "fix_attempts" in fix_out:
        print(f"  fix_attempts = {fix_out['fix_attempts']}")

    _hr("DONE")
    print(f"artifacts: {work_dir.resolve()}")
    print(f"final state slot count: {len(state.slots)}")
    print(f"verifier results: {[(r.kind, r.passed) for r in state.verifier_results]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Shipwright meta-graph end-to-end.")
    parser.add_argument("brief", help="natural-language brief describing the graph to build")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path(".shipwright-out"),
        help="where to write synthesized files (default: ./.shipwright-out)",
    )
    parser.add_argument(
        "--keep-required",
        action="store_true",
        help="halt after interview without injecting canned extras",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="skip verify_static + verify_tests + verify_smoke",
    )
    parser.add_argument(
        "--json-state",
        action="store_true",
        help="dump the final State as JSON instead of human-readable summary",
    )
    args = parser.parse_args()

    try:
        asyncio.run(
            _run(
                brief=args.brief,
                work_dir=args.work_dir,
                keep_required=args.keep_required,
                do_verify=not args.no_verify,
            )
        )
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"\nERROR: {type(e).__name__}: {e}", file=sys.stderr)
        if os.environ.get("SHIPWRIGHT_DEBUG"):
            import traceback

            traceback.print_exc()
        return 1
    if args.json_state:
        print(json.dumps({"ok": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
