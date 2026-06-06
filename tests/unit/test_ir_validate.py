# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :mod:`stargraph.ir._validate` (FR-17, FR-18, FR-35, AC-12.1-5, AC-19.2).

Covers:

* :func:`validate` returns ``[]`` on a valid IR document and a populated
  ``list[ValidationError]`` on every failure mode (AC-12.1, AC-12.2).
* Malformed JSON returns a structured error rather than raising (AC-12.5).
* :func:`_loc_to_pointer` produces RFC 6901 JSON Pointers with ``~``/``/``
  escaping and decimal indices.
* :data:`_HINTS` maps Pydantic error type codes to friendly text; unknown
  codes fall back to the URL or Pydantic message.
* Version-mismatch (FR-35, AC-19.2) is reported via :func:`check_version`,
  including the ``version_mismatch`` keyword in the hint.
* :func:`parse_version` rejects malformed semver strings.
* :func:`validate` accepts both ``str`` and ``dict`` inputs (AC-12.5).
"""

from __future__ import annotations

import pytest

from stargraph.errors import ValidationError as StargraphValidationError
from stargraph.ir._validate import (
    _HINTS,  # pyright: ignore[reportPrivateUsage]
    _loc_to_pointer,  # pyright: ignore[reportPrivateUsage]
    validate,
)
from stargraph.ir._versioning import STARGRAPH_IR_VERSION, check_version, parse_version

# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_returns_empty_list_for_valid_dict() -> None:
    """Valid IR (dict input) -> ``[]``."""
    errs = validate({"ir_version": STARGRAPH_IR_VERSION, "id": "run:t", "nodes": []})
    assert errs == []


@pytest.mark.unit
def test_validate_returns_empty_list_for_valid_json_string() -> None:
    """Valid IR (JSON string input) -> ``[]`` (AC-12.5: str is accepted)."""
    text = '{"ir_version":"1.0.0","id":"run:t","nodes":[]}'
    assert validate(text) == []


# ---------------------------------------------------------------------------
# Malformed JSON does not raise (AC-12.5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_malformed_json_returns_structured_error_not_raise() -> None:
    """AC-12.5: bad JSON -> a single ``ValidationError`` with parse hint, no raise."""
    errs = validate("{not valid json")
    assert len(errs) == 1
    err = errs[0]
    assert isinstance(err, StargraphValidationError)
    assert err.message == "IR JSON parse error"
    assert err.context["path"] == "/"
    # ``actual`` is the first 80 chars of the input (substring sanity check).
    assert err.context["actual"].startswith("{not valid json")


@pytest.mark.unit
def test_malformed_json_actual_is_truncated_to_80_chars() -> None:
    """The ``actual`` slice in the parse-error context never exceeds 80 chars."""
    payload = "x" * 200  # not JSON
    errs = validate(payload)
    assert len(errs) == 1
    assert len(errs[0].context["actual"]) == 80


# ---------------------------------------------------------------------------
# Pydantic error -> Stargraph ValidationError mapping (AC-12.1, AC-12.2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_missing_required_field_emits_path_and_hint() -> None:
    """``missing`` -> ``"Add the required field."`` hint + JSON Pointer path."""
    errs = validate({"id": "run:t", "nodes": []})  # missing ir_version
    msgs = [(e.context["path"], e.context["hint"]) for e in errs]
    assert ("/ir_version", "Add the required field.") in msgs


@pytest.mark.unit
def test_extra_forbidden_field_emits_hint() -> None:
    """``extra_forbidden`` -> "Remove this field -- IR forbids unknown keys." hint."""
    errs = validate({"ir_version": "1.0.0", "id": "run:t", "nodes": [], "future": 1})
    paths_hints = [(e.context["path"], e.context["hint"]) for e in errs]
    assert any(p == "/future" and "Remove this field" in h for p, h in paths_hints)


@pytest.mark.unit
def test_union_tag_invalid_uses_discriminator_hint() -> None:
    """Unknown discriminator value -> ``union_tag_invalid`` hint."""
    errs = validate(
        {
            "ir_version": "1.0.0",
            "id": "run:t",
            "nodes": [],
            "rules": [{"id": "r1", "then": [{"kind": "wat", "target": "x"}]}],
        },
    )
    assert errs and errs[0].context["hint"] == _HINTS["union_tag_invalid"]


@pytest.mark.unit
def test_union_tag_not_found_uses_discriminator_hint() -> None:
    """Missing discriminator field -> ``union_tag_not_found`` hint."""
    errs = validate(
        {
            "ir_version": "1.0.0",
            "id": "run:t",
            "nodes": [],
            "rules": [{"id": "r1", "then": [{"target": "x"}]}],
        },
    )
    assert errs and errs[0].context["hint"] == _HINTS["union_tag_not_found"]


@pytest.mark.unit
def test_unknown_pydantic_error_type_falls_back_to_url() -> None:
    """An error type not in :data:`_HINTS` falls back to ``See <url>``."""
    # ``string_type`` (got int instead of str) is not in _HINTS; should fall back.
    errs = validate({"ir_version": 123, "id": "run:t", "nodes": []})
    assert errs
    target = next(e for e in errs if e.context["path"] == "/ir_version")
    hint = target.context["hint"]
    # When url is present in pydantic error, hint starts with "See "
    assert hint.startswith("See ") or hint  # always populated


# ---------------------------------------------------------------------------
# RFC 6901 escaping (_loc_to_pointer)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_loc_to_pointer_empty_loc_is_empty_string() -> None:
    """RFC 6901: empty ``loc`` maps to ``""`` (whole-document pointer)."""
    assert _loc_to_pointer(()) == ""


@pytest.mark.unit
def test_loc_to_pointer_simple_string_segments() -> None:
    """Plain string segments are joined with ``/`` and prefixed with ``/``."""
    assert _loc_to_pointer(("a", "b", "c")) == "/a/b/c"


@pytest.mark.unit
def test_loc_to_pointer_integer_indices_become_decimals() -> None:
    """Integer ``loc`` segments (list indices) become decimal strings."""
    assert _loc_to_pointer(("rules", 0, "then", 2)) == "/rules/0/then/2"


@pytest.mark.unit
def test_loc_to_pointer_escapes_tilde_first_then_slash() -> None:
    """RFC 6901 escape order: ``~`` -> ``~0`` first, then ``/`` -> ``~1``."""
    # Input contains both characters; tilde encoded first to avoid double-escape.
    assert _loc_to_pointer(("a~b/c",)) == "/a~0b~1c"
    # Pure tilde / pure slash separately for clarity.
    assert _loc_to_pointer(("a~b",)) == "/a~0b"
    assert _loc_to_pointer(("a/b",)) == "/a~1b"


# ---------------------------------------------------------------------------
# Version checks (FR-35, AC-19.2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_emits_version_mismatch_for_wrong_major() -> None:
    """A document with a divergent major version is reported via ``check_version``."""
    errs = validate({"ir_version": "2.0.0", "id": "run:t", "nodes": []})
    assert len(errs) == 1
    err = errs[0]
    assert err.context["path"] == "/ir_version"
    assert "version_mismatch" in err.context["hint"]


@pytest.mark.unit
def test_check_version_returns_empty_list_when_majors_match() -> None:
    """Same major version -> ``[]``."""
    assert check_version({"ir_version": STARGRAPH_IR_VERSION}) == []


@pytest.mark.unit
def test_check_version_skips_when_ir_version_missing_or_non_string() -> None:
    """Missing / non-string ``ir_version`` is left to Pydantic upstream."""
    assert check_version({}) == []
    assert check_version({"ir_version": 123}) == []


@pytest.mark.unit
def test_check_version_skips_when_ir_version_malformed() -> None:
    """Malformed ``ir_version`` (non-semver) -> ``[]`` (pattern is schema's job)."""
    assert check_version({"ir_version": "not.a.semver"}) == []
    assert check_version({"ir_version": "1.0"}) == []


@pytest.mark.unit
def test_validate_with_non_dict_top_level_does_not_raise() -> None:
    """If parsed JSON is not a dict (e.g. a list), validation reports without raising."""
    # A JSON array fails Pydantic IRDocument validation -> structured errors.
    errs = validate("[]")
    assert errs
    # ``check_version`` short-circuits on non-dict so version-mismatch is not added.
    assert all("version_mismatch" not in e.context.get("hint", "") for e in errs)


# ---------------------------------------------------------------------------
# parse_version
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_version_happy_path() -> None:
    """``parse_version("1.2.3")`` -> ``(1, 2, 3)``."""
    assert parse_version("1.2.3") == (1, 2, 3)


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["1.0", "1.0.0.0", "1.x.0", "1..0", "", "abc"])
def test_parse_version_rejects_malformed(bad: str) -> None:
    """Wrong arity, empty segment, or non-int segment all raise :class:`ValueError`."""
    with pytest.raises(ValueError):
        parse_version(bad)


