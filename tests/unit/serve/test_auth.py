# SPDX-License-Identifier: Apache-2.0
"""Unit tests for `stargraph.serve.auth` AuthProvider impls (Task 3.2).

Coverage matrix (per design §5.2 + locked Decision #11):

* :class:`BypassAuthProvider` — POC permissive grant set.
* :class:`BearerJwtProvider`:
  - valid EdDSA JWT decoded → :class:`AuthContext` with caps.
  - expired ``exp`` claim → ``HTTPException(401, expired_token)``.
  - wrong-alg (``HS256`` vs EdDSA) → ``HTTPException(401, invalid_token)``.
  - ``alg=none`` → ``HTTPException(401, invalid_token)``
    (algorithm-confusion attack mitigation, locked Decision #11).
* :class:`MtlsProvider`:
  - valid XFCC URL-encoded leaf cert → CN extracted as ``actor``.
  - missing ``x-forwarded-client-cert`` (and no scope cert) →
    ``HTTPException(401, missing_client_cert)``.
* :class:`ApiKeyProvider`:
  - Argon2id round-trip on a valid key → :class:`AuthContext` matches
    stored entry.
  - unknown ``key_id`` exercises the dummy-hash constant-time path
    (raises ``invalid_api_key``).

JWT keys are forged per-test from fresh Ed25519 keypairs; mTLS certs
are forged with the ``trustme`` library (added to dev deps in this
task). No private keys are committed.

Requirements: FR-13, FR-41. Design: §5.2, §16.1, §17 #11.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from typing import Any

import jwt
import pytest
import trustme
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from fastapi import HTTPException

from stargraph.serve.auth import (
    ApiKeyEntry,
    ApiKeyProvider,
    BearerJwtProvider,
    BypassAuthProvider,
    MtlsProvider,
)


class _FakeRequest:
    """Minimal request stub: only ``.headers.get(name)`` is consulted.

    The :class:`AuthProvider` Protocol takes ``Any`` so we deliberately
    do not import FastAPI's ``Request`` here. Mirrors the lookup shape
    that ``starlette.requests.Request.headers`` exposes.
    """

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = _CaseInsensitiveHeaders(headers)
        self.scope: dict[str, Any] = {}


class _CaseInsensitiveHeaders:
    """Header dict supporting case-insensitive ``.get(name)``.

    Starlette headers are case-insensitive; the serve auth providers
    rely on that. This stub mirrors only the ``get`` method.
    """

    def __init__(self, headers: dict[str, str]) -> None:
        self._headers = {k.lower(): v for k, v in headers.items()}

    def get(self, name: str, default: Any = None) -> Any:
        return self._headers.get(name.lower(), default)


def _ed25519_keypair() -> tuple[Ed25519PrivateKey, bytes, bytes]:
    """Return ``(private_key_obj, private_pem, public_pem)``."""
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    pub_pem = priv.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    return priv, priv_pem, pub_pem


def _jwks_for_pubkey(pub_pem: bytes, kid: str) -> dict[str, list[dict[str, Any]]]:
    """Build a JWKS dict for the given Ed25519 public PEM under ``kid``."""
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    pub = load_pem_public_key(pub_pem)
    raw = pub.public_bytes(  # type: ignore[attr-defined]
        Encoding.Raw, PublicFormat.Raw
    )
    import base64

    x = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return {
        "keys": [
            {
                "kty": "OKP",
                "crv": "Ed25519",
                "x": x,
                "kid": kid,
                "alg": "EdDSA",
                "use": "sig",
            }
        ]
    }


def _patch_jwks_client(monkeypatch: pytest.MonkeyPatch, jwks: dict[str, Any]) -> None:
    """Stub :func:`urllib.request.urlopen` so PyJWKClient sees our JWKS.

    PyJWKClient uses ``urllib.request.urlopen`` under the hood. We
    intercept it to return a payload with our forged JWKS rather than
    making a network call.
    """
    import io
    import urllib.request

    payload = json.dumps(jwks).encode("utf-8")

    class _FakeResponse:
        def __init__(self, body: bytes) -> None:
            self._buf = io.BytesIO(body)

        def read(self, *args: Any, **kwargs: Any) -> bytes:
            return self._buf.read(*args, **kwargs)

        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

    def _fake_urlopen(url: Any, *args: Any, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)


# ---------- BypassAuthProvider ---------------------------------------


@pytest.mark.unit
async def test_bypass_provider_grants_documented_capabilities() -> None:
    """:class:`BypassAuthProvider` returns the POC-documented grant set."""
    provider = BypassAuthProvider()
    ctx = await provider.authenticate(_FakeRequest({}))
    assert ctx["actor"] == "anonymous"
    expected = {
        "runs:start",
        "runs:read",
        "runs:cancel",
        "runs:pause",
        "runs:respond",
        "runs:resume",
        "counterfactual:run",
        "artifacts:read",
        "artifacts:write",
    }
    assert ctx["capability_grants"] == expected
    assert ctx["session_id"] is None


# ---------- BearerJwtProvider ----------------------------------------


@pytest.mark.unit
async def test_bearer_jwt_valid_token_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forged EdDSA JWT decoded against the matching JWKS yields an AuthContext."""
    _priv_obj, priv_pem, pub_pem = _ed25519_keypair()
    kid = "test-kid"
    _patch_jwks_client(monkeypatch, _jwks_for_pubkey(pub_pem, kid))

    provider = BearerJwtProvider(
        jwks_url="https://issuer.example/.well-known/jwks.json",
        audience="stargraph-test",
        issuer="https://issuer.example",
    )

    now = int(time.time())
    payload = {
        "sub": "robot-1",
        "iss": "https://issuer.example",
        "aud": "stargraph-test",
        "iat": now,
        "exp": now + 60,
        "caps": ["runs:start", "runs:read"],
    }
    token = jwt.encode(payload, priv_pem, algorithm="EdDSA", headers={"kid": kid})

    request = _FakeRequest({"Authorization": f"Bearer {token}"})
    ctx = await provider.authenticate(request)
    assert ctx["actor"] == "robot-1"
    assert ctx["capability_grants"] == {"runs:start", "runs:read"}


