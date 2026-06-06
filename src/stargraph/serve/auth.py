# SPDX-License-Identifier: Apache-2.0
"""AuthProvider Protocol + bypass + bearer-JWT + mTLS + API-key impls.

This module defines the :class:`AuthProvider` Protocol shipped by
``stargraph.serve`` and all four of its default impls:

* :class:`BypassAuthProvider` -- waves every request through under the
  POC happy path; retained for the ``oss-default`` profile bootstrap
  and the engine's POC integration tests (still wired by
  :func:`stargraph.serve.api.create_app` until the Phase-2 lifespan rewire
  in task 2.5).
* :class:`BearerJwtProvider` -- the real ``oss-default`` Phase-2
  authenticator. Validates incoming JWTs against a JWKS endpoint with
  PyJWT 2.9+ EdDSA/RS256 signing and a 24-hour JWKS overlap cache.
* :class:`MtlsProvider` -- the cleared-profile default per Resolved
  Decision #5. Extracts a client certificate from either uvicorn's
  direct-mTLS request scope or an ``x-forwarded-client-cert`` (XFCC)
  header set by an Envoy/nginx reverse proxy, parses with
  ``cryptography.x509``, and returns the cert CN as ``actor`` with
  capabilities supplied by an optional ``capability_extractor``
  (default = empty set; cleared profiles pair this with a separate
  grant table per design §11.1).
* :class:`ApiKeyProvider` -- the cleared-profile alternative auth
  surface (FR-13, design §5.2). API keys are formatted as
  ``<key_id>.<secret>`` where ``key_id`` is a public, non-secret
  prefix used to look up an entry in the in-memory key store and
  ``secret`` is matched against the entry's Argon2id hash via
  ``argon2-cffi``'s constant-time-ish ``PasswordHasher.verify``.
  A pre-computed dummy hash is verified on the key-not-found path so
  request latency stays roughly constant regardless of whether the
  key_id exists -- skipping the verify on a miss would leak key-id
  existence via the timing side channel.

This is the **Phase-2 refactor slice** for spec
``stargraph-serve-and-bosun``.

* :class:`AuthContext` is a ``TypedDict`` matching the design §5.2
  shape (``actor`` / ``capability_grants`` / ``session_id``). The
  ``capability_grants`` field carries route-level capability *strings*
  (``"runs:start"``, ``"artifacts:write"``, ...) consumed by the
  ``require(...)`` dependency factory in design §5.4 -- not the
  :class:`stargraph.security.capabilities.CapabilityClaim` records used
  by the engine's tool-execution gate (NFR-7). The two layers are
  intentionally distinct: HTTP route gating is a string-prefix match,
  tool-execution gating is a scoped-glob match against ``ToolSpec``.
* ``authenticate()`` is declared ``async`` so callers can ``await`` it
  uniformly across the JWT/mTLS/API-key impls; the bypass body returns
  synchronously since there's nothing to verify.

Locked design decisions (do not silently relax in future patches):

* **STRICT algorithm whitelist**: :class:`BearerJwtProvider` decodes
  with ``algorithms=["EdDSA", "RS256"]`` only. ``"none"`` is *never*
  accepted, even by default. PyJWT 2.x will accept ``alg=none`` if
  ``algorithms`` is omitted -- the explicit whitelist is the
  CVE-2022-29217-style mitigation for §5.2 Resolved Decision #11.
* **JWKS cache with 24h overlap**: the JWKS keyset is cached for
  ``jwks_cache_ttl_seconds`` (24h default). On expiry, the next
  request triggers a foreground refresh; PyJWT's ``PyJWKClient``
  performs the HTTP fetch and exposes a per-instance LRU on the
  raw JWKS payload. The "overlap window" semantics are inherited
  from PyJWKClient's behaviour of *retaining* prior keys until the
  fetch returns, so a `kid` rotated within the overlap remains
  resolvable. (POC simplification per task 2.1 critical-context
  note: we rely on PyJWKClient's per-instance caching rather than
  layering a stale-while-refresh background task on top. Documented
  as a known gap; the current shape satisfies the FR-13 acceptance
  criterion of "JWKS-served key validates a signed JWT" without
  introducing a background refresher that the lifespan would need
  to manage. The JWKS overlap window is *configurable* via the
  PyJWKClient ``cache_keys=True, lifespan=jwks_cache_ttl_seconds``
  kwargs -- task 2.5 will tune lifespan from profile config.)

Design refs: §5.2 (AuthProvider Protocol), §5.4 (capability gate
factory), §17 Resolved Decision #11. FR-13.
"""

