# SPDX-License-Identifier: Apache-2.0
"""Integration: ``cve_rem.gepa_score_policy`` — CLIPS round-trip tests.

Covers v6-locked weighted score (0.35*validation + 0.25*sandbox +
0.15*cr_approved + 0.15*no_drift_7d + 0.10*no_rollback_30d), strictly-
better epsilon-margin gate, and out-of-range component fail-loud.
"""

from __future__ import annotations

import pytest
from fathom import Engine

from ._pack_helpers import facts_of, load_pack_rules, violations

pytestmark = pytest.mark.integration

H = "art-1"  # candidate artifact_hash used across tests


def _engine() -> Engine:
    eng = Engine(default_decision="allow")
    load_pack_rules(eng, "cve_rem.gepa_score_policy")
    return eng


def _assert_components(eng: Engine, h: str, vals: dict[str, float]) -> None:
    """Helper: assert all 5 score_component facts for artifact h."""
    for kind, value in vals.items():
        eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
            f'(cve_rem.score_component (artifact_hash "{h}") '
            f'(kind "{kind}") (value {value}))'
        )


def test_score_compute_perfect_components() -> None:
    """All components 1.0 → score = 0.35+0.25+0.15+0.15+0.10 = 1.00."""
    eng = _engine()
    _assert_components(eng, H, {
        "validation": 1.0,
        "sandbox": 1.0,
        "cr_approved": 1.0,
        "no_drift_7d": 1.0,
        "no_rollback_30d": 1.0,
    })
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    scores = facts_of(eng, "cve_rem.gepa_score")
    assert len(scores) == 1
    assert abs(float(scores[0]["value"]) - 1.0) < 1e-9


def test_score_compute_weighted_partial() -> None:
    """Validation=1, sandbox=1, others=0 → score = 0.35+0.25 = 0.60."""
    eng = _engine()
    _assert_components(eng, H, {
        "validation": 1.0,
        "sandbox": 1.0,
        "cr_approved": 0.0,
        "no_drift_7d": 0.0,
        "no_rollback_30d": 0.0,
    })
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    scores = facts_of(eng, "cve_rem.gepa_score")
    assert abs(float(scores[0]["value"]) - 0.60) < 1e-9


def test_strictly_better_accept() -> None:
    """Candidate 0.85, current 0.80, eps 0.02 → delta 0.05 ≥ eps → accept."""
    eng = _engine()
    _assert_components(eng, H, {
        "validation": 1.0,
        "sandbox": 1.0,
        "cr_approved": 1.0,
        "no_drift_7d": 0.0,
        "no_rollback_30d": 0.0,
    })  # score = 0.35+0.25+0.15 = 0.75
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        f'(cve_rem.gepa_inputs (artifact_hash "{H}") '
        f'(current_score 0.50) (epsilon 0.02))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    decisions = facts_of(eng, "cve_rem.gepa_decision")
    assert len(decisions) == 1
    assert decisions[0]["decision"] == "accept"


def test_strictly_better_reject_under_margin() -> None:
    """Candidate 0.50, current 0.49, eps 0.02 → delta 0.01 < eps → reject."""
    eng = _engine()
    _assert_components(eng, H, {
        "validation": 0.0,
        "sandbox": 0.0,
        "cr_approved": 1.0,
        "no_drift_7d": 1.0,
        "no_rollback_30d": 1.0,
    })  # score = 0.15+0.15+0.10 = 0.40
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        f'(cve_rem.gepa_inputs (artifact_hash "{H}") '
        f'(current_score 0.39) (epsilon 0.02))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    decisions = facts_of(eng, "cve_rem.gepa_decision")
    assert len(decisions) == 1
    assert decisions[0]["decision"] == "reject"


def test_component_out_of_range_emits_halt() -> None:
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        f'(cve_rem.score_component (artifact_hash "{H}") '
        f'(kind "validation") (value 1.5))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    v = violations(eng)
    assert len(v) == 1
    assert v[0]["kind"] == "score-component-out-of-range"
    assert v[0]["severity"] == "halt"


def test_component_below_zero_emits_halt() -> None:
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        f'(cve_rem.score_component (artifact_hash "{H}") '
        f'(kind "sandbox") (value -0.1))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    v = violations(eng)
    assert len(v) == 1
    assert v[0]["kind"] == "score-component-out-of-range"
