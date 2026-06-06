# SPDX-License-Identifier: Apache-2.0
"""Bosun ``safety_pii`` reference pack — Phase-4 implementation (task 4.3).

The pack ``rules.clp`` carries the CLIPS rules that match PII patterns
inside ``stargraph.evidence`` facts (via Fathom's built-in
``fathom-matches`` user-function). For Python-side callers that need a
direct test-corpus scanner without booting a Fathom engine, this module
exposes :func:`scan` — a thin regex helper that mirrors the patterns
embedded in the CLIPS rules.

**Locked design choice**: this pack is a starting library, NOT a
guarantee. The patterns are illustrative; production-grade PII
detection (Luhn-checked PANs, locale-aware phone validation, etc.) is
out of scope for v1. Operators are expected to extend per their own
data classification policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["Match", "scan"]


# Patterns mirror the CLIPS rules in ``rules.clp``. Both surfaces stay
# in lockstep: a change here MUST be reflected in the .clp regex literals
# (and vice versa). The shared semantic test
# ``test_safety_pii_pack.py`` exercises the Fathom path; the
# ``test_safety_pii_patterns.py`` regression test calls :func:`scan`.
_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("pii-ssn", r"[0-9]{3}-[0-9]{2}-[0-9]{4}", "halt"),
    ("pii-email", r"[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "warn"),
    ("pii-credit-card", r"[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{4}", "halt"),
    (
        "pii-phone",
        r"(\+?1[-. ]?)?(\([0-9]{3}\)|[0-9]{3})[-. ][0-9]{3}[-. ][0-9]{4}",
        "warn",
    ),
)
_COMPILED: tuple[tuple[str, re.Pattern[str], str], ...] = tuple(
    (kind, re.compile(p), severity) for (kind, p, severity) in _PATTERNS
)


@dataclass(frozen=True)
class Match:
    """One PII pattern hit (test-fixture surface).

    ``kind`` is the pattern label (e.g. ``pii-ssn``); ``span`` is the
    matched substring; ``severity`` mirrors the CLIPS rule severity.
    """

    kind: str
    span: str
    severity: str


def scan(text: str) -> list[Match]:
    """Return every PII match in ``text`` (test-fixture entry-point).

    Mirrors the regex patterns embedded in ``rules.clp``. Used by
    ``tests/integration/serve/test_safety_pii_patterns.py`` to assert
    starter-library coverage without booting a Fathom engine.
    """
    out: list[Match] = []
    for kind, pat, severity in _COMPILED:
        for m in pat.finditer(text):
            out.append(Match(kind=kind, span=m.group(0), severity=severity))
    return out
