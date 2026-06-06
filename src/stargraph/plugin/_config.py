# SPDX-License-Identifier: Apache-2.0
"""Bootstrap-time helpers for store config payloads (FR-19, NFR-9).

Two responsibilities, in the order Stargraph invokes them at bootstrap:

1. :func:`interpolate_env` -- substitute ``${VAR}`` placeholders in any
   string leaf with the matching environment variable. Runs *after*
   YAML parsing and *before* JSON Schema validation so the values
   reaching the validator are the resolved ones (FR-19, design §3.16).
2. :func:`validate_config` -- validate a config payload against a
   :class:`stargraph.ir.StoreSpec`'s ``config_schema`` using
   :mod:`jsonschema`. Validation errors surface as
   :class:`stargraph.errors.ValidationError` with the offending JSON
   pointer in ``context['path']``.

Missing environment variables are loud (NFR-9): unresolved placeholders
raise :class:`stargraph.errors.ValidationError`. ``${VAR}`` is the *only*
recognised syntax -- ``$VAR`` and the shell-style ``${VAR:-default}``
are intentionally not supported (the v1 contract is "explicit braces or
fail").
"""

from __future__ import annotations

import os
import re
from typing import Any

import jsonschema
import jsonschema.exceptions

from stargraph.errors import ValidationError

__all__ = ["interpolate_env", "validate_config"]

_ENV_PATTERN: re.Pattern[str] = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")
"""Match ``${VAR_NAME}`` -- uppercase + underscore + digits only."""


def interpolate_env(value: Any) -> Any:
    """Recursively interpolate ``${VAR}`` placeholders in ``value``.

    * ``str``: every ``${VAR}`` is replaced with ``os.environ[VAR]``;
      missing variables raise :class:`ValidationError`.
    * ``dict``: returns a new dict with each value interpolated (keys
      are left untouched -- environment substitution applies to values,
      never to schema field names).
    * ``list``: returns a new list with each element interpolated.
    * Any other type: returned unchanged.
    """
    if isinstance(value, str):
        return _interpolate_string(value)
    if isinstance(value, dict):
        return {k: interpolate_env(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list):
        return [interpolate_env(item) for item in value]  # pyright: ignore[reportUnknownVariableType]
    return value


def _interpolate_string(text: str) -> str:
    """Resolve every ``${VAR}`` in ``text`` against ``os.environ``.

    Raises :class:`ValidationError` (with ``var`` in the structured
    context) on the first unresolved placeholder so the caller can name
    the missing variable in its log.
    """

    def _sub(match: re.Match[str]) -> str:
        var = match.group(1)
        try:
            return os.environ[var]
        except KeyError as exc:
            raise ValidationError(
                f"undefined environment variable {var!r} in config placeholder ${{{var}}}",
                var=var,
            ) from exc

    return _ENV_PATTERN.sub(_sub, text)


def validate_config(config: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate ``config`` against ``schema`` (JSON Schema draft 2020-12).

    Empty schemas (``{}``) accept any payload -- the StoreSpec's
    ``config_schema`` defaults to an empty dict for providers that
    have no required config. Concrete validation failures surface as
    :class:`ValidationError` with ``path`` (JSON pointer to the
    offending node) and ``schema_path`` keys for log scrapers.
    """
    if not schema:
        return
    try:
        jsonschema.validate(instance=config, schema=schema)
    except jsonschema.exceptions.ValidationError as exc:
        path = "/" + "/".join(str(p) for p in exc.absolute_path)
        raise ValidationError(
            f"store config failed schema validation: {exc.message}",
            path=path,
            schema_path="/" + "/".join(str(p) for p in exc.absolute_schema_path),
        ) from exc