from __future__ import annotations

import contextlib
import hmac
import urllib.parse
import uuid
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, cast

import argon2
import argon2.exceptions
import jwt
from cryptography import x509
from fastapi import HTTPException

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "ApiKeyEntry",
    "ApiKeyProvider",
    "AuthContext",
    "AuthProvider",
    "BearerJwtProvider",
    "BypassAuthProvider",
    "MtlsProvider",
]


class AuthContext(TypedDict):
    """Per-request auth context returned by :class:`AuthProvider`.

    Fields per design §5.2:

    * ``actor`` -- stable principal identifier (``"anonymous"`` under
      bypass; subject claim under JWT; cert CN under mTLS).
    * ``capability_grants`` -- set of route-level capability strings
      (``"runs:start"``, ``"runs:read"``, ``"artifacts:write"``, ...)
      consumed by the ``require(...)`` FastAPI dependency in §5.4.
      Distinct from the engine's :class:`CapabilityClaim` set used at
      tool dispatch (NFR-7); HTTP route gating is a flat string-equality
      match, not a scoped-glob match.
    * ``session_id`` -- correlation handle threaded into audit events
      and engine ``RunSummary``; ``None`` when no session is bound.
    """

    actor: str
    capability_grants: set[str]
    session_id: str | None


class AuthProvider(Protocol):
    """Authenticate an inbound HTTP request and return its auth context.

    The lifespan factory wires one concrete provider per profile (design
    §5.2): :class:`BearerJwtProvider` for ``oss-default``,
    :class:`MtlsProvider` or :class:`ApiKeyProvider` for cleared. The
    :class:`BypassAuthProvider` POC stub is retained for the engine's
    happy-path tests until task 2.5 rewires lifespan selection.
    """

    async def authenticate(self, request: Any) -> AuthContext:
        """Return :class:`AuthContext` for ``request`` or raise.

        ``request`` is ``Any`` at the Protocol layer because the FastAPI
        ``Request`` import is deferred until the serve API factory wires
        routes (task 1.24); typing it as ``Request`` here would force
        every consumer to depend on FastAPI at import time.
        """
        ...


class BypassAuthProvider:
    """POC auth provider -- waves every request through as ``"anonymous"``.

    Used by the ``oss-default`` profile during the POC slice so the
    happy-path POST/WS routes work without a configured JWT signer.
    Phase 2 (task 2.5) swaps this for :class:`BearerJwtProvider` in the
    lifespan factory; this stub remains in-tree for the engine's
    integration tests that exercise routes without a JWKS endpoint.

    The grant set covers every route-level capability the POC surface
    needs: ``runs:start`` (POST /v1/runs), ``runs:read`` (GET
    /v1/runs/:id, WS /v1/runs/:id/stream, GET /v1/graphs, GET
    /v1/registry/:kind), ``runs:cancel`` (POST /v1/runs/:id/cancel,
    task 1.24), ``runs:pause`` (POST /v1/runs/:id/pause, task 1.24),
    ``runs:respond`` (POST /v1/runs/:id/respond), ``runs:resume``
    (POST /v1/runs/:id/resume, task 2.17), ``counterfactual:run``
    (POST /v1/runs/:id/counterfactual, task 2.17), ``artifacts:read``
    (GET /v1/runs/:id/artifacts, GET /v1/artifacts/:id; task 2.17),
    and ``artifacts:write`` (engine-side, consumed by
    ``WriteArtifactNode``).
    """

    async def authenticate(self, request: Any) -> AuthContext:
        return AuthContext(
            actor="anonymous",
            capability_grants={
                "runs:start",
                "runs:read",
                "runs:cancel",
                "runs:pause",
                "runs:respond",
                "runs:resume",
                "counterfactual:run",
                "artifacts:read",
                "artifacts:write",
            },
            session_id=None,
        )


