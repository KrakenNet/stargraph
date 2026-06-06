# SPDX-License-Identifier: Apache-2.0
"""Integration tests for ``harbor run --inspect``, ``harbor inspect`` and
``harbor simulate`` (FR-8, FR-9, FR-29, design §3.10).

Each test invokes the installed ``harbor`` console-script via subprocess
to exercise the same bind path operators see at the shell. ``--inspect``
must skip checkpoint and audit-log writes entirely; ``inspect`` must
filter audit records by ``run_id`` and force-loud on no-match.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.fixtures.ansi import strip_ansi

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
SAMPLE_GRAPH: Path = REPO_ROOT / "tests" / "fixtures" / "sample-graph.yaml"


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Invoke the ``harbor`` console-script, capturing stdout/stderr."""
    return subprocess.run(
        ["harbor", *args],
        capture_output=True,
        check=check,
        cwd=REPO_ROOT,
        text=True,
    )


def test_run_inspect_prints_trace_and_skips_checkpoint(tmp_path: Path) -> None:
    """``harbor run --inspect`` must print the rule trace and write nothing.

    The cwd is ``tmp_path`` so any accidental ``./.harbor/`` checkpoint
    side effect is observable as a stray directory.
    """
    result = subprocess.run(
        ["harbor", "run", "--inspect", str(SAMPLE_GRAPH)],
        capture_output=True,
        check=True,
        cwd=tmp_path,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    out = result.stdout
    # Two rules in sample-graph.yaml: r-advance, r-halt.
    assert "rule_firings=2" in out, out
    assert "rule=r-advance" in out, out
    assert "rule=r-halt" in out, out
    assert "graph_hash=" in out, out

    # No side-effects on disk.
    assert not (tmp_path / ".harbor").exists(), "inspect mode must not write checkpoint"
    assert list(tmp_path.iterdir()) == [], "inspect mode must not touch cwd"


def test_inspect_help_exits_zero() -> None:
    """``harbor inspect --help`` must exit 0 (subcommand is registered)."""
    result = _run("inspect", "--help")
    assert result.returncode == 0, result.stderr
    assert "run_id" in result.stdout.lower()


def test_simulate_help_exits_zero() -> None:
    """``harbor simulate --help`` must exit 0 (subcommand is registered)."""
    result = _run("simulate", "--help")
    assert result.returncode == 0, result.stderr
    assert "--fixtures" in strip_ansi(result.stdout)


def test_simulate_runs_and_prints_trace(tmp_path: Path) -> None:
    """``harbor simulate --fixtures`` must print a per-rule firing trace."""
    fixtures = tmp_path / "fixtures.yaml"
    fixtures.write_text("node_a: {}\nnode_b: {}\n", encoding="utf-8")
    result = _run("simulate", str(SAMPLE_GRAPH), "--fixtures", str(fixtures))
    assert result.returncode == 0, result.stderr
    assert "rule_firings=2" in result.stdout, result.stdout


def test_inspect_filters_by_run_id(tmp_path: Path) -> None:
    """``harbor inspect <run_id> --log-file`` round-trips a real audit log.

    Drives ``harbor run --log-file`` end-to-end first to produce a real
    JSONL audit log, then invokes ``harbor inspect`` with the run-id
    parsed from the run's stdout and asserts every emitted line carries
    the same ``run_id``.
    """
    log_file = tmp_path / "audit.jsonl"
    checkpoint_db = tmp_path / "ck.sqlite"
    run_result = subprocess.run(
        [
            "harbor",
            "run",
            str(SAMPLE_GRAPH),
            "--log-file",
            str(log_file),
            "--checkpoint",
            str(checkpoint_db),
        ],
        capture_output=True,
        check=True,
        cwd=tmp_path,
        text=True,
    )
    # Parse "run_id=<id> status=done" off the last stdout line.
    last = run_result.stdout.strip().splitlines()[-1]
    run_id = last.split()[0].split("=", 1)[1]

    inspect_result = _run("inspect", run_id, "--log-file", str(log_file))
    assert inspect_result.returncode == 0, inspect_result.stderr
    lines = [json.loads(line) for line in inspect_result.stdout.splitlines() if line.strip()]
    assert lines, "no events streamed"
    for ev in lines:
        assert ev["run_id"] == run_id, ev


def test_inspect_force_loud_on_no_match(tmp_path: Path) -> None:
    """``harbor inspect`` exits 1 when no audit record matches (FR-6)."""
    log_file = tmp_path / "empty.jsonl"
    log_file.write_text("", encoding="utf-8")
    result = _run("inspect", "no-such-run", "--log-file", str(log_file), check=False)
    assert result.returncode == 1, result.stdout
    assert "no events matched" in result.stderr.lower(), result.stderr
    sys.stdout.write("CLI INSPECT TEST PASS\n")
