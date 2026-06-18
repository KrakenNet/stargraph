# SPDX-License-Identifier: Apache-2.0
"""``pii_guard.redact_pii`` — deterministic PII redaction tool.

Masks emails, phone numbers, and credit-card-like digit runs with stable
placeholders (``[EMAIL]`` / ``[PHONE]`` / ``[CARD]``) and reports how many
substitutions it made. Pure transformation, no I/O — ``side_effects=read``
(it reads/transforms text but mutates nothing external).

Regex order matters. Emails go first so digits inside an address are not
mistaken for a phone/card. Then the longer-and-more-specific card pattern
runs before the broader phone pattern (a 16-digit card also matches phone).
"""

from __future__ import annotations

import re
from typing import Any

from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects

__all__ = ["redact_pii"]

_NAMESPACE = "pii_guard"
_NAME = "redact_pii"
_VERSION = "1"
_REQUIRED_CAPABILITY = "tools:pii_guard:read"

# Email: local-part @ domain with at least one dot in the domain.
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
)
# Credit-card-like: 13-16 digits, optionally grouped in 4s by space/hyphen.
# Anchored on word boundaries so it does not eat into longer numeric blobs.
_CARD_RE = re.compile(
    r"\b(?:\d[ -]?){12,18}\d\b",
)
# Phone: optional +country, then 7-14 digits with common separators.
_PHONE_RE = re.compile(
    r"\+?\d(?:[\d\s().-]{5,}\d)",
)


@tool(
    name=_NAME,
    namespace=_NAMESPACE,
    version=_VERSION,
    side_effects=SideEffects.read,
    requires_capability=_REQUIRED_CAPABILITY,
    description=(
        "Redact PII (emails, phone numbers, credit-card-like digit runs) from "
        "free text using deterministic placeholders, returning the redacted "
        "text and a per-category redaction count."
    ),
)
async def redact_pii(*, text: str) -> dict[str, Any]:
    """Redact emails, phone numbers, and card-like numbers from ``text``.

    Parameters
    ----------
    text
        Free text to scrub.

    Returns
    -------
    dict[str, Any]
        ``{"redacted": "<text>", "counts": {"email": int, "phone": int,
        "card": int}, "total": int}``. ``counts`` is per category and
        ``total`` is their sum.

    Notes
    -----
    Emails are masked first (so digits in an address are not mis-read as a
    phone/card), then cards before phones (a card number also matches the
    broader phone pattern). Each replacement increments the matching counter
    via the ``re.subn`` return value.
    """
    counts = {"email": 0, "card": 0, "phone": 0}

    redacted, counts["email"] = _EMAIL_RE.subn("[EMAIL]", text)
    redacted, counts["card"] = _CARD_RE.subn("[CARD]", redacted)
    redacted, counts["phone"] = _PHONE_RE.subn("[PHONE]", redacted)

    return {
        "redacted": redacted,
        "counts": counts,
        "total": sum(counts.values()),
        "__stargraph_provenance__": {
            "origin": "tool",
            "source": _NAMESPACE,
            "external_id": f"redact_pii:{len(text)}",
        },
    }
