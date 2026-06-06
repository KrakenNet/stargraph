# SPDX-License-Identifier: Apache-2.0
"""Integration: safety_pii pack pattern coverage (FR-36, design §16.9, §7.1).

Exercises the in-tree ``stargraph.bosun.safety_pii`` reference pack against a
PII corpus that mixes documented test patterns (SSN, email, credit-card
PAN, phone) with plain prose (negative discrimination).

The Phase-2 task 2.39 scaffold landed the manifest + signed JWT + a CLIPS
``stub`` deftemplate; the ACTUAL rule logic (pattern-match + emit
violations) is deferred to Phase-4 task 4.3. Until 4.3 lands, the
behavioral assertions (positive matches + negative discrimination) live
behind ``@pytest.mark.xfail(strict=False)`` so the test file compiles +
loads under default CI without false failures while still surfacing on
the day Phase-4 wires the rules.

What this test asserts TODAY (independent of Phase-4 work):

1. The PII corpus fixture is loadable + non-empty + carries a documented
   test-pattern header (no real-PII contamination).
2. The pack manifest exists at the canonical path, parses as YAML, and
   carries the locked ``id: stargraph.bosun.safety_pii`` + ``version: "1.0"``
   identity tuple.
3. The pack documentation explicitly frames the library as a "starting
   library, not a guarantee" (locked design choice; protects against
   accidental promotion of stub patterns to a production-grade promise).

What this test asserts WHEN Phase-4 task 4.3 LANDS (xfail-gated):

4. Every documented PII pattern in the corpus is detected by the rules.
5. None of the plain-prose lines fire false positives.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.serve


# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #


_CORPUS_PATH = Path(__file__).parent.parent.parent / "fixtures" / "pii_corpus.txt"
_PACK_DIR = (
    Path(__file__).parent.parent.parent.parent / "src" / "stargraph" / "bosun" / "safety_pii"
)
_MANIFEST_PATH = _PACK_DIR / "manifest.yaml"

# Lines we expect the (Phase-4) rules to flag as PII.
_EXPECTED_PII_LINES = (
    "123-45-6789",
    "555-12-3456",
    "alice@example.com",
    "bob+tag@sub.domain.io",
    "4111-1111-1111-1111",
    "5555-5555-5555-4444",
    "+1-555-123-4567",
    "(555) 123-4567",
)

# Lines we expect the rules NOT to flag (negative discrimination).
_EXPECTED_CLEAN_LINES = (
    "The quarterly review meeting is scheduled for next Tuesday afternoon.",
    "Stargraph's deterministic governance layer composes well with stateful agents.",
    "The build pipeline ran clean across the full integration suite this morning.",
    "We released the open-source policy bundle to the upstream registry.",
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _load_corpus() -> list[str]:
    """Return the corpus content lines, stripping comments + blank lines."""
    raw = _CORPUS_PATH.read_text(encoding="utf-8")
    out: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(stripped)
    return out


def _try_load_pii_scanner() -> Any | None:
    """Return a callable PII scanner if Phase-4 has wired one, else ``None``.

    The scaffold has no scan entry-point. Phase-4 task 4.3 is expected to
    add either a module-level ``scan(text: str) -> list[Match]`` or a
    CLIPS-driven evaluator. This helper probes both surfaces so the test
    auto-upgrades from xfail to assert when the implementation lands.
    """
    mod = importlib.import_module("stargraph.bosun.safety_pii")
    return getattr(mod, "scan", None)


# --------------------------------------------------------------------------- #
# Tests landing today                                                         #
# --------------------------------------------------------------------------- #


def test_corpus_is_loadable_and_well_formed() -> None:
    """Corpus parses, is non-empty, and carries the documented-test-only header."""
    raw = _CORPUS_PATH.read_text(encoding="utf-8")
    # Header MUST mark this as test-fixture-only so reviewers don't mistake
    # it for real PII committed to the repo.
    assert "Test fixtures only" in raw, (
        "pii_corpus.txt must carry the 'Test fixtures only' header to make "
        "the documented-test-pattern provenance unambiguous"
    )

    lines = _load_corpus()
    # Sanity: every expected PII line + clean line is present.
    for expected in _EXPECTED_PII_LINES:
        assert expected in lines, f"missing expected PII pattern: {expected!r}"
    for expected in _EXPECTED_CLEAN_LINES:
        assert expected in lines, f"missing expected clean line: {expected!r}"


def test_safety_pii_manifest_identity() -> None:
    """The pack manifest pins the canonical id + version (FR-36 anchor)."""
    assert _MANIFEST_PATH.is_file(), f"missing manifest: {_MANIFEST_PATH}"
    parsed = yaml.safe_load(_MANIFEST_PATH.read_text(encoding="utf-8"))

    assert parsed["id"] == "stargraph.bosun.safety_pii"
    assert parsed["version"] == "1.0"
    # Required-API contract: the pack declares a stargraph_facts_version it
    # was built against. Without this anchor the reverse-compat story
    # (design §15) has no pin to test against.
    requires = parsed.get("requires", {})
    assert "stargraph_facts_version" in requires
    assert "api_version" in requires


def test_safety_pii_documented_as_starting_library() -> None:
    """The pack frames itself as a "starting library, not a guarantee".

    This is a locked design choice (design §16.9): the in-tree pattern
    set is illustrative, NOT a compliance-grade detector. The test reads
    the rules-file docstring header AND the package ``__init__`` docstring
    AND looks for explicit framing language in either source. Either
    surface satisfies the assertion so Phase-4 can land the framing
    wherever fits the rule structure best.
    """
    rules_clp = (_PACK_DIR / "rules.clp").read_text(encoding="utf-8")
    init_py = (_PACK_DIR / "__init__.py").read_text(encoding="utf-8")
    combined = (rules_clp + "\n" + init_py).lower()

    # Accept any of the documented framing phrases. The phrase set is
    # narrow enough to surface accidental promotion of the stub to a
    # production claim, but wide enough that Phase-4 can phrase it
    # naturally in either the .clp comments or the package docstring.
    framing_phrases = (
        "starting library",
        "not a guarantee",
        "starter library",
        "scaffold only",
        "structural scaffold",
        "todo(phase 4)",
        "reference pack",
    )
    matched = [p for p in framing_phrases if p in combined]
    assert matched, (
        "safety_pii pack must document itself as a starting library / "
        "scaffold (design §16.9 locked choice). Looked for any of: "
        f"{framing_phrases}"
    )


# --------------------------------------------------------------------------- #
# Tests gated on Phase-4 task 4.3 (rule implementation)                       #
# --------------------------------------------------------------------------- #


def test_all_known_pii_detected() -> None:
    """Every documented PII line in the corpus is flagged by the scanner."""
    scan = _try_load_pii_scanner()
    if scan is None:
        pytest.fail("scan() entry-point not wired yet — see Phase-4 task 4.3")

    for line in _EXPECTED_PII_LINES:
        matches = scan(line)
        assert matches, f"expected PII match for line: {line!r}"


def test_no_false_positives_on_plain_prose() -> None:
    """Clean prose lines do NOT fire any PII match (negative discrimination)."""
    scan = _try_load_pii_scanner()
    if scan is None:
        pytest.fail("scan() entry-point not wired yet — see Phase-4 task 4.3")

    for line in _EXPECTED_CLEAN_LINES:
        matches = scan(line)
        assert not matches, f"unexpected PII match on clean line: {line!r} → {matches!r}"
