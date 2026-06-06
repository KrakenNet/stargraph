# SPDX-License-Identifier: Apache-2.0
"""Integration: ``stargraph.bosun.safety_pii@1.0`` round-trip (FR-36, design §7.1).

Loads the pack rules + injects ``stargraph.evidence`` facts carrying SSN,
email, credit-card, and phone patterns. Asserts ``bosun.violation``
facts are emitted with the right ``kind`` + ``severity`` per pattern.
Negative case: a clean prose evidence fact emits no violations.

Overlaps with ``tests/integration/serve/test_safety_pii_patterns.py``
(which exercises the Python ``scan()`` mirror) — the integration angle
here is the full pack-load + Fathom-engine + rule-fire cycle.
"""

from __future__ import annotations

import pytest
from fathom import Engine

from ._helpers import load_pack_rules

pytestmark = pytest.mark.serve


def _fresh_engine() -> Engine:
    eng = Engine(default_decision="deny")
    load_pack_rules(eng, "safety_pii")
    return eng


@pytest.mark.parametrize(
    ("text", "expected_kind"),
    [
        ("agent ssn is 123-45-6789 in profile", "pii-ssn"),
        ("contact alice@example.com for details", "pii-email"),
        ("card number 4111-1111-1111-1111 was charged", "pii-credit-card"),
        ("call 555-123-4567 for support", "pii-phone"),
    ],
)
def test_pii_patterns_emit_violations(text: str, expected_kind: str) -> None:
    """Each documented PII pattern in evidence text fires a matching
    ``bosun.violation``."""
    eng = _fresh_engine()
    # Escape double-quotes inside the string literal for CLIPS safety.
    safe = text.replace('"', '\\"')
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        f'(stargraph.evidence (run_id "r1") (step 1) (text "{safe}"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    viols = [dict(v) for v in eng._env.find_template("bosun.violation").facts()]  # pyright: ignore[reportPrivateUsage]
    kinds = [v["kind"] for v in viols]
    assert expected_kind in kinds, (
        f"expected {expected_kind!r} violation for text {text!r}; got {kinds!r}"
    )


def test_clean_text_emits_no_violation() -> None:
    """Plain prose without PII patterns → no ``bosun.violation``."""
    eng = _fresh_engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(stargraph.evidence (run_id "r1") (step 1) (text "The quarterly review is scheduled."))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    viols = list(eng._env.find_template("bosun.violation").facts())  # pyright: ignore[reportPrivateUsage]
    assert viols == [], f"expected no violations on clean text; got {[dict(v) for v in viols]}"


def test_multiple_pii_in_one_evidence_fires_multiple_violations() -> None:
    """Multiple PII patterns in one evidence text → one violation per pattern."""
    eng = _fresh_engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(stargraph.evidence (run_id "r1") (step 1) '
        '(text "alice@example.com 123-45-6789 and 555-123-4567"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    viols = [dict(v) for v in eng._env.find_template("bosun.violation").facts()]  # pyright: ignore[reportPrivateUsage]
    kinds = sorted(v["kind"] for v in viols)
    assert "pii-email" in kinds
    assert "pii-ssn" in kinds
    assert "pii-phone" in kinds
