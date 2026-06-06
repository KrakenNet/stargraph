# SPDX-License-Identifier: Apache-2.0
"""Deployment-profile model + selector (POC stub).

This module implements the minimal `Profile` Pydantic surface and the
`select_profile()` precedence resolver that downstream serve wiring
(lifespan, auth-provider factory, capability-gate factory) reads from.

Capability-gate semantics (task 2.36, FR-32, FR-69, AC-4.1, design §11.1):

* :class:`ClearedProfile` is **default-deny** for the route-level
  capability gate (:attr:`Profile.default_deny_capabilities=True`). An
  unset capability on the request's :class:`AuthContext.capability_grants`
  set returns ``HTTPException(403, "capability '<cap>' not granted
  under cleared profile")``. The 7 routes flagged "cleared default-deny"
  per design §5.1 + §11.1 are: ``runs:cancel``, ``runs:pause``,
  ``runs:respond``, ``runs:counterfactual``, ``artifacts:read``,
  ``artifacts:write``, ``tools:broker_request``. The remaining
  ``runs:start`` / ``runs:read`` are permissive in both profiles.
* :class:`OssDefaultProfile` is **default-permissive**
  (:attr:`Profile.default_deny_capabilities=False`). An unset capability
  flows through to the route handler -- the Phase-1 behavior the
  POC tests + integration harness rely on. Cleared deployments tighten
  this; OSS-default deployments rely on the auth provider's grant set
  to be the source of truth (Bypass for the POC; BearerJwt for prod).

This is the **POC slice** for spec ``stargraph-serve-and-bosun``:

* Fields are limited to the policy bits needed by Phase 2's lifespan-time
  enforcement -- TLS gate, signature-verify gate, default-deny capability
  gate, audit-sink mandatory gate. The fuller field set in design §11.1
  (``allow_anonymous``, ``allow_pack_mutation``, ``allow_side_effects``,
  ...) lands in the full Phase 2 implementation.
* ``auth_provider_factory`` is a ``Callable[[], AuthProvider]``. Each
  shipped profile sets a realistic default via ``Field(default_factory=...)``
  per design §11.1 + §17 Decision #5:

  - :class:`OssDefaultProfile` defaults to a :class:`BypassAuthProvider`
    factory for POC convenience (the OSS-default profile is permissive --
    ``allow_anonymous=True``). Phase 3 polish swaps to
    :class:`BearerJwtProvider` driven by ``stargraph.toml`` JWKS config.
  - :class:`ClearedProfile` defaults to :class:`MtlsProvider`. When
    ``stargraph.toml`` exists with a ``[serve.cleared]`` section and an
    ``auth_provider`` key, the value (``"mtls"`` | ``"api_key"`` |
    ``"bearer_jwt"``) selects the factory. Unknown values fall through
    to the documented default (``mtls``); missing toml = no override.

* ``select_profile()`` honors only the env-var rung of the precedence
  ladder (``STARGRAPH_PROFILE`` env > CLI flag > ``stargraph.toml`` > default).
  The CLI and TOML rungs are deferred until ``stargraph.cli`` and
  ``stargraph.config`` ship; the env-var rung covers the systemd-friendly
  primary deployment path (design §11.2).

Design refs: §11.1 (Profile model), §11.2 (selection precedence),
§17 Decision #5 (cleared-profile auth default). FR-29, FR-30, FR-31,
FR-32, AC-1.2.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Callable  # noqa: TC003 -- pydantic resolves at runtime
from pathlib import Path
from typing import Any, cast

from pydantic import Field

from stargraph.ir._models import IRBase
from stargraph.serve.auth import (
    ApiKeyProvider,
    AuthProvider,
    BearerJwtProvider,
    BypassAuthProvider,
    MtlsProvider,
)

__all__ = [
    "ClearedProfile",
    "OssDefaultProfile",
    "Profile",
    "select_profile",
]


_STARGRAPH_TOML_FILENAME = "stargraph.toml"


def _read_stargraph_toml() -> dict[str, object]:
    """Read ``stargraph.toml`` from CWD if present; return ``{}`` otherwise.

    POC: a single read at profile-instantiation time. Phase 3 polish
    can extend to walk-up-from-CWD discovery + reload-on-SIGHUP. Any
    parse failure (malformed TOML, permission denied) returns ``{}``
    so the profile silently falls back to its documented default --
    cleared deployments must validate ``stargraph.toml`` out-of-band as
    part of the air-gap deployment guide.
    """
    path = Path.cwd() / _STARGRAPH_TOML_FILENAME
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _cleared_auth_factory_default() -> Callable[[], AuthProvider]:
    """Resolve the cleared-profile ``auth_provider_factory`` from ``stargraph.toml``.

    Per locked design §17 Decision #5, the cleared profile defaults to
    :class:`MtlsProvider`. ``stargraph.toml`` may set
    ``[serve.cleared].auth_provider`` to one of ``"mtls"``,
    ``"api_key"``, or ``"bearer_jwt"`` to override; unknown values
    silently fall through to ``mtls`` (deployment guide tells operators
    to validate the toml before relying on it).

    The returned value is a *factory* (callable returning an
    :class:`AuthProvider` instance), not the instance itself, so the
    serve lifespan can construct providers lazily after JWKS URL
    discovery / API-key store loading lands in Phase 3.

    Note: ``ApiKeyProvider`` and ``BearerJwtProvider`` require
    constructor arguments that are not yet wired through ``stargraph.toml``
    (the key store, the JWKS URL). The override path returns the bare
    class for ``mtls`` (which has all-defaulted kwargs); ``api_key``
    and ``bearer_jwt`` overrides surface a deferred-config error at
    factory-call time when the lifespan tries to instantiate them
    without the required kwargs. POC scope per task 2.5; Phase 3
    polish wires the kwarg sources.
    """
    cfg = _read_stargraph_toml()
    serve = cfg.get("serve")
    if isinstance(serve, dict):
        cleared = cast("dict[str, Any]", serve).get("cleared")
        if isinstance(cleared, dict):
            choice = cast("dict[str, Any]", cleared).get("auth_provider")
            if choice == "api_key":
                return ApiKeyProvider  # type: ignore[return-value]
            if choice == "bearer_jwt":
                return BearerJwtProvider  # type: ignore[return-value]
    return MtlsProvider


class Profile(IRBase):
    """Deployment-profile policy bundle (minimal POC fields).

    Each instance is the bag of policy knobs the serve lifespan reads at
    startup to decide TLS termination, signature verification, capability
    default-deny, audit-sink mandatory enforcement, and which
    ``AuthProvider`` to instantiate. Profiles are passed by *value* into
    lifespan; downstream wiring never mutates them.

    Field semantics:

    * ``name`` -- short stable identifier; matches the
      ``STARGRAPH_PROFILE`` env value, the CLI ``--profile`` flag, and the
      ``profile = "..."`` key in ``stargraph.toml``.
    * ``tls_required`` -- if True, lifespan refuses to bind plain HTTP.
    * ``signature_verify_mandatory`` -- if True, pack/manifest signature
      verification is non-negotiable; missing signatures = startup fail.
    * ``default_deny_capabilities`` -- if True, the engine's capability
      gate denies any unrequested capability without explicit grant.
    * ``audit_required`` -- if True, the audit sink is mandatory at
      startup; missing sink = startup fail.
    * ``auth_provider_factory`` -- callable returning an
      :class:`AuthProvider` instance. The lifespan calls this at
      startup and stashes the result on ``app.state.auth_provider``.
      Each subclass sets a realistic default via
      ``Field(default_factory=...)``; ``None`` is allowed for engine
      tests that bypass auth wiring.
    """

    name: str
    tls_required: bool
    signature_verify_mandatory: bool
    default_deny_capabilities: bool
    audit_required: bool
    auth_provider_factory: Callable[[], AuthProvider] | None = None


class OssDefaultProfile(Profile):
    """OSS-default profile -- developer-friendly defaults.

    All gates open. ``auth_provider_factory`` defaults to a
    :class:`BypassAuthProvider` factory for POC convenience (the
    OSS-default profile is permissive: ``allow_anonymous=True`` per
    design §11.1). Phase 3 polish swaps to a :class:`BearerJwtProvider`
    factory once ``stargraph.toml`` JWKS configuration lands.
    """

    name: str = "oss-default"
    tls_required: bool = False
    signature_verify_mandatory: bool = False
    default_deny_capabilities: bool = False
    audit_required: bool = False
    auth_provider_factory: Callable[[], AuthProvider] | None = Field(
        default_factory=lambda: BypassAuthProvider,
    )


class ClearedProfile(Profile):
    """Cleared-profile -- air-gapped / classified-deployment defaults.

    All gates closed. Per locked design §17 Decision #5, the
    ``auth_provider`` default is ``mtls``; ``stargraph.toml``
    ``[serve.cleared].auth_provider`` can override to ``api_key`` or
    ``bearer_jwt`` without forking. The override is read once at
    profile instantiation via :func:`_cleared_auth_factory_default`.
    """

    name: str = "cleared"
    tls_required: bool = True
    signature_verify_mandatory: bool = True
    default_deny_capabilities: bool = True
    audit_required: bool = True
    auth_provider_factory: Callable[[], AuthProvider] | None = Field(
        default_factory=_cleared_auth_factory_default,
    )


def select_profile() -> Profile:
    """Resolve the active deployment profile (POC: env-var rung only).

    Precedence per design §11.2:

    1. ``STARGRAPH_PROFILE`` env var (primary; systemd-friendly).
    2. CLI ``--profile`` flag (deferred to Phase 2 -- needs ``stargraph.cli``).
    3. ``stargraph.toml`` ``profile = "..."`` (deferred -- needs ``stargraph.config``).
    4. Default = :class:`OssDefaultProfile`.

    Unknown values fall through to the default rather than raising; the
    Phase 2 implementation will swap this for a strict registry lookup
    (``_PROFILE_REGISTRY[name]``) once the CLI / TOML rungs land.
    """
    env = os.environ.get("STARGRAPH_PROFILE")
    if env == "cleared":
        return ClearedProfile()
    return OssDefaultProfile()
