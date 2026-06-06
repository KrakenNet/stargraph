# SPDX-License-Identifier: Apache-2.0
"""Integration: ``stargraph.bosun.retries@1.0`` round-trip (FR-37, design §7.1).

Loads the pack rules + injects ``stargraph.error recoverable=TRUE`` facts.
Asserts:

1. First-attempt error → ``action.retry`` with ``delay_seconds=2`` +
   ``attempt=1`` (exponential backoff floor).
2. Mid-attempt error (e.g. attempt=3) → exponential delay (=8) +
   matching ``attempt`` slot.
3. Past the cap (attempt=6) → ``bosun.violation kind=retry-exhausted
   severity=halt`` instead of another retry.
"""

from __future__ import annotations

import pytest
from fathom import Engine

from ._helpers import load_pack_rules

pytestmark = pytest.mark.serve


def _fresh_engine() -> Engine:
    eng = Engine(default_decision="deny")
    load_pack_rules(eng, "retries")
    return eng


def test_first_attempt_emits_retry_with_two_second_delay() -> None:
    """attempt=1 → action.retry delay_seconds=2 (2^1)."""
    eng = _fresh_engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(stargraph.error (run_id "r1") (step 1) (reason "timeout") (recoverable TRUE) (attempt 1))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    retries = [dict(r) for r in eng._env.find_template("action.retry").facts()]  # pyright: ignore[reportPrivateUsage]
    assert len(retries) == 1, f"expected exactly 1 retry; got {retries}"
    r = retries[0]
    assert r["run_id"] == "r1"
    assert r["attempt"] == 1
    # CLIPS ** returns float; compare numerically.
    assert int(r["delay_seconds"]) == 2


def test_third_attempt_emits_retry_with_eight_second_delay() -> None:
    """attempt=3 → action.retry delay_seconds=8 (2^3)."""
    eng = _fresh_engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(stargraph.error (run_id "r2") (step 5) (reason "timeout") (recoverable TRUE) (attempt 3))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    retries = [dict(r) for r in eng._env.find_template("action.retry").facts()]  # pyright: ignore[reportPrivateUsage]
    assert len(retries) == 1
    assert int(retries[0]["delay_seconds"]) == 8
    assert retries[0]["attempt"] == 3


def test_attempt_past_cap_emits_retry_exhausted_violation() -> None:
    """attempt=6 → bosun.violation kind=retry-exhausted severity=halt."""
    eng = _fresh_engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(stargraph.error (run_id "r3") (step 9) (reason "timeout") (recoverable TRUE) (attempt 6))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    viols = [dict(v) for v in eng._env.find_template("bosun.violation").facts()]  # pyright: ignore[reportPrivateUsage]
    assert len(viols) == 1, f"expected exactly 1 retry-exhausted violation; got {viols}"
    v = viols[0]
    assert v["kind"] == "retry-exhausted"
    assert v["severity"] == "halt"
    assert v["run_id"] == "r3"
    # And no further retry was emitted.
    retries = list(eng._env.find_template("action.retry").facts())  # pyright: ignore[reportPrivateUsage]
    assert retries == [], (
        f"expected no retry emitted past the cap; got {[dict(r) for r in retries]}"
    )
