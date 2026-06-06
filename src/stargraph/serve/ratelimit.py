# SPDX-License-Identifier: Apache-2.0
"""Per-actor anyio token-bucket rate-limiter for the counterfactual route.

Design §5.5 + §17 Resolved Decision #6 mandate: an ``anyio`` token-bucket
keyed by ``actor`` (default 10 req/min, configurable via
``stargraph.toml: counterfactual.rate_limit_per_min``); over-limit ->
``HTTPException(429)`` + ``Retry-After`` header. Bucket state is held
**in-memory only** (single-process scheduler invariant NFR-14).

**Restart semantics (locked Decision #6)**: a process restart resets
every bucket to ``capacity`` tokens, producing a brief burst-allowance
window where an actor can immediately reissue a full ``capacity``-sized
burst before normal refill semantics take over again. Operators control
restart frequency, so the burst window is bounded by deployment
practice; persistent (Redis / DB-backed) storage is deferred to v1.x
should a security review (FR-67) demand stronger guarantees.

Refill semantics
----------------
Continuous refill: ``tokens_to_add = elapsed_seconds * (refill_per_minute / 60)``,
capped at ``capacity``. With the default config (``capacity=10``,
``refill_per_minute=10``) the bucket holds at most 10 tokens; an idle
actor regains one token every 6s and a fully-drained actor recovers a
single token after 6s of inactivity. Burst protection: at most
``capacity`` requests in any 60s window.

The implementation uses ``anyio.Lock`` (not ``asyncio.Lock``) for stargraph
consistency, and ``anyio.current_time()`` (monotonic; immune to wall-clock
drift) for the elapsed-time arithmetic.

Design refs: §5.5 (rate-limit), §17 Decision #6 (in-memory bucket).
FR-16, NFR-9.
"""

from __future__ import annotations

import math

import anyio

__all__ = [
    "DEFAULT_CAPACITY",
    "DEFAULT_REFILL_PER_MINUTE",
    "PerActorBucketRegistry",
    "TokenBucket",
]


#: Default bucket capacity (max burst). Design §5.5 ("10/min default").
DEFAULT_CAPACITY = 10
#: Default steady-state refill rate (tokens per 60s window).
DEFAULT_REFILL_PER_MINUTE = 10