@pytest.mark.unit
async def test_bearer_jwt_expired_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Token with ``exp`` in the past raises 401 ``expired_token``."""
    _priv_obj, priv_pem, pub_pem = _ed25519_keypair()
    kid = "test-kid"
    _patch_jwks_client(monkeypatch, _jwks_for_pubkey(pub_pem, kid))

    provider = BearerJwtProvider(
        jwks_url="https://issuer.example/.well-known/jwks.json",
        audience="stargraph-test",
        issuer="https://issuer.example",
    )

    now = int(time.time())
    payload = {
        "sub": "robot-1",
        "iss": "https://issuer.example",
        "aud": "stargraph-test",
        "iat": now - 3600,
        # exp is past plus more than the leeway (30s) so decode rejects.
        "exp": now - 600,
    }
    token = jwt.encode(payload, priv_pem, algorithm="EdDSA", headers={"kid": kid})

    request = _FakeRequest({"Authorization": f"Bearer {token}"})
    with pytest.raises(HTTPException) as excinfo:
        await provider.authenticate(request)
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "expired_token"


@pytest.mark.unit
async def test_bearer_jwt_wrong_alg_hs256_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HS256-signed token (algorithm confusion attack) is rejected.

    The provider's whitelist is ``["EdDSA", "RS256"]``. Even when the
    JWKS is configured for the matching ``kid``, an HS256 token must
    bounce off the whitelist before any signature work happens.
    """
    _priv_obj, _priv_pem, pub_pem = _ed25519_keypair()
    kid = "test-kid"
    _patch_jwks_client(monkeypatch, _jwks_for_pubkey(pub_pem, kid))

    provider = BearerJwtProvider(
        jwks_url="https://issuer.example/.well-known/jwks.json",
        audience="stargraph-test",
        issuer="https://issuer.example",
    )

    # PyJWKClient will fail to resolve a signing key for an HS256-headed
    # token because the JWKS keys are EdDSA; the provider raises 401
    # ``invalid_token`` either way.
    now = int(time.time())
    bad = jwt.encode(
        {
            "sub": "attacker",
            "iss": "https://issuer.example",
            "aud": "stargraph-test",
            "iat": now,
            "exp": now + 60,
        },
        key="attacker-controlled-symmetric-secret",
        algorithm="HS256",
        headers={"kid": kid},
    )
    request = _FakeRequest({"Authorization": f"Bearer {bad}"})
    with pytest.raises(HTTPException) as excinfo:
        await provider.authenticate(request)
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "invalid_token"


