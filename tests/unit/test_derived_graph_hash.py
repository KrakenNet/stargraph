# SPDX-License-Identifier: Apache-2.0
"""TDD-RED: ``derived_graph_hash`` byte-sequence (FR-27, design §3.8.3).

Pins the *exact* byte layout for the cf-derived graph hash per design
§3.8.3 *before* the implementation lands in task 3.33. Currently RED
because :mod:`stargraph.replay.counterfactual` does not yet exist; the
``importlib.import_module`` call fails first with :class:`ImportError`.

Byte sequence under test (design §3.8.3, Learning E):

    sha256(
        b"stargraph-cf-v1"        # 12-byte domain tag
        + b"\\x00"             # 1-byte separator
        + original_hash.encode("ascii")   # 64 bytes hex
        + b"\\x00"             # 1-byte separator
        + rfc8785.dumps(mutation.model_dump(exclude_none=True, mode="json"))
    )

Cases:

1. ``test_derived_hash_matches_documented_byte_sequence`` -- the
   function output equals the locally re-computed sha256 of the exact
   byte sequence above (cross-implementation check).
2. ``test_derived_hash_is_64_char_hex`` -- output is a 64-char lowercase
   hex string (sha256 hex digest).
3. ``test_derived_hash_distinct_from_original`` -- the derived hash is
   never equal to the original hash (domain separation).
4. ``test_derived_hash_changes_with_mutation`` -- two mutations
   producing different canonical bytes yield different derived hashes.
5. ``test_derived_hash_uses_jcs_canonicalization`` -- semantically
   equal mutations (different key-insertion order) yield the *same*
   derived hash (RFC 8785 lex-sorted keys, JCS-canonical).
"""

from __future__ import annotations

import hashlib
import importlib
from typing import Any

import rfc8785

_ORIGINAL_HASH = "a" * 64  # plausible 64-char hex sha256


def _module() -> Any:
    """Import ``stargraph.replay.counterfactual`` (TDD-RED: not yet built)."""
    return importlib.import_module("stargraph.replay.counterfactual")


def _expected(original: str, mutation: Any) -> str:
    """Re-compute the design §3.8.3 byte sequence locally (cross-check)."""
    canonical = rfc8785.dumps(mutation.model_dump(exclude_none=True, mode="json"))
    h = hashlib.sha256()
    h.update(b"stargraph-cf-v1")
    h.update(b"\x00")
    h.update(original.encode("ascii"))
    h.update(b"\x00")
    h.update(canonical)
    return h.hexdigest()


def test_derived_hash_matches_documented_byte_sequence() -> None:
    """Output equals locally-recomputed sha256 of the exact §3.8.3 bytes."""
    mod = _module()
    mutation = mod.CounterfactualMutation(state_overrides={"x": 1})
    got: str = mod.derived_graph_hash(_ORIGINAL_HASH, mutation)
    assert got == _expected(_ORIGINAL_HASH, mutation)


def test_derived_hash_is_64_char_hex() -> None:
    """Output is a 64-char lowercase hex sha256 digest."""
    mod = _module()
    mutation = mod.CounterfactualMutation()
    got: str = mod.derived_graph_hash(_ORIGINAL_HASH, mutation)
    assert len(got) == 64
    assert all(c in "0123456789abcdef" for c in got)


def test_derived_hash_distinct_from_original() -> None:
    """Domain separation: derived hash never equals original (cf-prefix)."""
    mod = _module()
    mutation = mod.CounterfactualMutation()
    got: str = mod.derived_graph_hash(_ORIGINAL_HASH, mutation)
    assert got != _ORIGINAL_HASH


def test_derived_hash_changes_with_mutation() -> None:
    """Distinct mutations produce distinct derived hashes."""
    mod = _module()
    m1 = mod.CounterfactualMutation(state_overrides={"x": 1})
    m2 = mod.CounterfactualMutation(state_overrides={"x": 2})
    assert mod.derived_graph_hash(_ORIGINAL_HASH, m1) != mod.derived_graph_hash(_ORIGINAL_HASH, m2)


def test_derived_hash_uses_jcs_canonicalization() -> None:
    """JCS canonicalization: key order in source data MUST NOT affect output."""
    mod = _module()
    m1 = mod.CounterfactualMutation(state_overrides={"a": 1, "b": 2})
    m2 = mod.CounterfactualMutation(state_overrides={"b": 2, "a": 1})
    assert mod.derived_graph_hash(_ORIGINAL_HASH, m1) == mod.derived_graph_hash(_ORIGINAL_HASH, m2)