class TokenBucket:
    """Continuous-refill token bucket, ``anyio``-friendly.

    A single instance models one actor's quota. The bucket starts full
    (``capacity`` tokens) so a fresh actor may issue an initial burst of
    up to ``capacity`` requests before refill becomes the limiting
    factor. Concurrent ``consume`` calls are serialized by an internal
    ``anyio.Lock`` so the read-refill-write triple is atomic.

    Time is sourced from :func:`anyio.current_time` -- a monotonic clock
    immune to wall-clock changes.
    """

    def __init__(
        self,
        *,
        capacity: int = DEFAULT_CAPACITY,
        refill_per_minute: int = DEFAULT_REFILL_PER_MINUTE,
    ) -> None:
        if capacity <= 0:
            msg = f"capacity must be positive, got {capacity!r}"
            raise ValueError(msg)
        if refill_per_minute <= 0:
            msg = f"refill_per_minute must be positive, got {refill_per_minute!r}"
            raise ValueError(msg)
        self._capacity = capacity
        self._refill_per_minute = refill_per_minute
        # Tokens held as float so partial refill (elapsed * rate) is
        # carried across calls without quantization loss.
        self._tokens: float = float(capacity)
        # Sentinel; reset on first ``consume``. Uses anyio's monotonic
        # clock so the field is unset (``None``) until we're inside an
        # async runtime.
        self._last_refill: float | None = None
        self._lock = anyio.Lock()

    @property
    def capacity(self) -> int:
        """Maximum bucket size (max burst)."""
        return self._capacity

    @property
    def refill_per_minute(self) -> int:
        """Steady-state refill rate (tokens per 60s window)."""
        return self._refill_per_minute

    def _refill_rate_per_second(self) -> float:
        """Tokens added per second of elapsed wall-clock."""
        return self._refill_per_minute / 60.0

    async def consume(self, n: int = 1) -> bool:
        """Try to consume ``n`` tokens.

        Returns ``True`` if the request is accepted (tokens debited),
        ``False`` if the bucket is empty (rate-limited). On rate-limit
        the bucket state is unchanged; the caller may invoke
        :meth:`seconds_until_available` to compute a ``Retry-After``
        header value.
        """
        if n <= 0:
            msg = f"n must be positive, got {n!r}"
            raise ValueError(msg)
        async with self._lock:
            self._refill_locked()
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False

    async def seconds_until_available(self, n: int = 1) -> int:
        """Seconds until ``n`` tokens are available (lower bound, ceil-rounded).

        Used for the 429 ``Retry-After`` header. Returns at least ``1``
        (per HTTP semantics ``Retry-After: 0`` is degenerate). Refills
        the bucket first so the answer reflects current arithmetic.
        """
        async with self._lock:
            self._refill_locked()
            deficit = n - self._tokens
            if deficit <= 0:
                return 1
            seconds = deficit / self._refill_rate_per_second()
            return max(1, math.ceil(seconds))

    def _refill_locked(self) -> None:
        """Add elapsed-time-proportional tokens, capped at ``capacity``.

        Caller must hold ``self._lock``.
        """
        now = anyio.current_time()
        if self._last_refill is None:
            # First-touch initialisation: bucket is already at full
            # capacity (constructor seeded it); just stamp the clock.
            self._last_refill = now
            return
        elapsed = now - self._last_refill
        if elapsed <= 0:
            # Monotonic clock guarantees non-decreasing time, but guard
            # the ``=`` case anyway.
            return
        added = elapsed * self._refill_rate_per_second()
        self._tokens = min(float(self._capacity), self._tokens + added)
        self._last_refill = now


class PerActorBucketRegistry:
    """Lazy per-actor :class:`TokenBucket` registry.

    First request from an actor lazily mints a bucket initialized with
    the registry's default config; subsequent requests reuse it. The
    registry is process-local in-memory only (locked Decision #6) -- a
    process restart drops every bucket and the next request re-mints a
    full one (brief burst window documented at module level).

    Concurrency: bucket creation under contention is guarded by an
    ``anyio.Lock`` so two simultaneous first-requests for the same actor
    don't race two buckets into existence (the second would shadow the
    first and lose its refill state). Per-bucket atomicity remains the
    bucket's own lock's responsibility.
    """

    def __init__(
        self,
        *,
        capacity: int = DEFAULT_CAPACITY,
        refill_per_minute: int = DEFAULT_REFILL_PER_MINUTE,
    ) -> None:
        self._capacity = capacity
        self._refill_per_minute = refill_per_minute
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = anyio.Lock()

    async def get_or_create(self, actor: str) -> TokenBucket:
        """Return the bucket for ``actor``, creating it on first request."""
        # Fast path: bucket exists; skip the lock.
        bucket = self._buckets.get(actor)
        if bucket is not None:
            return bucket
        async with self._lock:
            # Re-check under lock (another task may have minted it).
            bucket = self._buckets.get(actor)
            if bucket is None:
                bucket = TokenBucket(
                    capacity=self._capacity,
                    refill_per_minute=self._refill_per_minute,
                )
                self._buckets[actor] = bucket
            return bucket

    @property
    def capacity(self) -> int:
        """Default per-actor bucket capacity."""
        return self._capacity

    @property
    def refill_per_minute(self) -> int:
        """Default per-actor refill rate (tokens per 60s)."""
        return self._refill_per_minute

    def reset(self) -> None:
        """Drop every bucket (test-only helper).

        Production callers never invoke this -- restart is the only
        mechanism for clearing per-actor state per locked Decision #6.
        """
        self._buckets.clear()
