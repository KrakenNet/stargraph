# SPDX-License-Identifier: Apache-2.0
"""Parse ``--inputs key=value`` CLI flags into IR-typed initial state."""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from pydantic import BaseModel

_ZERO_BY_TYPE: dict[str, object] = {"str": "", "int": 0, "bool": False, "bytes": b""}


def _coerce(value: str, declared: str) -> object:
    if declared == "str":
        return value
    if declared == "int":
        try:
            return int(value)
        except ValueError as e:
            raise typer.BadParameter(f"value {value!r} is not an integer") from e
    if declared == "bool":
        v = value.lower()
        if v in {"true", "1", "yes", "y"}:
            return True
        if v in {"false", "0", "no", "n"}:
            return False
        raise typer.BadParameter(f"value {value!r} is not a boolean")
    if declared == "bytes":
        return value.encode()
    raise typer.BadParameter(f"unsupported declared type {declared!r}")


def parse_inputs(pairs: list[str], state_schema: dict[str, str]) -> dict[str, object]:
    """Build the initial-state dict, zero-filling unspecified fields.

    Args:
        pairs: ``key=value`` strings from the CLI (--inputs is repeatable).
        state_schema: IR-declared mapping of ``field_name -> declared_type``.

    Returns:
        ``dict[str, object]`` with one entry per ``state_schema`` key.
        Unspecified keys are zero-filled per declared type.

    Raises:
        typer.BadParameter: on missing ``=``, unknown keys, unparsable values,
            or unsupported declared types.
    """
    parsed: dict[str, object] = {n: _ZERO_BY_TYPE[t] for n, t in state_schema.items()}
    for pair in pairs:
        if "=" not in pair:
            raise typer.BadParameter(f"input must be key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        if key not in state_schema:
            raise typer.BadParameter(
                f"unknown input {key!r}; declared fields: {sorted(state_schema)}"
            )
        parsed[key] = _coerce(value, state_schema[key])
    return parsed


def parse_inputs_for_model(pairs: list[str], model_cls: type[BaseModel]) -> dict[str, object]:
    """Parse ``--inputs`` against a resolved Pydantic ``BaseModel`` subclass.

    Used when the IR declares ``state_class`` instead of the primitive
    ``state_schema`` map. Validates that every input key is a model field
    name; coercion of values is left to Pydantic so rich annotations
    (``str | None``, ``Literal[...]``, etc.) work without the CLI having
    to mirror them.

    Args:
        pairs: ``key=value`` strings from the CLI (--inputs is repeatable).
        model_cls: The resolved state ``BaseModel`` subclass.

    Returns:
        ``{field_name: raw_string_value}`` for the keys that were supplied.
        Pydantic constructs the final model and applies its own coercion.
    """
    parsed: dict[str, object] = {}
    field_names = set(model_cls.model_fields)
    for pair in pairs:
        if "=" not in pair:
            raise typer.BadParameter(f"input must be key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        if key not in field_names:
            raise typer.BadParameter(
                f"unknown input {key!r}; declared fields: {sorted(field_names)}"
            )
        parsed[key] = value
    return parsed
