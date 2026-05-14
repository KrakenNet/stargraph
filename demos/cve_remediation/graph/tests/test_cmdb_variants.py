# SPDX-License-Identifier: Apache-2.0
"""Tests for mechanical CPE-token variant derivation + CMDB scoring.

Step 2 cheat-removal: replaces hand-authored ``_CMDB_PRODUCT_ALIASES``
and ``_CMDB_CATCHALL_NAMES`` with derivation driven from the CPE
token / vendor strings themselves. These tests assert that derivation
generalizes across vendors without per-vendor knowledge.
"""

from __future__ import annotations

from demos.cve_remediation.graph.real_nodes import (
    _derive_cpe_variants,
    _expand_product_aliases,
    _score_cmdb_candidate,
)


# ---------------------------------------------------------------------------
# Variant derivation
# ---------------------------------------------------------------------------


def test_variants_strip_software_suffix_and_emit_acronym():
    out = _derive_cpe_variants("adaptive_security_appliance_software", "cisco")
    assert "adaptive_security_appliance_software" in out
    assert "adaptive security appliance software" in out
    assert "adaptive_security_appliance" in out  # suffix stripped
    assert "ASA" in out  # acronym from 3 underscore tokens
    assert "cisco ASA" in out  # vendor+acronym join


def test_variants_single_token_emits_vendor_join_only():
    out = _derive_cpe_variants("log4j", "apache")
    assert out[0] == "log4j"
    assert "apache log4j" in out
    # No 2+ token underscore split → no acronym entry
    assert not any(len(v) == 2 and v.isupper() for v in out)


def test_variants_empty_and_too_short_return_empty():
    assert _derive_cpe_variants("") == []
    assert _derive_cpe_variants("a") == []


def test_variants_dedupe_and_preserve_order():
    out = _derive_cpe_variants("log4j", "")
    assert len(out) == len(set(v.lower() for v in out))
    assert out[0] == "log4j"


def test_expand_product_aliases_shim_matches_derive():
    a = _derive_cpe_variants("connect_secure", "ivanti")
    b = _expand_product_aliases("connect_secure", "ivanti")
    assert a == b


# ---------------------------------------------------------------------------
# Scoring with mechanical aliases
# ---------------------------------------------------------------------------


def test_scorer_matches_acronym_via_alias_tokens():
    """`Cisco ASA` row scores high for CPE `adaptive_security_appliance_software`."""
    variants = _derive_cpe_variants("adaptive_security_appliance_software", "cisco")
    row = {"name": "Cisco ASA", "vendor": "cisco"}
    s, q = _score_cmdb_candidate(
        row, "cisco", "adaptive_security_appliance_software",
        extra_aliases=variants,
    )
    assert q in {"high", "medium"}
    assert s >= 40


def test_scorer_direct_full_match_still_wins():
    variants = _derive_cpe_variants("log4j", "apache")
    row = {"name": "Apache Log4j 2", "vendor": "apache"}
    s, q = _score_cmdb_candidate(row, "apache", "log4j", extra_aliases=variants)
    assert q == "high"
    assert s >= 80


def test_scorer_rejects_structural_catchall():
    """Long generic CI names with zero matching tokens → reject."""
    variants = _derive_cpe_variants("log4j", "apache")
    row = {
        "name": "Microsoft Office Package for the Web Bundled Suite Pro",
        "vendor": "microsoft",
    }
    s, q = _score_cmdb_candidate(row, "apache", "log4j", extra_aliases=variants)
    assert q == "reject"
    assert s < 20


def test_scorer_hard_rejects_short_product_token():
    """Tokens shorter than 4 chars are always rejected (substring noise)."""
    s, q = _score_cmdb_candidate({"name": "Cisco ASA", "vendor": "cisco"}, "cisco", "asa")
    assert q == "reject"
    assert s == 0


def test_scorer_no_aliases_falls_back_to_product_coverage():
    """Backwards-compatible: extra_aliases omitted ⇒ behaviour unchanged."""
    row = {"name": "Apache Log4j 2", "vendor": "apache"}
    s, _ = _score_cmdb_candidate(row, "apache", "log4j")
    s_with, _ = _score_cmdb_candidate(row, "apache", "log4j", extra_aliases=[])
    assert s == s_with
