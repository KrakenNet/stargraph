# SPDX-License-Identifier: Apache-2.0
"""Integration tests for ``harbor counterfactual`` (FR-27, design §3.10).

The CLI dry-runs a counterfactual fork: it loads a YAML mutation,
validates it through :class:`CounterfactualMutation`, and prints the
cf-derived graph hash. This test suite exercises both the happy path
(mutation YAML round-trips, derived hash differs from original) and the
force-loud path (extra='forbid' rejects unknown keys).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.fixtures.ansi import strip_ansi

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
SAMPLE_GRAPH: Path = REPO_ROOT / "tests" / "fixtures" / "sample-graph.yaml"


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["harbor", *args],
        capture_output=True,
        check=check,
        cwd=REPO_ROOT,
        text=True,
    )


def test_counterfactual_help_exits_zero() -> None:
    """``harbor counterfactual --help`` must exit 0 (subcommand registered)."""
    result = _run("counterfactual", "--help")
    assert result.returncode == 0, result.stderr
    help_text = strip_ansi(result.stdout)
    assert "--step" in help_text
    assert "--mutate" in help_text


def test_counterfactual_prints_derived_hash(tmp_path: Path) -> None:
    """``harbor counterfactual`` prints original and derived graph hashes."""
    mutate = tmp_path / "mutation.yaml"
    mutate.write_text(
        "state_overrides:\n  message: counterfactual\n",
        encoding="utf-8",
    )
    result = _run(
        "counterfactual",
        str(SAMPLE_GRAPH),
        "--step",
        "3",
        "--mutate",
        str(mutate),
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "original_graph_hash=" in out, out
    assert "derived_graph_hash=" in out, out
    assert "cf_step=3" in out, out

    # Parse the two hex digests and confirm domain-separation actually
    # produced a different cf-derived hash (FR-27 amendment 6).
    lines = {
        k: v for k, v in (line.split("=", 1) for line in out.strip().splitlines() if "=" in line)
    }
    assert lines["original_graph_hash"] != lines["derived_graph_hash"]
    assert len(lines["derived_graph_hash"]) == 64


def test_counterfactual_rejects_unknown_mutation_key(tmp_path: Path) -> None:
    """Unknown YAML keys must surface (extra='forbid' on CounterfactualMutation)."""
    mutate = tmp_path / "bad.yaml"
    mutate.write_text("not_a_real_field: 1\n", encoding="utf-8")
    result = _run(
        "counterfactual",
        str(SAMPLE_GRAPH),
        "--step",
        "0",
        "--mutate",
        str(mutate),
        check=False,
    )
    assert result.returncode != 0, result.stdout
    # Pydantic's standard message for extra='forbid' violations.
    combined = (result.stdout + result.stderr).lower()
    assert "extra" in combined or "not permitted" in combined or "forbidden" in combined, (
        result.stderr or result.stdout
    )
