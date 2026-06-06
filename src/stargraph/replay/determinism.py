# SPDX-License-Identifier: Apache-2.0
"""Determinism shims for replay-safe non-deterministic primitives (FR-28, design §3.8.5).

Per design §3.8.5, replay-mode workflows MUST source their non-deterministic
primitives -- wall-clock, RNG, UUIDs, OS randomness, secret tokens -- through
this module so byte-identical re-execution is achievable on counterfactual or
resume. The shims switch on a context-managed :class:`DeterminismScope`:

* Record mode (``replay=False``): call the real implementation, append the
  result to the scope's recording dict, return it.
* Replay mode (``replay=True``): pop the next recorded value from the scope's
  recording dict and return it; raise if the cassette is exhausted.

Per FR-28 amendment-6: ``set`` / ``frozenset`` field types are forbidden in
the IR ``state_schema`` because Python's set iteration order is hash-randomized
across processes (PEP 456) -- callers must use ``list[str]`` (with declared
sort) or a ``dict[str, bool]`` keyed by the would-be members. The compile-time
check lives in :func:`stargraph.graph.definition._check_state_schema_no_set_fields`.

The HTTP cassette layer (:func:`http_cassette`) wraps vcrpy with the
``(method, url, body_hash)`` matcher tuple required by amendment-6 §cassette-
layer-#1; CI ships ``record_mode="none"`` (loud-fail on missing recording),
dev ships ``record_mode="once"`` (record on first run, replay thereafter).
"""

from __future__ import annotations

import hashlib
import os as _os
import random as _random
import secrets as _secrets
import time as _time
import uuid as _uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

import vcr  # pyright: ignore[reportMissingTypeStubs]

from stargraph.errors import ReplayError

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


__all__ = [
    "HTTP_CASSETTE_MATCHERS",
    "DeterminismScope",
    "current_scope",
    "http_cassette",
    "now",
    "random",
    "secrets_token",
    "urandom",
    "uuid4",
]


# Per FR-28 amendment-6 §cassette-layer-#1: HTTP cassettes match on the tuple
# (method, url, body_hash). The body_hash matcher is registered with vcrpy at
# cassette construction time (see :func:`http_cassette`).
HTTP_CASSETTE_MATCHERS: tuple[str, ...] = ("method", "url", "body_hash")


class DeterminismScope:
    """Context manager bundling record/replay state for the determinism shims.

    Attributes:
        replay: ``True`` for replay mode, ``False`` for record mode.
        recording: Dict mapping shim name (``"now"``, ``"random"``, ``"uuid4"``,
            ``"urandom"``, ``"secrets_token"``) to a list of recorded values.
            In record mode the list is appended to; in replay mode values are
            popped from the front in call order.
    """

    __slots__ = ("_token", "recording", "replay")

    def __init__(
        self,
        *,
        replay: bool,
        recording: dict[str, list[Any]] | None = None,
    ) -> None:
        self.replay = replay
        self.recording: dict[str, list[Any]] = recording if recording is not None else {}
        self._token: Any = None

    def __enter__(self) -> DeterminismScope:
        self._token = _CURRENT_SCOPE.set(self)
        return self

    def __exit__(self, *exc: object) -> None:
        _CURRENT_SCOPE.reset(self._token)
        self._token = None


_CURRENT_SCOPE: ContextVar[DeterminismScope | None] = ContextVar(
    "stargraph_determinism_scope",
    default=None,
)


def current_scope() -> DeterminismScope | None:
    """Return the active :class:`DeterminismScope`, or ``None`` outside one."""
    return _CURRENT_SCOPE.get()


def _shim(name: str, real: Any) -> Any:
    """Record-or-replay dispatcher for a single primitive call.

    Outside any :class:`DeterminismScope` the shim is a transparent passthrough
    -- engine code that calls ``now()`` / ``random()`` etc. without an active
    scope just gets the real wall-clock / RNG value. Inside a scope, replay
    mode pops the next recorded value (raising on exhaustion) and record mode
    invokes ``real()`` and appends the result to the recording.
    """
    scope = _CURRENT_SCOPE.get()
    if scope is None:
        return real()
    bucket = scope.recording.setdefault(name, [])
    if scope.replay:
        if not bucket:
            raise ReplayError(
                f"determinism cassette exhausted for shim {name!r}; "
                "the replay produced more calls than were recorded",
                shim=name,
            )
        return bucket.pop(0)
    value = real()
    bucket.append(value)
    return value