@pytest.mark.unit
async def test_bearer_jwt_alg_none_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``alg=none`` token rejected (whitelist defense, locked Decision #11)."""
    _priv_obj, _priv_pem, pub_pem = _ed25519_keypair()
    kid = "test-kid"
    _patch_jwks_client(monkeypatch, _jwks_for_pubkey(pub_pem, kid))

    provider = BearerJwtProvider(
        jwks_url="https://issuer.example/.well-known/jwks.json",
        audience="stargraph-test",
        issuer="https://issuer.example",
    )

    now = int(time.time())
    bad = jwt.encode(
        {
            "sub": "attacker",
            "iss": "https://issuer.example",
            "aud": "stargraph-test",
            "iat": now,
            "exp": now + 60,
        },
        key="",
        algorithm="none",
        headers={"kid": kid},
    )
    request = _FakeRequest({"Authorization": f"Bearer {bad}"})
    with pytest.raises(HTTPException) as excinfo:
        await provider.authenticate(request)
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "invalid_token"


# ---------- MtlsProvider ---------------------------------------------


@pytest.mark.unit
async def test_mtls_provider_extracts_actor_from_xfcc_cert() -> None:
    """XFCC ``Cert=`` (URL-encoded PEM) → CN extracted as ``actor``.

    Uses ``trustme`` to mint a CA + leaf cert with CN
    ``robot.example.com``. The leaf PEM is URL-encoded into the XFCC
    header per the Envoy convention, and the provider's CN extraction
    must recover the original principal name.
    """
    ca = trustme.CA()
    leaf = ca.issue_cert("robot.example.com", common_name="robot.example.com")
    leaf_pem = leaf.cert_chain_pems[0].bytes().decode("ascii")
    xfcc_value = "Hash=abc;Cert=" + urllib.parse.quote(leaf_pem, safe="")

    provider = MtlsProvider()
    request = _FakeRequest({"x-forwarded-client-cert": xfcc_value})
    ctx = await provider.authenticate(request)
    assert ctx["actor"] == "robot.example.com"
    # session_id is the cert serial, lowercase hex (no 0x prefix).
    assert ctx["session_id"] is not None
    int(ctx["session_id"], 16)  # parses as hex
    assert ctx["capability_grants"] == set()


@pytest.mark.unit
async def test_mtls_provider_missing_cert_raises_401() -> None:
    """No XFCC header and no scope cert → 401 ``missing_client_cert``."""
    provider = MtlsProvider()
    request = _FakeRequest({})
    with pytest.raises(HTTPException) as excinfo:
        await provider.authenticate(request)
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "missing_client_cert"


# ---------- ApiKeyProvider -------------------------------------------


@pytest.mark.unit
async def test_api_key_provider_argon2id_roundtrip() -> None:
    """Round-trip: hash a secret, present matching key → :class:`AuthContext`."""
    import argon2

    hasher = argon2.PasswordHasher()
    key_id = "kid-001"
    secret = "this-is-a-strong-secret-string"
    entry: ApiKeyEntry = {
        "actor": "robot-001",
        "argon2id_hash": hasher.hash(secret),
        "capability_grants": {"runs:start", "runs:read"},
    }
    provider = ApiKeyProvider(key_store={key_id: entry})

    request = _FakeRequest({"X-Api-Key": f"{key_id}.{secret}"})
    ctx = await provider.authenticate(request)
    assert ctx["actor"] == "robot-001"
    assert ctx["capability_grants"] == {"runs:start", "runs:read"}
    assert ctx["session_id"] is not None


@pytest.mark.unit
async def test_api_key_provider_unknown_key_id_runs_dummy_verify() -> None:
    """Unknown ``key_id`` raises 401 after exercising the dummy-hash path.

    Skipping the verify on a miss leaks ``key_id`` existence via timing.
    The provider stores a pre-computed dummy hash and verifies the
    presented secret against it on the not-found branch. We assert the
    branch raises 401 ``invalid_api_key`` — a wrong path would either
    succeed (unthinkable) or raise something other than the documented
    HTTP error.
    """
    import argon2

    hasher = argon2.PasswordHasher()
    entry: ApiKeyEntry = {
        "actor": "robot-001",
        "argon2id_hash": hasher.hash("real-secret"),
        "capability_grants": set(),
    }
    provider = ApiKeyProvider(key_store={"known-kid": entry})

    request = _FakeRequest({"X-Api-Key": "ghost-kid.attacker-guess"})
    with pytest.raises(HTTPException) as excinfo:
        await provider.authenticate(request)
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "invalid_api_key"