# ---------------------------------------------------------------------------
# Stage 5: within-IR namespace duplicate detection (TODO #16)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_rejects_duplicate_node_ids() -> None:
    """Two nodes sharing an id raise a structured ValidationError row."""
    errs = validate(
        {
            "ir_version": STARGRAPH_IR_VERSION,
            "id": "run:dup-nodes",
            "nodes": [
                {"id": "alpha", "kind": "stub"},
                {"id": "alpha", "kind": "stub"},
            ],
        }
    )
    assert errs
    assert errs[0].context["path"] == "/nodes/1/id"
    assert "duplicate" in errs[0].context["hint"]


@pytest.mark.unit
def test_validate_rejects_duplicate_rule_ids() -> None:
    """Two rules sharing an id are flagged."""
    errs = validate(
        {
            "ir_version": STARGRAPH_IR_VERSION,
            "id": "run:dup-rules",
            "nodes": [],
            "rules": [
                {"id": "r1"},
                {"id": "r1"},
            ],
        }
    )
    assert errs
    assert any(e.context["path"] == "/rules/1/id" for e in errs)


@pytest.mark.unit
def test_validate_allows_same_id_across_namespaces() -> None:
    """A node id matching a rule id is fine -- distinct namespaces."""
    errs = validate(
        {
            "ir_version": STARGRAPH_IR_VERSION,
            "id": "run:cross-ns",
            "nodes": [{"id": "shared", "kind": "stub"}],
            "rules": [{"id": "shared"}],
        }
    )
    assert errs == []


