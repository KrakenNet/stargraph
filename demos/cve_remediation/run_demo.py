# SPDX-License-Identifier: Apache-2.0
"""cve_remediation end-to-end demo runner.

Drives every IR in the demo through ``harbor run --inspect`` (the
graph-simulation rule-firing trace), printing the graph hash + rule
count per IR. Used as both an operator-facing showcase and the
backbone of ``tests/test_e2e.py``.

Usage::

    uv run --no-project python -m demos.cve_remediation.run_demo

Phase E will swap ``--inspect`` for live execution once real node
bodies, signed packs, and broker intents land (E1/E2/E3).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

GRAPH_DIR: Final[Path] = Path(__file__).resolve().parent / "graph"

# Run order: main pipeline first, then phase 0/6, then triggered graphs,
# then sub-graphs (mounted by main but runnable standalone).
IR_FILES: Final[list[Path]] = [
    GRAPH_DIR / "harbor.yaml",
    GRAPH_DIR / "phase0" / "doctrine_ingest.yaml",
    GRAPH_DIR / "phase6" / "offline_learning.yaml",
    GRAPH_DIR / "triggered" / "drift_watch.yaml",
    GRAPH_DIR / "triggered" / "tier_re_eval.yaml",
    GRAPH_DIR / "triggered" / "audit_anchor.yaml",
    GRAPH_DIR / "triggered" / "lab_leak_reaper.yaml",
    GRAPH_DIR / "triggered" / "rolling_restart.yaml",
    GRAPH_DIR / "subgraphs" / "sandbox_dispatch.yaml",
    GRAPH_DIR / "subgraphs" / "progressive_execute.yaml",
]


@dataclass(frozen=True)
class RunRecord:
    ir_path: Path
    graph_hash: str
    rule_firings: int
    exit_code: int


_GRAPH_HASH_RE = re.compile(r"^graph_hash=([0-9a-f]{64})$", re.MULTILINE)
_RULE_FIRINGS_RE = re.compile(r"^rule_firings=(\d+)$", re.MULTILINE)


def run_inspect(ir_path: Path) -> RunRecord:
    """Invoke ``harbor run --inspect`` on ``ir_path`` and parse the output.

    Inspect-mode is float-free, side-effect-free, and skips checkpoint
    bootstrap — exactly what the POC stub state needs to clear FR-4.
    """
    proc = subprocess.run(
        ["uv", "run", "--no-project", "harbor", "run", str(ir_path), "--inspect"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    out = proc.stdout
    hash_match = _GRAPH_HASH_RE.search(out)
    fire_match = _RULE_FIRINGS_RE.search(out)
    return RunRecord(
        ir_path=ir_path,
        graph_hash=hash_match.group(1) if hash_match else "",
        rule_firings=int(fire_match.group(1)) if fire_match else -1,
        exit_code=proc.returncode,
    )


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    json_mode = "--json" in args

    records: list[RunRecord] = []
    failures = 0
    for ir in IR_FILES:
        rec = run_inspect(ir)
        records.append(rec)
        if rec.exit_code != 0 or not rec.graph_hash:
            failures += 1

    if json_mode:
        print(
            json.dumps(
                [
                    {
                        "ir": str(r.ir_path.relative_to(GRAPH_DIR.parent)),
                        "graph_hash": r.graph_hash,
                        "rule_firings": r.rule_firings,
                        "exit_code": r.exit_code,
                    }
                    for r in records
                ],
                indent=2,
            )
        )
    else:
        print(f"{'IR':<50}  {'rules':>5}  {'hash':<16}  exit")
        print("-" * 90)
        for r in records:
            print(
                f"{str(r.ir_path.relative_to(GRAPH_DIR.parent)):<50}  "
                f"{r.rule_firings:>5}  {r.graph_hash[:16]:<16}  {r.exit_code}"
            )
        print("-" * 90)
        print(f"total: {len(records)} IRs   failures: {failures}")

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