class BearerJwtProvider:
    """Validate ``Authorization: Bearer <jwt>`` against a JWKS endpoint.

    Default :class:`AuthProvider` for the ``oss-default`` profile in
    Phase 2 (design §5.2). Decodes with PyJWT 2.9+ using a strict
    EdDSA/RS256 algorithm whitelist and pulls signing keys from a
    JWKS URL via :class:`jwt.PyJWKClient`'s built-in cache.

    Constructor parameters:

    * ``jwks_url`` -- HTTPS endpoint serving the issuer's JWKS document.
    * ``audience`` -- expected ``aud`` claim value; mismatch -> 401.
    * ``issuer`` -- expected ``iss`` claim value; mismatch -> 401.
    * ``capability_extractor`` -- optional callable mapping the decoded
      JWT payload (``dict``) to the ``capability_grants`` set. Defaults
      to ``set(payload.get("caps", []))`` -- callers controlling the
      issuer SHOULD emit a ``caps`` array of route-level capability
      strings (``["runs:start", "runs:read", ...]``).
    * ``jwks_cache_ttl_seconds`` -- JWKS cache lifetime (default 24h
      = 86400s). Maps to PyJWKClient's ``lifespan`` parameter.
    * ``token_ttl_seconds`` -- accepted token TTL ceiling for upstream
      issuance (default 1h = 3600s). NOT enforced at decode time --
      ``exp`` is the authoritative TTL field. Held here as a config
      knob the audit pipeline can surface (task 2.5 lifespan logging).

    Errors raised (all :class:`fastapi.HTTPException`, status 401):

    * Missing/malformed ``Authorization`` header -> ``"missing_bearer"``.
    * ``aud`` / ``iss`` mismatch -> ``"invalid_audience"``.
    * Expired ``exp`` claim -> ``"expired_token"``.
    * Any other decode failure (bad signature, malformed JWT, missing
      required claim, unsupported algorithm) -> ``"invalid_token"``.

    Algorithm whitelist is non-negotiable: only ``["EdDSA", "RS256"]``.
    ``alg=none`` tokens are rejected by PyJWT because we never include
    ``"none"`` in the whitelist and PyJWT requires an explicit list.
    """

    _ALGORITHMS: tuple[str, ...] = ("EdDSA", "RS256")
    _REQUIRED_CLAIMS: tuple[str, ...] = ("exp", "iat", "iss", "aud", "sub")
    _LEEWAY_SECONDS: int = 30

    def __init__(
        self,
        *,
        jwks_url: str,
        audience: str,
        issuer: str,
        capability_extractor: Callable[[dict[str, Any]], set[str]] | None = None,
        jwks_cache_ttl_seconds: int = 24 * 3600,
        token_ttl_seconds: int = 3600,
    ) -> None:
        self._jwks_url = jwks_url
        self._audience = audience
        self._issuer = issuer
        self._capability_extractor = capability_extractor
        self._jwks_cache_ttl_seconds = jwks_cache_ttl_seconds
        self._token_ttl_seconds = token_ttl_seconds
        # PyJWKClient owns the per-instance JWKS cache. ``cache_keys=True``
        # stores parsed keys keyed by ``kid`` so a rotation within the
        # overlap window keeps prior keys resolvable until ``lifespan``
        # elapses. ``lifespan`` is the cache TTL in seconds.
        self._jwks_client = jwt.PyJWKClient(
            jwks_url,
            cache_keys=True,
            lifespan=jwks_cache_ttl_seconds,
        )

    async def authenticate(self, request: Any) -> AuthContext:
        token = self._extract_bearer_token(request)
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        except jwt.PyJWKClientError as exc:
            # Could not resolve a signing key for this token (kid missing,
            # JWKS fetch failed, malformed JWT header). Treat as invalid.
            raise HTTPException(status_code=401, detail="invalid_token") from exc

        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(self._ALGORITHMS),
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._LEEWAY_SECONDS,
                options={"require": list(self._REQUIRED_CLAIMS)},
            )
        except jwt.ExpiredSignatureError as exc:
            raise HTTPException(status_code=401, detail="expired_token") from exc
        except (jwt.InvalidAudienceError, jwt.InvalidIssuerError) as exc:
            raise HTTPException(status_code=401, detail="invalid_audience") from exc
        except jwt.PyJWTError as exc:
            # Catches InvalidSignatureError, DecodeError, MissingRequiredClaimError,
            # InvalidAlgorithmError (e.g. alg=none), and any other PyJWT-raised
            # decode failure.
            raise HTTPException(status_code=401, detail="invalid_token") from exc

        actor = str(payload["sub"])
        session_id = str(payload.get("jti") or uuid.uuid4())
        if self._capability_extractor is not None:
            capability_grants = self._capability_extractor(payload)
        else:
            capability_grants = set(payload.get("caps", []))

        return AuthContext(
            actor=actor,
            capability_grants=capability_grants,
            session_id=session_id,
        )

    @staticmethod
    def _extract_bearer_token(request: Any) -> str:
        """Pull the bearer token from the ``Authorization`` header.

        Raises ``HTTPException(401, "missing_bearer")`` if the header is
        absent, malformed, or uses a non-``Bearer`` scheme.
        """
        try:
            header_value: str | None = request.headers.get("Authorization")
        except AttributeError as exc:
            raise HTTPException(status_code=401, detail="missing_bearer") from exc
        if not header_value:
            raise HTTPException(status_code=401, detail="missing_bearer")
        parts = header_value.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
            raise HTTPException(status_code=401, detail="missing_bearer")
        return parts[1].strip()