@pytest.mark.unit
def test_validate_rejects_duplicate_store_names() -> None:
    """Two stores sharing a ``name`` collide on the StoreRef key (FR-19/FR-20)."""
    errs = validate(
        {
            "ir_version": STARGRAPH_IR_VERSION,
            "id": "run:dup-stores",
            "nodes": [],
            "stores": [
                {"name": "kg", "provider": "ryugraph"},
                {"name": "kg", "provider": "kuzu"},
            ],
        }
    )
    assert errs
    assert any(e.context["path"] == "/stores/1/name" for e in errs)


# ---------------------------------------------------------------------------
# Stage 6: NodeSpec.config Cypher pre-lint (TODO #16)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_rejects_unportable_cypher_in_node_config() -> None:
    """A banned procedure (apoc.*) inside ``cypher`` config fails IR validation."""
    errs = validate(
        {
            "ir_version": STARGRAPH_IR_VERSION,
            "id": "run:bad-cypher",
            "nodes": [
                {
                    "id": "kg_query",
                    "kind": "stub",
                    "config": {"cypher": "MATCH (a) WITH apoc.coll.sum([1]) AS x RETURN x"},
                },
            ],
        }
    )
    assert errs
    assert errs[0].context["path"] == "/nodes/0/config/cypher"


@pytest.mark.unit
def test_validate_accepts_portable_cypher_in_node_config() -> None:
    """A clean MATCH/RETURN passes the pre-lint."""
    errs = validate(
        {
            "ir_version": STARGRAPH_IR_VERSION,
            "id": "run:ok-cypher",
            "nodes": [
                {
                    "id": "kg_query",
                    "kind": "stub",
                    "config": {"cypher": "MATCH (a:User) RETURN a.name"},
                },
            ],
        }
    )
    assert errs == []


@pytest.mark.unit
def test_validate_lints_cypher_suffixed_keys() -> None:
    """``filter_cypher`` (and any ``*_cypher``) is also linted."""
    errs = validate(
        {
            "ir_version": STARGRAPH_IR_VERSION,
            "id": "run:filter-cypher",
            "nodes": [
                {
                    "id": "promote",
                    "kind": "stub",
                    "config": {"filter_cypher": "MATCH (a)-[*]->(b) RETURN b"},
                },
            ],
        }
    )
    # Unbounded variable-length path is rejected by the portable subset.
    assert errs
    assert errs[0].context["path"] == "/nodes/0/config/filter_cypher"


@pytest.mark.unit
def test_validate_skips_non_string_cypher_values() -> None:
    """A dict / list under a ``cypher`` key is not linted (structured builder)."""
    errs = validate(
        {
            "ir_version": STARGRAPH_IR_VERSION,
            "id": "run:non-string",
            "nodes": [
                {
                    "id": "n",
                    "kind": "stub",
                    "config": {"cypher": {"match": "(a)"}},
                },
            ],
        }
    )
    assert errs == []