def now() -> float:
    """Replay-safe wall-clock; mirrors :func:`time.time`."""
    return _shim("now", _time.time)


def random() -> float:
    """Replay-safe RNG draw in [0, 1); mirrors :func:`random.random`."""
    return _shim("random", _random.random)


def uuid4() -> _uuid.UUID:
    """Replay-safe UUID4; mirrors :func:`uuid.uuid4`."""
    return _shim("uuid4", _uuid.uuid4)


def urandom(n: int) -> bytes:
    """Replay-safe OS randomness; mirrors :func:`os.urandom`."""
    return _shim("urandom", lambda: _os.urandom(n))


def secrets_token(*args: Any, **kwargs: Any) -> str:
    """Replay-safe secret token; mirrors :func:`secrets.token_hex`."""
    return _shim("secrets_token", lambda: _secrets.token_hex(*args, **kwargs))


# ---------------------------------------------------------------------------
# vcrpy HTTP cassette layer
# ---------------------------------------------------------------------------


def _body_hash_matcher(r1: Any, r2: Any) -> bool:
    """Custom vcrpy matcher: SHA-256 of the request body bytes.

    Returns ``True`` when both requests have the same body hash. Empty / ``None``
    bodies hash to the same digest (``hashlib.sha256(b"")``), so GETs without a
    payload match consistently.
    """
    return _hash_body(r1.body) == _hash_body(r2.body)


def _hash_body(body: Any) -> str:
    if body is None:
        payload = b""
    elif isinstance(body, bytes):
        payload = body
    elif isinstance(body, str):
        payload = body.encode("utf-8")
    else:
        payload = repr(body).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _default_record_mode() -> str:
    """``"none"`` in CI (``CI=true``), ``"once"`` in dev otherwise.

    Mirrors the FR-28 amendment-6 cassette-layer #1 contract: CI must loud-fail
    on any HTTP request without a recorded cassette; dev records on first run.
    """
    return "none" if _os.environ.get("CI", "").lower() in ("1", "true", "yes") else "once"


@contextmanager
def http_cassette(
    cassette_path: Path | str,
    *,
    record_mode: str | None = None,
    match_on: tuple[str, ...] = HTTP_CASSETTE_MATCHERS,
) -> Generator[Any]:
    """Wrap an HTTP request scope with a vcrpy cassette (FR-28, design §3.8.5).

    Per amendment-6 §cassette-layer-#1, matches on
    ``(method, url, body_hash)``; CI defaults to ``record_mode="none"`` so
    unrecorded requests raise :class:`vcr.errors.CannotOverwriteExistingCassetteException`.
    Dev defaults to ``"once"`` (record on first run, replay thereafter).

    Args:
        cassette_path: Path to the YAML cassette file.
        record_mode: Override the env-derived default
            (``"none"`` / ``"once"`` / ``"new_episodes"`` / ``"all"``).
        match_on: Tuple of vcrpy matcher names; defaults to the FR-28
            ``(method, url, body_hash)`` tuple.

    Yields the active :class:`vcr.cassette.Cassette` (vcrpy's standard ``use_cassette``
    return value) so callers can introspect the recording in tests if needed.
    """
    mode = record_mode if record_mode is not None else _default_record_mode()
    instance = vcr.VCR(record_mode=mode)  # pyright: ignore[reportArgumentType, reportUnknownMemberType]
    # Register the body_hash matcher exactly once per cassette open. vcrpy's
    # matcher dict is keyed by name; re-registering with the same callable is
    # idempotent so this is safe across repeated http_cassette() calls.
    instance.register_matcher("body_hash", _body_hash_matcher)  # pyright: ignore[reportUnknownMemberType]
    cm = instance.use_cassette(str(cassette_path), match_on=list(match_on))  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    with cm as cassette:  # pyright: ignore[reportGeneralTypeIssues, reportUnknownVariableType]
        yield cassette
