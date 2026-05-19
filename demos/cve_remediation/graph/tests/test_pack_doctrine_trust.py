# SPDX-License-Identifier: Apache-2.0
"""Integration: ``cve_rem.doctrine_trust`` — CLIPS round-trip tests."""

from __future__ import annotations

import pytest
from fathom import Engine

from ._pack_helpers import load_pack_rules, violations

pytestmark = pytest.mark.integration


def _engine() -> Engine:
    eng = Engine(default_decision="allow")
    load_pack_rules(eng, "cve_rem.doctrine_trust")
    return eng


def test_trusted_source_no_violation() -> None:
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.doctrine_source (id "mitre-attack") '
        '(source_class "trusted-doctrine") (corpus_version_pin "v15.1") '
        '(corpus_sha256 "abc123"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    assert violations(eng) == []


def test_untrusted_source_class_emits_halt() -> None:
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.doctrine_source (id "blog-post") '
        '(source_class "untrusted") (corpus_version_pin "x") '
        '(corpus_sha256 "y"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    v = violations(eng)
    assert len(v) == 1
    assert v[0]["kind"] == "doctrine-source-class-mismatch"
    assert v[0]["severity"] == "halt"


def test_active_manifest_in_allowlist_no_violation() -> None:
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.doctrine_manifest (manifest_hash "h1") '
        '(signed_at "t") (signed_by "krakntrust"))'
    )
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.allowlist_entry (manifest_hash "h1") (active "true"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    assert violations(eng) == []


def test_manifest_missing_from_allowlist_emits_halt() -> None:
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.doctrine_manifest (manifest_hash "h-rogue") '
        '(signed_at "t") (signed_by "unknown"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    v = violations(eng)
    assert len(v) == 1
    assert v[0]["kind"] == "doctrine-manifest-unallowlisted"


def test_deactivated_manifest_in_use_emits_halt() -> None:
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.doctrine_manifest (manifest_hash "h-old") '
        '(signed_at "t") (signed_by "krakntrust"))'
    )
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.allowlist_entry (manifest_hash "h-old") (active "false"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    v = violations(eng)
    kinds = {x["kind"] for x in v}
    # Deactivated AND missing-active-entry both fire; both are halt.
    assert "doctrine-manifest-deactivated" in kinds


def test_pin_sha_divergence_emits_halt() -> None:
    """Same pin, divergent sha across two source facts → supply-chain halt."""
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.doctrine_source (id "src-a") '
        '(source_class "trusted-doctrine") (corpus_version_pin "v15.1") '
        '(corpus_sha256 "hash-aaa"))'
    )
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.doctrine_source (id "src-b") '
        '(source_class "trusted-doctrine") (corpus_version_pin "v15.1") '
        '(corpus_sha256 "hash-bbb"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    v = violations(eng)
    assert any(x["kind"] == "doctrine-pin-sha-divergence" for x in v)
