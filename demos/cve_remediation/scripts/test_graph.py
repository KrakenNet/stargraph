# SPDX-License-Identifier: Apache-2.0
"""Top-level "score + test the graph" entry point.

Runs three layers, in order:

1. **Structural validator** (``validate_graph.py``) -- harbor.yaml parses,
   every node ``kind:`` resolves, no orphan rule refs. <2s.
2. **Pytest suite** (``demos/cve_remediation/graph/tests/``) -- fast unit
   + cassette tests. Skipped only if pytest unavailable.
3. **Verifier scorecard** (``score_graph.py``) -- runs all
   ``verify_*.py`` against live PDI/CargoNet/PG/Redis. Slowest layer.

Failing layer 1 short-circuits the rest (broken IR -> nothing else
matters). Layers 2+3 run independently; aggregate failure = exit 1.

Run::

    uv run --no-project python -m demos.cve_remediation.scripts.test_graph
    uv run --no-project python -m demos.cve_remediation.scripts.test_graph \\
        --skip-scorecard      # fast: validator + pytest only
    uv run --no-project python -m demos.cve_remediation.scripts.test_graph \\
        --scorecard-args="--category fancy --filter F12"
"""

from __future__ import annotations

import argparse
import shlex
import subprocess  # noqa: S404 -- repo-controlled module paths
import sys
import time
from pathlib import Path

_DEMO_PKG = "demos.cve_remediation.scripts"
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PYTEST_DIR = _REPO_ROOT / "demos" / "cve_remediation" / "graph" / "tests"


def _run(label: str, cmd: list[str]) -> tuple[int, float]:
    print(f"\n{'='*60}\n  {label}\n  $ {' '.join(shlex.quote(c) for c in cmd)}\n{'='*60}")
    start = time.monotonic()
    rc = subprocess.run(  # noqa: S603 -- repo-controlled command
        cmd, cwd=str(_REPO_ROOT), check=False
    ).returncode
    elapsed = time.monotonic() - start
    icon = "PASS" if rc == 0 else "FAIL"
    print(f"\n  -> {label}: {icon} (rc={rc}, {elapsed:.1f}s)")
    return rc, elapsed


def main() -> int:
    ap = argparse.ArgumentParser(description="cve_remediation graph test runner")
    ap.add_argument("--skip-validate", action="store_true")
    ap.add_argument("--skip-pytest", action="store_true")
    ap.add_argument("--skip-scorecard", action="store_true")
    ap.add_argument("--scorecard-args", default="",
                    help="extra args forwarded to score_graph.py")
    ap.add_argument("--pytest-args", default="-x -q",
                    help="extra args forwarded to pytest (default '-x -q')")
    args = ap.parse_args()

    layers: list[tuple[str, int, float]] = []

    if not args.skip_validate:
        rc, t = _run(
            "Layer 1: structural validator",
            ["uv", "run", "--no-project", "python", "-m",
             f"{_DEMO_PKG}.validate_graph"],
        )
        layers.append(("validate", rc, t))
        if rc != 0:
            print("\n!! Validator failed -- skipping pytest + scorecard "
                  "(broken IR makes downstream signals meaningless).")
            _print_summary(layers)
            return 1

    if not args.skip_pytest:
        if not _PYTEST_DIR.exists():
            print(f"\n!! pytest dir missing: {_PYTEST_DIR}")
        else:
            rc, t = _run(
                "Layer 2: pytest (graph/tests/)",
                ["uv", "run", "--no-project", "pytest",
                 *shlex.split(args.pytest_args), str(_PYTEST_DIR)],
            )
            layers.append(("pytest", rc, t))

    if not args.skip_scorecard:
        rc, t = _run(
            "Layer 3: verifier scorecard",
            ["uv", "run", "--no-project", "python", "-m",
             f"{_DEMO_PKG}.score_graph",
             *shlex.split(args.scorecard_args)],
        )
        layers.append(("scorecard", rc, t))

    _print_summary(layers)
    return 0 if all(rc == 0 for _, rc, _ in layers) else 1


def _print_summary(layers: list[tuple[str, int, float]]) -> None:
    print(f"\n{'='*60}\n  AGGREGATE\n{'='*60}")
    print(f"  {'layer':<12} {'status':<6} {'time':>8}")
    print(f"  {'-'*12} {'-'*6} {'-'*8}")
    for name, rc, t in layers:
        status = "PASS" if rc == 0 else "FAIL"
        print(f"  {name:<12} {status:<6} {t:7.1f}s")
    fails = [n for n, rc, _ in layers if rc != 0]
    overall = "PASS" if not fails else f"FAIL ({', '.join(fails)})"
    print(f"\n  OVERALL: {overall}")


if __name__ == "__main__":
    sys.exit(main())