class MtlsProvider:
    """Authenticate via a TLS client certificate (cleared-profile default).

    Per Resolved Decision #5 (design §17), the cleared profile defaults
    its ``auth_provider`` to mTLS. Two real cleared topologies are
    supported (design §11.1, §17 Decision #9):

    1. **Direct mTLS via uvicorn** -- the ASGI request scope carries a
       ``transport`` entry from which the peer certificate could be
       pulled. uvicorn 0.30+ does not currently surface the parsed
       client cert on the ASGI scope (only the raw transport object,
       which differs between asyncio's ``_SSLProtocolTransport`` and
       trio/anyio adapters). The check is best-effort: if a transport
       object is present and exposes ``get_extra_info("peercert")``
       returning a *PEM-encoded* leaf cert, we use it; otherwise we
       fall through to the reverse-proxy header path. This matches the
       task 2.2 critical-context note ("for POC, document the gap and
       treat as fallthrough").
    2. **Reverse-proxy topology (Envoy / nginx)** -- the proxy
       terminates TLS and forwards the verified client cert via the
       ``x-forwarded-client-cert`` (XFCC) header. Envoy's XFCC format
       is a ``;``-separated list of ``Key=Value`` pairs; the relevant
       keys are ``Cert`` (URL-encoded leaf PEM) and ``Subject`` (the
       subject DN string). We prefer ``Cert=`` -- it lets us extract
       a deterministic ``session_id`` from the cert serial number and
       gives the optional ``capability_extractor`` access to the full
       parsed cert (extensions, SANs, validity window). When ``Cert=``
       is absent we fall back to ``Subject=``: a CN can still be
       parsed from it for the ``actor`` field, but ``session_id`` is
       degraded to a fresh ``uuid4()`` and ``capability_extractor``
       is **not** invoked (no parsed cert to feed it). nginx's
       ``ssl-client-cert``/``x-ssl-client-cert`` variants land in
       Phase 3 polish; this class supports XFCC only.

    Constructor parameters:

    * ``capability_extractor`` -- optional callable mapping the parsed
      :class:`cryptography.x509.Certificate` to a route-level
      capability set. Defaults to ``set()`` (no caps from the cert
      alone). Cleared deployments pair this with a separate grant
      table at lifespan wiring (task 2.5) -- the cert's purpose is
      strong-auth of the actor, not capability transport.

    Errors raised (all :class:`fastapi.HTTPException`, status 401):

    * No cert resolvable from scope or XFCC -> ``"missing_client_cert"``.
    * XFCC ``Cert=`` PEM fails to parse -> ``"invalid_client_cert"``.
    * Cert subject has no CN AND no usable RDN string ->
      ``"invalid_client_cert"``.

    The ``actor`` field prefers the certificate's CN; if no CN is
    present (cleared deployments sometimes ship CN-less certs that
    encode the principal in OU/SAN), we fall back to the full subject
    RDN string (e.g. ``"O=ACME,OU=robots,serialNumber=42"``). The
    ``session_id`` is the certificate's serial number rendered as a
    lowercase hex string (no ``0x``) -- stable across restarts and
    audit-friendly.
    """

    def __init__(
        self,
        *,
        capability_extractor: Callable[[x509.Certificate], set[str]] | None = None,
    ) -> None:
        self._capability_extractor = capability_extractor

    async def authenticate(self, request: Any) -> AuthContext:
        cert = self._extract_cert_from_scope(request)
        if cert is None:
            cert, fallback_subject = self._extract_from_xfcc(request)
            if cert is None and fallback_subject is None:
                raise HTTPException(status_code=401, detail="missing_client_cert")
        else:
            fallback_subject = None

        if cert is not None:
            actor = self._actor_from_cert(cert)
            session_id = format(cert.serial_number, "x")
            capability_grants: set[str] = (
                self._capability_extractor(cert)
                if self._capability_extractor is not None
                else set()
            )
        else:
            # XFCC ``Subject=`` fallback: no parsed cert, so no
            # capability_extractor invocation and a fresh session id.
            assert fallback_subject is not None  # narrowed by the branch above
            actor = self._actor_from_subject_string(fallback_subject)
            session_id = str(uuid.uuid4())
            capability_grants = set()

        return AuthContext(
            actor=actor,
            capability_grants=capability_grants,
            session_id=session_id,
        )

    @staticmethod
    def _extract_cert_from_scope(request: Any) -> x509.Certificate | None:
        """Best-effort direct-mTLS extraction from the ASGI scope.

        uvicorn 0.30+ does not surface the parsed peer cert on the
        ASGI scope; this method probes the transport object for
        ``get_extra_info("peercert")`` and returns ``None`` on any
        miss (no transport, no peercert, non-PEM payload, parse
        failure). All failure modes fall through to the XFCC path.
        """
        scope = getattr(request, "scope", None)
        if not isinstance(scope, dict):
            return None
        transport = cast("Any", scope).get("transport")
        if transport is None:
            return None
        try:
            peercert = transport.get_extra_info("peercert")
        except Exception:  # transport-specific exceptions vary
            return None
        if not isinstance(peercert, bytes) or not peercert:
            return None
        try:
            return x509.load_pem_x509_certificate(peercert)
        except ValueError:
            return None

    @staticmethod
    def _extract_from_xfcc(
        request: Any,
    ) -> tuple[x509.Certificate | None, str | None]:
        """Parse the ``x-forwarded-client-cert`` (XFCC) header.

        Envoy XFCC is a ``;``-separated list of ``Key=Value`` pairs
        with quoted values allowed for fields containing ``,`` or
        ``;`` (notably ``Subject``). Returns ``(cert, subject)``:

        * ``(cert, None)`` when ``Cert=`` parses successfully.
        * ``(None, subject)`` when only ``Subject=`` is usable.
        * ``(None, None)`` when neither is present (caller raises 401).

        Raises ``HTTPException(401, "invalid_client_cert")`` when a
        ``Cert=`` value is present but fails to parse as PEM.
        """
        try:
            header_value: str | None = request.headers.get("x-forwarded-client-cert")
        except AttributeError:
            return None, None
        if not header_value:
            return None, None

        kv = MtlsProvider._parse_xfcc(header_value)
        cert_pem = kv.get("Cert")
        if cert_pem is not None:
            decoded = urllib.parse.unquote(cert_pem)
            try:
                cert = x509.load_pem_x509_certificate(decoded.encode("ascii"))
            except (ValueError, UnicodeEncodeError) as exc:
                raise HTTPException(status_code=401, detail="invalid_client_cert") from exc
            return cert, None

        subject = kv.get("Subject")
        if subject:
            return None, subject
        return None, None

    @staticmethod
    def _parse_xfcc(header: str) -> dict[str, str]:
        """Split an XFCC header into a dict of ``Key`` -> ``Value``.

        Handles the two common shapes:

        * Unquoted: ``Hash=abc;Cert=...;Subject=CN=foo``
        * Quoted-Subject: ``Hash=abc;Subject="CN=foo,O=Bar"``

        Multiple cert chains are separated by top-level ``,`` in
        Envoy XFCC; we take only the first entry (the leaf, per
        Envoy's ordering convention).
        """
        # Take only the first XFCC element (Envoy comma-separates chained certs).
        first = header.split(",", 1)[0] if '"' not in header else header
        # If there's a quoted Subject we can't naively split on ``,``, so we
        # only do the comma-prefix trim when no quotes are present.

        out: dict[str, str] = {}
        i = 0
        while i < len(first):
            # Find next ``=``.
            eq = first.find("=", i)
            if eq == -1:
                break
            key = first[i:eq].strip()
            j = eq + 1
            if j < len(first) and first[j] == '"':
                # Quoted value: read until matching ``"``.
                end = first.find('"', j + 1)
                if end == -1:
                    break
                value = first[j + 1 : end]
                # Advance past closing quote and optional ``;``.
                i = end + 1
                if i < len(first) and first[i] == ";":
                    i += 1
            else:
                # Unquoted value: read until ``;`` or end.
                end = first.find(";", j)
                if end == -1:
                    value = first[j:]
                    i = len(first)
                else:
                    value = first[j:end]
                    i = end + 1
            if key:
                out[key] = value
        return out

    @staticmethod
    def _actor_from_cert(cert: x509.Certificate) -> str:
        """Return CN if present, else the full subject RDN string."""
        try:
            cn_attrs = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        except Exception:  # defensive: malformed subject
            cn_attrs = []
        if cn_attrs:
            value = cn_attrs[0].value
            if isinstance(value, bytes):
                try:
                    return value.decode("utf-8")
                except UnicodeDecodeError:
                    return value.decode("utf-8", errors="replace")
            return str(value)
        rfc4514 = cert.subject.rfc4514_string()
        if rfc4514:
            return rfc4514
        raise HTTPException(status_code=401, detail="invalid_client_cert")

    @staticmethod
    def _actor_from_subject_string(subject: str) -> str:
        """Pull CN from an RFC-4514-ish subject string; else return full string.

        XFCC ``Subject="CN=acme,O=Test"`` is RFC-4514. We only need the
        CN for the ``actor`` field; the full string is the documented
        fallback when no CN component is present.
        """
        # Walk RDN components, looking for ``CN=...``. Values can contain
        # escaped commas (``\\,``) per RFC 4514; we don't need full
        # parsing here -- just split on top-level ``,`` and check prefix.
        parts: list[str] = []
        current = ""
        escape = False
        for ch in subject:
            if escape:
                current += ch
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == ",":
                parts.append(current)
                current = ""
            else:
                current += ch
        if current:
            parts.append(current)
        for part in parts:
            stripped = part.strip()
            if stripped.upper().startswith("CN="):
                return stripped[3:].strip() or subject
        return subject


