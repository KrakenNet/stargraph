# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :mod:`stargraph.ir._ids` (FR-30, FR-31, AC-14.3, AC-14.5).

Covers:

* :func:`new_run_id` / :func:`new_checkpoint_id` produce 36-char UUIDv7
  strings (8-4-4-4-12 hex with version nibble ``7``).
* :func:`fact_content_hash` produces a 32-char BLAKE2b hex digest
  (``digest_size=16``) and is stable under key reordering (sort_keys).
* :func:`_slug` lowercases, collapses non-alphanumerics to ``-``, strips
  leading/trailing ``-``, and truncates to 24 characters.
* :func:`autogen_node_ids` / ``autogen_rule_ids`` / ``autogen_pack_ids``:
    - leave existing IDs untouched (persistence on first save -- AC-14.5),
    - fill missing IDs from ``name`` via ``_slug``,
    - suffix collisions ``-2``, ``-3``, ...,
    - synthesize a placeholder when no ``name`` and no other field exists.
"""

from __future__ import annotations

import re
import uuid

import pytest

from stargraph.ir._ids import (
    _slug,
    autogen_node_ids,
    autogen_pack_ids,
    autogen_rule_ids,
    fact_content_hash,
    new_checkpoint_id,
    new_run_id,
)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}$",
)


# ---------------------------------------------------------------------------
# UUIDv7 (FR-30)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_new_run_id_is_uuidv7_shape() -> None:
    """:func:`new_run_id` returns a 36-char string with the v7 nibble set."""
    rid = new_run_id()
    assert isinstance(rid, str)
    assert len(rid) == 36
    assert _UUID_RE.match(rid)
    # Round-trip parses cleanly through stdlib ``uuid``.
    parsed = uuid.UUID(rid)
    assert parsed.version == 7


@pytest.mark.unit
def test_new_checkpoint_id_is_uuidv7_shape() -> None:
    """:func:`new_checkpoint_id` also returns a 36-char v7 string."""
    cid = new_checkpoint_id()
    assert _UUID_RE.match(cid)
    assert uuid.UUID(cid).version == 7


@pytest.mark.unit
def test_run_ids_are_unique_across_calls() -> None:
    """Successive calls return distinct ids (sortable monotonic clock)."""
    ids = {new_run_id() for _ in range(5)}
    assert len(ids) == 5


# ---------------------------------------------------------------------------
# fact_content_hash (FR-31)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_fact_content_hash_is_32_char_hex_digest() -> None:
    """BLAKE2b ``digest_size=16`` -> 32 hex chars."""
    digest = fact_content_hash({"k": "v"})
    assert len(digest) == 32
    assert re.fullmatch(r"[0-9a-f]{32}", digest)


@pytest.mark.unit
def test_fact_content_hash_is_stable_under_key_reordering() -> None:
    """``sort_keys=True`` makes the hash insensitive to dict insertion order."""
    a = fact_content_hash({"a": 1, "b": 2})
    b = fact_content_hash({"b": 2, "a": 1})
    assert a == b


@pytest.mark.unit
def test_fact_content_hash_changes_with_content() -> None:
    """Different content -> different digest (sanity)."""
    a = fact_content_hash({"a": 1})
    b = fact_content_hash({"a": 2})
    assert a != b


# ---------------------------------------------------------------------------
# _slug
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_slug_lowercases_and_collapses_non_alphanumerics() -> None:
    """Whitespace and punctuation collapse to a single ``-``; output is lowercase."""
    assert _slug("Hello World!") == "hello-world"
    assert _slug("a__b__c") == "a-b-c"
    assert _slug("foo BAR baz") == "foo-bar-baz"


@pytest.mark.unit
def test_slug_strips_leading_and_trailing_dashes() -> None:
    """Edge punctuation is stripped after collapse."""
    assert _slug("___hello___") == "hello"
    assert _slug("...trim...") == "trim"


@pytest.mark.unit
def test_slug_truncates_to_24_chars() -> None:
    """Long inputs are clipped at 24 characters."""
    out = _slug("a" * 100)
    assert len(out) == 24
    assert out == "a" * 24


@pytest.mark.unit
def test_slug_empty_input_returns_empty_string() -> None:
    """Empty / all-punctuation input -> ``""``."""
    assert _slug("") == ""
    assert _slug("___") == ""


# ---------------------------------------------------------------------------
# autogen_*_ids: persistence + collision suffixing (AC-14.3, AC-14.5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_autogen_preserves_existing_ids() -> None:
    """AC-14.5: existing ``id`` fields are left untouched (persistence on first save)."""
    items = [{"id": "n1", "name": "Alpha"}, {"id": "n2", "name": "Beta"}]
    out = autogen_node_ids(items)
    assert [o["id"] for o in out] == ["n1", "n2"]
    # Returned items are independent dicts (no aliasing back to inputs).
    assert out[0] is not items[0]


@pytest.mark.unit
def test_autogen_fills_missing_ids_from_name_via_slug() -> None:
    """Missing IDs are filled with ``_slug(item['name'])``."""
    items = [{"name": "Hello World"}, {"name": "Another One"}]
    out = autogen_node_ids(items)
    assert out[0]["id"] == "hello-world"
    assert out[1]["id"] == "another-one"


@pytest.mark.unit
def test_autogen_suffixes_collisions_with_increasing_integers() -> None:
    """Repeated slugs get ``-2``, ``-3``, ... suffixes so all IDs stay unique."""
    items = [{"name": "Same Name"}, {"name": "Same Name"}, {"name": "Same Name"}]
    out = autogen_rule_ids(items)
    ids = [o["id"] for o in out]
    assert ids == ["same-name", "same-name-2", "same-name-3"]


@pytest.mark.unit
def test_autogen_suffix_avoids_collision_with_pre_existing_id() -> None:
    """A pre-existing ``id`` reserved upfront is not overwritten by a later slug-of-name."""
    items = [
        {"id": "tagged", "name": "ignored"},
        {"name": "Tagged"},  # would slug to "tagged" -> collision -> "tagged-2"
    ]
    out = autogen_pack_ids(items)
    assert out[0]["id"] == "tagged"
    assert out[1]["id"] == "tagged-2"


@pytest.mark.unit
def test_autogen_synthesizes_placeholder_when_no_name() -> None:
    """No ``name`` and no other usable value -> ``item-2`` placeholder pattern."""
    items: list[dict[str, object]] = [{}, {}]
    out = autogen_node_ids(items)
    # First item: base = _slug("") = "" -> not seen -> goes to fallback "item-2".
    # Second item: same, but "item-2" already taken -> "item-3".
    assert out[0]["id"] == "item-2"
    assert out[1]["id"] == "item-3"


@pytest.mark.unit
def test_autogen_uses_first_value_when_name_missing_but_other_field_present() -> None:
    """When ``name`` is absent, the slug falls back to the first available value."""
    items = [{"label": "Pretty Label"}]
    out = autogen_node_ids(items)
    # ``next(iter(item.values()), "")`` picks the first dict value.
    assert out[0]["id"] == "pretty-label"


@pytest.mark.unit
def test_autogen_rule_ids_and_pack_ids_share_the_uniform_implementation() -> None:
    """The three public wrappers all delegate to the same ``_autogen_ids`` body."""
    items = [{"name": "X"}, {"name": "X"}]
    assert (
        autogen_node_ids(list(items))
        == autogen_rule_ids(list(items))
        == autogen_pack_ids(
            list(items),
        )
    )
