# SPDX-License-Identifier: Apache-2.0
"""Structural-hash calibration on a 100-node IR (NFR-3, FR-4).

Times :func:`stargraph.graph.hash.structural_hash` across 100 invocations on
``tests/fixtures/100-node-ir.yaml`` (100 echo nodes wired by 99 goto rules
+ a terminal halt). Exercises rule (a) topology lex-sort, rule (b) node
signatures (``model_json_schema()`` once per node), rule (c) state-schema
``repr`` fallback, and rule (d) rule-pack triple sort -- all four
amendment 3 components -- at production-scale node count.

Initial budget (NFR-3): p99 < 20ms per ``structural_hash`` call.

Skip-by-default: marked ``@pytest.mark.slow``; run with ``--runslow``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
import yaml

from stargraph.graph.hash import structural_hash
from stargraph.ir._models import IRDocument

_FIXTURE: Path = Path(__file__).parent.parent / "fixtures" / "100-node-ir.yaml"


def _percentile(samples_ns: list[int], pct: float) -> float:
    """Return ``pct``-th percentile of ``samples_ns`` in milliseconds."""
    s = sorted(samples_ns)
    idx = max(0, min(len(s) - 1, round((pct / 100.0) * (len(s) - 1))))
    return s[idx] / 1_000_000.0


@pytest.mark.slow
def test_structural_hash_100_node_p99(capsys: pytest.CaptureFixture[str]) -> None:
    """Hash a 100-node IR 100 times; report p50/p95/p99 (ms).

    Soft-pass via ``pytest.xfail`` if p99 exceeds the 20ms NFR-3 budget;
    the calibration line is the artifact regardless of pass/xfail.
    """
    iterations = 100
    data = yaml.safe_load(_FIXTURE.read_text())
    ir = IRDocument.model_validate(data)
    rule_packs: list[tuple[str, str, str]] = [
        ("pack-a", "sha256:aa", "1.0.0"),
        ("pack-b", "sha256:bb", "2.1.0"),
    ]

    # Warm-up: first call pays one-time `model_json_schema()` cost per node
    # type; we want the steady-state distribution.
    for _ in range(3):
        structural_hash(ir, rule_pack_versions=rule_packs)

    samples_ns: list[int] = []
    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        structural_hash(ir, rule_pack_versions=rule_packs)
        samples_ns.append(time.perf_counter_ns() - t0)

    p50 = _percentile(samples_ns, 50)
    p95 = _percentile(samples_ns, 95)
    p99 = _percentile(samples_ns, 99)

    line = (
        f"structural_hash_100_node n={iterations} p50={p50:.4f}ms p95={p95:.4f}ms p99={p99:.4f}ms"
    )
    with capsys.disabled():
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    budget_ms = 20.0
    if p99 >= budget_ms:
        pytest.xfail(f"p99={p99:.4f}ms exceeded {budget_ms}ms budget (NFR-3 calibration soft-pass)")
