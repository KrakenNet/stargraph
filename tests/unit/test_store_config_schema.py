# SPDX-License-Identifier: Apache-2.0
"""JSON Schema validation of store configs at bootstrap (FR-19, NFR-9).

:func:`stargraph.plugin._config.validate_config` enforces the
:class:`stargraph.ir.StoreSpec`.config_schema contract before the engine
hands a config payload to a provider. Validation failures surface as
:class:`stargraph.errors.ValidationError` with ``path`` / ``schema_path``
in the structured context (NFR-9 loud-fail).

Empty schemas (the StoreSpec default) accept any payload.
"""

from __future__ import annotations

import pytest

from stargraph.errors import ValidationError
from stargraph.plugin._config import validate_config

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


def test_validate_config_empty_schema_accepts_any_payload() -> None:
    """An empty ``config_schema`` (StoreSpec default) is permissive."""
    validate_config({"any": "thing", "nested": {"x": 1}}, schema={})


def test_validate_config_happy_path_passes() -> None:
    """A payload matching the schema validates without raising."""
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "table": {"type": "string"}},
        "required": ["path"],
    }
    validate_config({"path": "/tmp/db", "table": "vectors"}, schema=schema)


def test_validate_config_missing_required_field_raises() -> None:
    """Missing required fields surface with a JSON pointer in context."""
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    with pytest.raises(ValidationError) as exc_info:
        validate_config({}, schema=schema)
    err = exc_info.value
    assert "schema validation" in err.message
    assert "path" in err.context
    assert "schema_path" in err.context


def test_validate_config_wrong_type_raises_with_pointer() -> None:
    """Type mismatch surfaces the JSON pointer to the offending node."""
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"port": {"type": "integer"}},
    }
    with pytest.raises(ValidationError) as exc_info:
        validate_config({"port": "not-an-int"}, schema=schema)
    err = exc_info.value
    # absolute_path -> ['port'] -> JSON pointer "/port"
    assert err.context["path"] == "/port"


def test_validate_config_additional_properties_rejected_when_schema_forbids() -> None:
    """``additionalProperties: false`` rejects unknown keys loudly."""
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "additionalProperties": False,
    }
    with pytest.raises(ValidationError):
        validate_config({"path": "/tmp/db", "rogue": True}, schema=schema)
