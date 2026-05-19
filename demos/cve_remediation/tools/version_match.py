# SPDX-License-Identifier: Apache-2.0
"""Version-range matcher used by the CMDB correlation gate.

Phase E (2026-05-11): when a CVE has populated ``affected_version_ranges``
+ ``exact_affected_versions`` AND the matched CMDB Software CI carries a
non-empty ``version`` field, gate the correlation: only treat the asset
as truly affected when its installed version falls within at least one
declared affected range.

Honest-empty contract:
  * Empty CI version  → returns ``"unknown"`` (no false-reject).
  * Empty range list AND empty exact list → returns ``"unknown"``.
  * Otherwise → ``"in_range"`` or ``"out_of_range"``.

The comparator reuses ``fetch_advisory._version_key`` semantics: each
segment becomes an int (non-numeric segments → 0). Sufficient for
CPE-listed semver/calver/kernel-style versions; not a full PEP-440 or
NVD CPE-applicability implementation. Real-world CMDB version strings
that don't normalize cleanly land in ``"unknown"`` rather than being
forced into a wrong tier.
"""
from __future__ import annotations

import re
from typing import Any


def _version_key(v: str) -> tuple[int, ...]:
    """Mirror of ``fetch_advisory._version_key`` — kept local so this
    module has no cross-cutting import from the advisory tool."""
    if not v:
        return (0,)
    out: list[int] = []
    for seg in v.replace("-", ".").split("."):
        digits = "".join(ch for ch in seg if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


_VERSION_LIKE_RE = re.compile(r"^[0-9]+(?:[.\-_][0-9A-Za-z]+)*$")


def _is_version_like(v: str) -> bool:
    """Heuristic: looks like a version string. Vendor strings such as
    'MS15-065' or 'KB5028166' look numeric-ish and would mis-compare;
    bail out into ``"unknown"`` for those."""
    if not v:
        return False
    if not _VERSION_LIKE_RE.match(v.strip()):
        return False
    # Reject vendor-style identifiers that happen to contain digits
    # without a real version triple (e.g. 'MS15-065'). Require at least
    # one segment to be purely digit-only OR two segments separated by
    # '.'.
    parts = [p for p in v.replace("-", ".").split(".") if p]
    if not parts:
        return False
    has_pure_digit = any(p.isdigit() for p in parts)
    return has_pure_digit


def _cmp_versions(a: str, b: str) -> int:
    """Return -1 / 0 / 1 for a vs b using tuple-of-int key."""
    ka, kb = _version_key(a), _version_key(b)
    if ka < kb:
        return -1
    if ka > kb:
        return 1
    return 0


def _version_in_range(
    ci_version: str, row: dict[str, str]
) -> bool:
    """Test one cpeMatch row. A row matches when:

      * ``exact`` is set AND equals ci_version, OR
      * the row carries lower/upper bounds and ci_version falls within
        them (inclusive/exclusive per slot name).

    A row with NO bounds + no exact = "everything affected" (rare; NVD
    rows usually have at least one bound). Treated as ``True`` so we
    don't silently let a fully-open row evaporate the gate.
    """
    exact = (row.get("exact") or "").strip()
    if exact and exact == ci_version:
        return True

    start_inc = (row.get("start_inc") or "").strip()
    start_exc = (row.get("start_exc") or "").strip()
    end_inc = (row.get("end_inc") or "").strip()
    end_exc = (row.get("end_exc") or "").strip()

    any_bound = any((start_inc, start_exc, end_inc, end_exc))
    if not any_bound and not exact:
        # Fully open row; can't reject on a no-bound.
        return True

    # Lower bound
    if start_inc and _cmp_versions(ci_version, start_inc) < 0:
        return False
    if start_exc and _cmp_versions(ci_version, start_exc) <= 0:
        return False
    # Upper bound
    if end_inc and _cmp_versions(ci_version, end_inc) > 0:
        return False
    if end_exc and _cmp_versions(ci_version, end_exc) >= 0:
        return False
    return True


def check_ci_against_affected(
    *,
    ci_version: str,
    affected_ranges: list[dict[str, Any]],
    exact_affected: list[str],
    matched_product: str = "",
) -> str:
    """Return one of ``"in_range" | "out_of_range" | "unknown"``.

    Empty inputs collapse to ``"unknown"`` — gate is no-op under
    sparse data, never false-reject.

    When ``matched_product`` is supplied, only ranges whose
    ``product`` matches (case-insensitive substring) are considered.
    This prevents a Log4j2-CVE that lists 100 vendor-firmware rows
    from rejecting a legitimate log4j-core CMDB match because one
    firmware vendor's row uses a different scheme.

    ``exact_affected`` is checked first — explicit version equality
    always wins.
    """
    civ = (ci_version or "").strip()
    if not civ:
        return "unknown"
    if not _is_version_like(civ):
        return "unknown"

    exacts = [v for v in (exact_affected or []) if v]
    rows = list(affected_ranges or [])
    if not exacts and not rows:
        return "unknown"

    if civ in exacts:
        return "in_range"

    # Filter rows to matched product if supplied.
    if matched_product:
        mp_l = matched_product.lower()
        scoped = [
            r for r in rows
            if mp_l in str(r.get("product", "") or "").lower()
            or not r.get("product")
        ]
        rows = scoped or rows  # if filter empties, fall back to all rows

    if not rows and not exacts:
        return "unknown"
    if not rows:
        return "out_of_range"  # had exacts, none matched

    any_match = False
    for row in rows:
        if _version_in_range(civ, row):
            any_match = True
            break
    return "in_range" if any_match else "out_of_range"


__all__ = [
    "check_ci_against_affected",
]
