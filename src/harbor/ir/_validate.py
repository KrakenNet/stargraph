# SPDX-License-Identifier: Apache-2.0
"""Eager structured IR validation -- :func:`validate` never raises.

This module is the single entry point for IR validation (FR-17, FR-18,
AC-12.1, AC-12.2, AC-12.5). :func:`validate` accepts a JSON string or an
already-decoded dict and returns ``list[ValidationError]``: empty on
success, populated on any failure including malformed JSON, missing
required fields, type errors, pattern mismatches, and (Phase 2)
``ir_version`` major divergence.

Each :class:`harbor.errors.ValidationError` carries structured context
(``path``, ``expected``, ``actual``, ``hint``) instead of just a string,
so downstream tools can render diagnostics consistently.

* :data:`_HINTS` maps ``pydantic_core.ErrorDetails.type`` codes to
  actionable suggestions; unknown types fall back to the Pydantic docs URL.
* :func:`_loc_to_pointer` converts a Pydantic ``loc`` tuple to an
  RFC 6901 JSON Pointer (``~`` -> ``~0``, ``/`` -> ``~1``).
* :func:`check_version` (re-exported from :mod:`._versioning`) flags
  documents whose ``ir_version`` major diverges from this build's
  :data:`HARBOR_IR_VERSION` (FR-35, AC-19.2).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from pydantic import ValidationError as PydanticValidationError

from harbor.errors import ValidationError

from ._ids import validate_node_id, validate_pack_id, validate_rule_id
from ._models import IRDocument
from ._versioning import check_version

# IR-time Cypher lint hook. Lazily imported so a YAML-only IR doesn't
# pull graphglot when no node config carries Cypher. The Linter's
# constructor is cheap; the parse runs only on encountered strings.

if TYPE_CHECKING:
    from pydantic_core import ErrorDetails

__all__ = ["check_version", "validate"]


_HINTS: dict[str, str] = {
    "missing": "Add the required field.",
    "extra_forbidden": "Remove this field -- IR forbids unknown keys.",
    "union_tag_invalid": "Set a valid discriminator value (see schema).",
    "union_tag_not_found": "Add the discriminator field.",
    "string_pattern_mismatch": "Match the documented regex (e.g., id format).",
    "greater_than_equal": "Adjust value to meet the bound.",
    "less_than_equal": "Adjust value to meet the bound.",
    "string_too_short": "Adjust length.",
    "string_too_long": "Adjust length.",
    "int_parsing": "Provide a numeric value of the documented type.",
    "decimal_parsing": "Provide a numeric value of the documented type.",
}


def _loc_to_pointer(loc: tuple[int | str, ...]) -> str:
    """Convert a Pydantic ``loc`` tuple to an RFC 6901 JSON Pointer.

    Each segment is escaped: ``~`` -> ``~0`` then ``/`` -> ``~1``.
    Integer segments (list indices) become decimal strings. An empty
    ``loc`` maps to ``""`` (whole-document pointer per RFC 6901).
    """
    if not loc:
        return ""
    parts: list[str] = []
    for segment in loc:
        text = str(segment)
        text = text.replace("~", "~0").replace("/", "~1")
        parts.append(text)
    return "/" + "/".join(parts)


def _to_harbor_error(detail: ErrorDetails) -> ValidationError:
    """Map a Pydantic :class:`ErrorDetails` to a Harbor :class:`ValidationError`."""
    err_type = detail.get("type", "")
    url = detail.get("url", "")
    hint = _HINTS.get(err_type, f"See {url}" if url else detail.get("msg", ""))
    path = _loc_to_pointer(detail.get("loc", ()))
    expected = detail.get("msg", "")
    actual = detail.get("input")
    return ValidationError(
        "IR validation failed",
        path=path,
        expected=expected,
        actual=actual,
        hint=hint,
    )


def validate(ir: dict[str, Any] | str) -> list[ValidationError]:
    """Eager structured validation -- never raises.

    Returns ``[]`` on valid IR; a populated list on any failure
    (malformed JSON, missing required, type/pattern mismatch, version
    divergence). Each error carries ``path``, ``expected``, ``actual``,
    ``hint`` in its ``context`` dict for structured rendering.
    """
    parsed: Any
    if isinstance(ir, str):
        try:
            parsed = json.loads(ir)
        except json.JSONDecodeError as exc:
            return [
                ValidationError(
                    "IR JSON parse error",
                    path="/",
                    expected="valid JSON",
                    actual=ir[:80],
                    hint=str(exc),
                )
            ]
    else:
        parsed = ir

    try:
        doc = IRDocument.model_validate(parsed)
    except PydanticValidationError as exc:
        return [_to_harbor_error(d) for d in exc.errors(include_url=True, include_input=True)]

    # Stable-ID slug enforcement (FR-33) — done outside the IRDocument
    # Pydantic class because FR-7 / AC-13.1 ban ``model_validator`` decorators
    # in ``_models.py`` to keep the JSON Schema round-trip pure.
    id_errors: list[ValidationError] = []
    for idx, node in enumerate(doc.nodes):
        try:
            validate_node_id(node.id)
        except ValueError as exc:
            id_errors.append(
                ValidationError(
                    "IR validation failed",
                    path=f"/nodes/{idx}/id",
                    expected="slug ([a-z0-9][a-z0-9_\\-.]{0,127})",
                    actual=node.id,
                    hint=str(exc),
                ),
            )
    for idx, rule in enumerate(doc.rules):
        try:
            validate_rule_id(rule.id)
        except ValueError as exc:
            id_errors.append(
                ValidationError(
                    "IR validation failed",
                    path=f"/rules/{idx}/id",
                    expected="slug ([a-z0-9][a-z0-9_\\-.]{0,127})",
                    actual=rule.id,
                    hint=str(exc),
                ),
            )
    for idx, pack in enumerate(doc.governance):
        try:
            validate_pack_id(pack.id)
        except ValueError as exc:
            id_errors.append(
                ValidationError(
                    "IR validation failed",
                    path=f"/governance/{idx}/id",
                    expected="slug ([a-z0-9][a-z0-9_\\-.]{0,127})",
                    actual=pack.id,
                    hint=str(exc),
                ),
            )
    if id_errors:
        return id_errors

    # Stage 5 (FR-17 amendment): within-IR namespace duplicate detection.
    # Plugin loader handles cross-distribution conflicts; this catches the
    # YAML-author case where a single IR document declares two nodes/rules
    # with the same id (or two pack mounts / tool refs / skill refs / store
    # refs colliding within their own list).
    dup_errors = _detect_within_ir_duplicates(doc)
    if dup_errors:
        return dup_errors

    # Stage 6 (TODO-#16): pre-lint Cypher literals embedded in NodeSpec
    # configs so a malformed query in a packed graph fails IR validation
    # rather than the first cypher store call. Conventional config keys:
    # ``cypher`` and any ``*_cypher`` (e.g. ``filter_cypher``).
    cypher_errors = _lint_cypher_in_node_configs(doc)
    if cypher_errors:
        return cypher_errors

    if isinstance(parsed, dict):
        return check_version(cast("dict[str, Any]", parsed))
    return []


def _detect_within_ir_duplicates(doc: IRDocument) -> list[ValidationError]:
    """Stage 5: flag duplicate ids within each IR namespace.

    Each list (nodes, rules, governance, tools, skills, stores) maintains
    its own id namespace; collisions across namespaces are allowed
    (a node id matching a rule id is fine — different roles). Within
    a single list, duplicates would cause non-deterministic dispatch /
    pack-load behavior, so they fail loudly here.

    The store list keys on ``name`` rather than ``id`` because
    :class:`StoreRef` exposes ``name`` not ``id``.
    """
    errors: list[ValidationError] = []
    pairs: list[tuple[str, list[Any], str]] = [
        ("nodes", list(doc.nodes), "id"),
        ("rules", list(doc.rules), "id"),
        ("governance", list(doc.governance), "id"),
        ("tools", list(doc.tools), "id"),
        ("skills", list(doc.skills), "id"),
        ("stores", list(doc.stores), "name"),
    ]
    for field_name, items, key_attr in pairs:
        seen: dict[str, int] = {}
        for idx, item in enumerate(items):
            value = getattr(item, key_attr, None)
            if not isinstance(value, str):
                continue
            prior = seen.get(value)
            if prior is not None:
                errors.append(
                    ValidationError(
                        "IR validation failed",
                        path=f"/{field_name}/{idx}/{key_attr}",
                        expected=f"unique {key_attr} within {field_name}",
                        actual=value,
                        hint=(
                            f"duplicate {key_attr} {value!r} also at "
                            f"/{field_name}/{prior}/{key_attr}"
                        ),
                    ),
                )
            else:
                seen[value] = idx
    return errors


def _lint_cypher_in_node_configs(doc: IRDocument) -> list[ValidationError]:
    """Stage 6: parse + AST-lint any Cypher literals in NodeSpec configs.

    Walks each :class:`NodeSpec.config` for keys named ``cypher`` or any
    ``*_cypher`` suffix, and runs the canonical
    :class:`harbor.stores.cypher.Linter` against the string value.
    Parse errors and unportable-subset rejections (banned procs,
    unbounded varlen paths, YIELD *, path comprehension, CALL
    subqueries) all surface as :class:`ValidationError` rows so a
    malformed packed graph fails IR validation rather than at first
    store call. Non-string values are skipped silently — callers may
    embed structured query builders that are not raw strings.
    """
    errors: list[ValidationError] = []
    linter: Any = None  # lazy

    for n_idx, node in enumerate(doc.nodes):
        if not isinstance(node.config, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            continue
        for key, value in node.config.items():
            if not _is_cypher_key(key) or not isinstance(value, str):
                continue
            if linter is None:
                from harbor.stores.cypher import Linter

                linter = Linter()
            try:
                linter.check(value)
            except Exception as exc:
                errors.append(
                    ValidationError(
                        "IR validation failed",
                        path=f"/nodes/{n_idx}/config/{key}",
                        expected="portable Cypher subset (graphglot neo4j dialect)",
                        actual=value[:80],
                        hint=str(exc),
                    ),
                )
    return errors


def _is_cypher_key(key: str) -> bool:
    """Return True if ``key`` conventionally carries a Cypher literal."""
    return key == "cypher" or key.endswith("_cypher")
