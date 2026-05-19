# SPDX-License-Identifier: Apache-2.0
"""Bosun ``retries`` reference pack -- Stage-6 scaffold stubs (task T11).

Scaffold-stage declarations. Real bodies land in Ralph-Loop T11 (mirror
``bosun/safety_pii/__init__.py`` shape: ``_PATTERNS`` tuple, frozen
``RetryDecision`` dataclass, sync ``decide()`` function, ``_PACK_ROOT``
constant for ``bosun/signing.sign_pack``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

__all__ = ["RetryDecision", "decide"]


# Pack-root constant consumed by ``bosun.signing.sign_pack(tree=_PACK_ROOT, ...)``.
_PACK_ROOT: Path = Path(__file__).parent

_LOG: logging.Logger = logging.getLogger("harbor.bosun.retries")

# Substring patterns mirroring ``rules.clp`` recoverable-error semantics.
# Each entry: (substring matched against ``error``, ``reason`` slot for the
# emitted :class:`RetryDecision`). First match wins.
_PATTERNS: tuple[tuple[str, str], ...] = (
    ("TransientNetworkError", "transient_network"),
    ("TimeoutError", "timeout"),
    ("ConnectionResetError", "connection_reset"),
    ("ConnectionError", "connection_error"),
    ("TooManyRequests", "rate_limited"),
)


@dataclass(frozen=True)
class RetryDecision:
    """Result of :func:`decide` -- the action a Bosun retries rule emits."""

    should_retry: bool
    delay_s: float
    reason: str


def decide(*, error: str, attempt: int, max_attempts: int) -> RetryDecision:
    """Map ``(error, attempt, max_attempts)`` to a :class:`RetryDecision`.

    Walks :data:`_PATTERNS` for a matching error substring. Mirrors the
    rules.clp ``retry-on-recoverable`` rule: exponential backoff
    ``delay = 2 ** attempt`` seconds; exhausted attempts (``attempt >
    max_attempts``) fall through to the ``no_pattern_matched`` /
    ``max_attempts_exceeded`` no-retry path so the engine terminates
    rather than spinning.
    """
    for needle, reason in _PATTERNS:
        if needle in error:
            if attempt > max_attempts:
                _LOG.info(
                    "retries.decide max_attempts_exceeded attempt=%d max=%d", attempt, max_attempts
                )
                return RetryDecision(
                    should_retry=False, delay_s=0.0, reason="max_attempts_exceeded"
                )
            delay = float(2**attempt)
            _LOG.info(
                "retries.decide match=%s attempt=%d delay=%.1f", reason, attempt, delay
            )
            return RetryDecision(should_retry=True, delay_s=delay, reason=reason)
    return RetryDecision(should_retry=False, delay_s=0.0, reason="no_pattern_matched")
