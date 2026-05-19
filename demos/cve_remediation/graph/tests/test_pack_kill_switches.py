# SPDX-License-Identifier: Apache-2.0
"""Integration: ``cve_rem.kill_switches`` — CLIPS round-trip tests.

Covers all 4 metric kinds (rollback-rate, sandbox-mismatch, cross-bucket,
stuck-state) and all 3 quorum-collect rules for halt-rollback-in-flight.
"""

from __future__ import annotations

import pytest
from fathom import Engine

from ._pack_helpers import load_pack_rules, violations

pytestmark = pytest.mark.integration


def _engine() -> Engine:
    eng = Engine(default_decision="allow")
    load_pack_rules(eng, "cve_rem.kill_switches")
    return eng


# --- error-budget rules ------------------------------------------------------


def test_rollback_rate_under_threshold_no_violation() -> None:
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.metric (kind "rollback-rate") (window_hours 24) '
        '(value 0.03) (threshold 0.05) (run_id "fleet") (computed_at "t"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    assert violations(eng) == []


def test_rollback_rate_over_threshold_emits_halt() -> None:
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.metric (kind "rollback-rate") (window_hours 24) '
        '(value 0.07) (threshold 0.05) (run_id "fleet") (computed_at "t"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    v = violations(eng)
    assert len(v) == 1
    assert v[0]["kind"] == "rollback-rate-exceeded"
    assert v[0]["severity"] == "halt"


def test_sandbox_mismatch_over_threshold_emits_halt() -> None:
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.metric (kind "sandbox-mismatch") (window_hours 24) '
        '(value 0.05) (threshold 0.03) (run_id "fleet") (computed_at "t"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    v = violations(eng)
    assert len(v) == 1
    assert v[0]["kind"] == "sandbox-mismatch-exceeded"


def test_cross_bucket_value_ge_one_emits_halt() -> None:
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.metric (kind "cross-bucket") (window_hours 1) '
        '(value 1) (threshold 0) (run_id "fleet") (computed_at "t"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    v = violations(eng)
    assert len(v) == 1
    assert v[0]["kind"] == "cross-bucket"


def test_stuck_state_over_336h_emits_info_only() -> None:
    """14 days = 336 hours; severity is info, not halt."""
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.metric (kind "stuck-state") (window_hours 400) '
        '(value 1) (threshold 0) (run_id "r-stuck") (computed_at "t"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    v = violations(eng)
    assert len(v) == 1
    assert v[0]["kind"] == "stuck-state"
    assert v[0]["severity"] == "info"


# --- single-signer kill-signal RBAC ------------------------------------------


def test_halt_new_pipeline_owner_emits_halt() -> None:
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.kill_signal (kind "halt-new") (actor "alice") '
        '(role "pipeline-owner") (run_id "fleet") (signature_id "sig-1"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    v = violations(eng)
    assert any(x["kind"] == "kill-signal-halt-new" for x in v)


def test_halt_new_unauthorized_role_no_violation() -> None:
    """netops-lead is NOT authorized to fire halt-new alone."""
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.kill_signal (kind "halt-new") (actor "carol") '
        '(role "netops-lead") (run_id "fleet") (signature_id "sig-2"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    assert violations(eng) == []


# --- 2-of-3 quorum on halt-rollback-in-flight --------------------------------


def test_rollback_quorum_pipeline_owner_plus_security_eng() -> None:
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.kill_signal (kind "halt-rollback-in-flight") '
        '(actor "alice") (role "pipeline-owner") (run_id "r-1") (signature_id "s1"))'
    )
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.kill_signal (kind "halt-rollback-in-flight") '
        '(actor "bob") (role "security-eng") (run_id "r-1") (signature_id "s2"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    v = violations(eng)
    assert any(x["kind"] == "kill-signal-rollback-quorum" for x in v)


def test_rollback_single_signer_no_quorum() -> None:
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.kill_signal (kind "halt-rollback-in-flight") '
        '(actor "alice") (role "pipeline-owner") (run_id "r-1") (signature_id "s1"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    assert violations(eng) == []