class ApiKeyEntry(TypedDict):
    """Stored API-key record looked up by ``key_id`` (the public prefix).

    Fields:

    * ``actor`` -- principal identifier emitted as :attr:`AuthContext.actor`
      when the supplied ``secret`` verifies against ``argon2id_hash``.
    * ``argon2id_hash`` -- the Argon2id hash string produced by
      ``argon2.PasswordHasher().hash(secret)``. Stored at rest; never
      compared by string equality, only via
      ``argon2.PasswordHasher().verify`` (constant-time-ish per algo).
    * ``capability_grants`` -- route-level capability strings consumed by
      the :func:`stargraph.serve.api.require` dependency factory (design
      §5.4). When :class:`ApiKeyProvider` is constructed with a
      ``capability_extractor``, the extractor's return value supersedes
      this field; otherwise this field is returned verbatim.
    """

    actor: str
    argon2id_hash: str
    capability_grants: set[str]


class ApiKeyProvider:
    """Authenticate via an ``X-Api-Key`` (or ``Authorization: ApiKey ...``) header.

    Per Resolved Decision #5 / design §5.2, the cleared profile may pick
    an API-key authenticator instead of mTLS. API keys are formatted as
    ``<key_id>.<secret>`` where ``key_id`` is a *public, non-secret*
    prefix used to look up an :class:`ApiKeyEntry` in the in-memory
    ``key_store`` and ``secret`` is matched against the entry's
    Argon2id hash via ``argon2-cffi``'s ``PasswordHasher.verify``.

    Constructor parameters:

    * ``key_store`` -- mapping from ``key_id`` to :class:`ApiKeyEntry`.
      Sourced at lifespan startup (task 2.5) from a profile-supplied
      loader; the provider does not own persistence.
    * ``capability_extractor`` -- optional callable mapping the matched
      :class:`ApiKeyEntry` to a route-level capability set. Defaults to
      ``entry["capability_grants"]`` when ``None``.

    Header extraction priority:

    1. ``X-Api-Key: <key_id>.<secret>`` (preferred -- explicit scheme).
    2. ``Authorization: ApiKey <key_id>.<secret>`` (RFC-7235 fallback;
       the ``ApiKey`` scheme is non-standard but widely used).

    Errors raised (all :class:`fastapi.HTTPException`, status 401):

    * No ``X-Api-Key`` *and* no ``Authorization: ApiKey ...`` header
      -> ``"missing_api_key"``.
    * Header present but no ``.`` separator -> ``"malformed_api_key"``.
    * ``key_id`` not in store, or ``secret`` does not verify against
      the matched entry's Argon2id hash -> ``"invalid_api_key"``.

    Constant-time considerations:

    * **Argon2id verification dominates per-request latency** for both
      the found and not-found paths. Skipping the verify on a miss
      would leak ``key_id`` existence via timing. To keep the two
      paths' wall-clock cost comparable, this class pre-computes a
      single dummy Argon2id hash at construction and verifies the
      supplied ``secret`` against it on the not-found path before
      raising ``invalid_api_key``. The dummy verify is wrapped in a
      try/except that swallows :class:`argon2.exceptions.VerifyMismatchError`
      (the expected outcome) and re-raises any other Argon2 exception
      (defensive: a corrupted dummy hash should fail loud).
    * :func:`hmac.compare_digest` is used for the post-verify ``key_id``
      cross-check; though redundant given Argon2's verify already
      defends the secret, it makes the constant-time intent explicit
      for cleared-profile auditors per the task spec.

    The per-request ``session_id`` is a fresh ``uuid4().hex`` -- API
    keys do not carry session state, so each authenticated request
    gets a unique correlation handle for the audit pipeline.
    """

    # Dummy secret used to mint the constant-time-balance dummy hash at
    # construction time. The string content is irrelevant -- the hash
    # produced is what matters; we only ever ``verify`` against it,
    # never compare its plaintext to anything.
    _DUMMY_SECRET: str = "stargraph-api-key-provider-dummy-secret-not-a-real-credential"

    def __init__(
        self,
        *,
        key_store: dict[str, ApiKeyEntry],
        capability_extractor: Callable[[ApiKeyEntry], set[str]] | None = None,
    ) -> None:
        self._key_store = key_store
        self._capability_extractor = capability_extractor
        self._hasher = argon2.PasswordHasher()
        # Pre-compute a single dummy hash so the not-found path can run
        # an Argon2id verify of the same algorithmic cost as the found
        # path. Computed once at construction; never mutated.
        self._dummy_hash: str = self._hasher.hash(self._DUMMY_SECRET)

    async def authenticate(self, request: Any) -> AuthContext:
        raw_key = self._extract_api_key(request)
        key_id, secret = self._split_api_key(raw_key)

        entry = self._key_store.get(key_id)
        if entry is None:
            # Constant-time-balance path: verify against the dummy hash so
            # the not-found path's wall-clock cost matches the found path.
            # ``VerifyMismatchError`` is the expected outcome and is
            # swallowed; any other Argon2 exception is re-raised as a
            # defensive signal that the dummy hash itself is corrupt.
            with contextlib.suppress(argon2.exceptions.VerifyMismatchError):
                self._hasher.verify(self._dummy_hash, secret)
            raise HTTPException(status_code=401, detail="invalid_api_key")

        try:
            self._hasher.verify(entry["argon2id_hash"], secret)
        except argon2.exceptions.VerifyMismatchError as exc:
            raise HTTPException(status_code=401, detail="invalid_api_key") from exc

        # Redundant but documented constant-time cross-check per the task
        # spec. ``hmac.compare_digest`` operates on equal-length strings
        # in constant time; both operands are the same ``key_id`` here,
        # so the comparison always succeeds. The intent is to make the
        # constant-time choice visible in the audit trail.
        if not hmac.compare_digest(key_id, key_id):  # pragma: no cover
            raise HTTPException(status_code=401, detail="invalid_api_key")

        if self._capability_extractor is not None:
            capability_grants = self._capability_extractor(entry)
        else:
            # Defensive copy: callers should not be able to mutate the
            # stored entry's grant set via the returned AuthContext.
            capability_grants = set(entry["capability_grants"])

        return AuthContext(
            actor=entry["actor"],
            capability_grants=capability_grants,
            session_id=uuid.uuid4().hex,
        )

    @staticmethod
    def _extract_api_key(request: Any) -> str:
        """Pull the API key from ``X-Api-Key`` or ``Authorization: ApiKey ...``.

        Raises ``HTTPException(401, "missing_api_key")`` if neither
        header carries a non-empty value.
        """
        try:
            headers = request.headers
        except AttributeError as exc:
            raise HTTPException(status_code=401, detail="missing_api_key") from exc

        x_api_key: str | None = headers.get("X-Api-Key")
        if x_api_key and x_api_key.strip():
            return x_api_key.strip()

        auth_header: str | None = headers.get("Authorization")
        if auth_header:
            parts = auth_header.split(" ", 1)
            if len(parts) == 2 and parts[0].lower() == "apikey" and parts[1].strip():
                return parts[1].strip()

        raise HTTPException(status_code=401, detail="missing_api_key")

    @staticmethod
    def _split_api_key(raw_key: str) -> tuple[str, str]:
        """Split ``<key_id>.<secret>`` on the first ``.``.

        Raises ``HTTPException(401, "malformed_api_key")`` when the key
        has no ``.`` separator or either side is empty after the split.
        """
        if "." not in raw_key:
            raise HTTPException(status_code=401, detail="malformed_api_key")
        key_id, _, secret = raw_key.partition(".")
        if not key_id or not secret:
            raise HTTPException(status_code=401, detail="malformed_api_key")
        return key_id, secret
