# SPDX-License-Identifier: Apache-2.0
"""Provenance bundle definition and slot sanitization (AC-6.3).

Implements the 12-row coercion table that maps Python values to Fathom-safe
encodings. Every rejected case raises :class:`stargraph.errors.ValidationError`
with the offending type recorded in ``context``; there is no silent
``str()`` fallback (FR-9).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, TypedDict
from uuid import UUID

from stargraph.errors import ValidationError

__all__ = ["ProvenanceBundle", "_sanitize_provenance_slot"]


class ProvenanceBundle(TypedDict):
    """Standard provenance metadata attached to every Fathom assertion."""

    origin: str | Enum
    source: str
    run_id: str | UUID
    step: int
    confidence: Decimal | int
    timestamp: datetime


def _reject(value: Any, reason: str) -> ValidationError:
    return ValidationError(
        f"provenance slot rejected: {reason}",
        type=type(value).__name__,
        reason=reason,
    )


def _sanitize_provenance_slot(value: Any) -> str | int:
    """Encode a provenance slot value per the AC-6.3 12-row coercion table.

    Returns a Fathom-safe ``str`` or ``int``. Raises
    :class:`ValidationError` for any rejected type (``float``, ``None``,
    naive ``datetime``, relative ``Path``, or unknown types).
    """
    # bool MUST come before int because ``isinstance(True, int)`` is True.
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, float):
        raise _reject(value, "float not permitted (FR-9); use Decimal")
    if value is None:
        raise _reject(value, "None not permitted")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return value.hex
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise _reject(value, "naive datetime not permitted; require tz-aware UTC")
        utc = value.astimezone(UTC)
        return utc.strftime("%Y-%m-%dT%H:%M:%S") + (
            f".{utc.microsecond:06d}Z" if utc.microsecond else "Z"
        )
    if isinstance(value, Path):
        if not value.is_absolute():
            raise _reject(value, "relative Path not permitted; require absolute")
        return value.as_posix()
    if isinstance(value, Enum):
        return f"{type(value).__name__}.{value.name}"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    raise _reject(value, "unsupported type; no silent str() coercion")
