# SPDX-License-Identifier: Apache-2.0
"""Phase-3 integration test (task 3.17): profile parity matrix.

Drives the same scenarios under both
:class:`~harbor.serve.profiles.OssDefaultProfile` and
:class:`~harbor.serve.profiles.ClearedProfile`, asserting the
locked-design §11.1 / §11.3 divergence:

* **Cleared** (``default_deny_capabilities=True``): every gated route
  whose required capability is not in
  :attr:`AuthContext.capability_grants` returns 403 with the canonical
  ``"capability '<cap>' not granted under cleared profile"`` body.
* **OSS-default** (``default_deny_capabilities=False``): the same
  unset-capability requests pass the gate and proceed to the route
  handler -- yielding 404 (run/artifact missing), 200 (run/artifact
  present), or 409 (state precondition).

Per the spec's "≥10 parametrized cases" requirement, the matrix below
covers 7 scenarios x 2 profiles = 14 cases:

1. ``runs:cancel`` without grant.
2. ``runs:pause`` without grant.
3. ``runs:respond`` without grant.
4. ``counterfactual:run`` without grant (route capability is
   ``counterfactual:run``; the spec wording referenced
   ``runs:counterfactual`` which is the FR-32 short form).
5. ``artifacts:read`` without grant.
6. Pack-signing missing-signature surface (cleared raises
   :class:`PackSignatureError`; oss-default WARN-logs +
   ``VerifyResult(verified=False)``). Drives the
   :func:`harbor.bosun.signing.verify_pack` boundary directly because
   pack-load wiring is not yet threaded through ``create_app`` (Phase
   2 task 2.30 follow-up); the engine-side surface is the canonical
   contract.
7. ``--allow-side-effects`` startup flag (cleared raises
   :class:`ProfileViolationError`; oss-default boots normally).
   Drives :mod:`harbor.cli.serve` via :class:`typer.testing.CliRunner`;
   the cleared exit_code is non-zero and the stderr message references
   ``cleared`` / ``violation``.

Single-source-of-truth fixture: each test parametrises ``profile`` over
``[OssDefaultProfile, ClearedProfile]`` and instantiates a fresh
:func:`create_app` (or invokes the CLI) per case so no in-memory
profile state leaks. The assertion shape branches on
``profile.default_deny_capabilities`` for the gated-route scenarios and
on ``profile.signature_verify_mandatory`` for the pack-signing
scenario.

Refs: tasks.md §3.17; design §11.1 (Profile model), §11.3 (parity
matrix), §16.3 (profile-parity test harness); FR-32, FR-33, NFR-7,
AC-4.1, AC-4.2.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from typer.testing import CliRunner

from harbor.bosun.signing import (
    PackSignatureError,
    StaticTrustStore,
    sign_pack,
    verify_pack,
)
from harbor.cli.serve import cmd as serve_cmd
from harbor.serve.api import create_app
from harbor.serve.auth import AuthContext
from harbor.serve.profiles import ClearedProfile, OssDefaultProfile, Profile

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.serve, pytest.mark.api, pytest.mark.integration]


# --------------------------------------------------------------------------- #
# Single-source-of-truth ``profile`` fixture                                  #
# --------------------------------------------------------------------------- #


_PROFILE_FACTORIES = [
    pytest.param(OssDefaultProfile, id="oss-default"),
    pytest.param(ClearedProfile, id="cleared"),
]


@pytest.fixture(params=_PROFILE_FACTORIES)
def profile(request: pytest.FixtureRequest) -> Profile:
    """Yield a fresh :class:`Profile` instance per parametrised case.

    Each test gets its own newly-constructed profile so any cached
    state (auth-provider factory closures, in-memory rate-limiter
    seeds) does not leak across cases. Per the spec's gotcha note,
    this avoids the trap where a shared :func:`create_app` instance
    sticks profile-conditional middleware to all subsequent tests.
    """
    factory = request.param
    return factory()  # type: ignore[no-any-return]


class _NoGrantAuthProvider:
    """Auth provider returning ``actor='anonymous'`` with NO grants.

    Used to exercise the gate's profile-conditional default-deny branch
    in isolation. Mirrors the helper in
    :file:`tests/integration/serve/test_profile_default_deny.py`.
    """

    async def authenticate(self, request: Any) -> AuthContext:
        del request
        return AuthContext(
            actor="anonymous",
            capability_grants=set(),
            session_id=None,
        )


def _build_app(profile: Profile) -> Any:
    """Build a fresh :func:`create_app` with a no-grant auth provider.

    Empty ``deps["runs"]`` so route handlers reach for a missing run
    and emit 404 under the permissive (oss-default) path; the cleared
    path 403s at the gate before the handler runs.
    """
    deps: dict[str, Any] = {"runs": {}}
    app = create_app(profile, deps=deps)
    app.state.auth_provider = _NoGrantAuthProvider()
    return app


def _expected_status_for_gate(profile: Profile, *, permissive_status: int) -> int:
    """Return the expected response status for a gated-route case.

    Cleared (default-deny) -> 403. OSS-default (permissive) ->
    ``permissive_status`` (typically 404 for missing run / artifact).
    """
    if profile.default_deny_capabilities:
        return 403
    return permissive_status


# --------------------------------------------------------------------------- #
# Test 1: cancel without ``runs:cancel``                                       #
# --------------------------------------------------------------------------- #


async def test_cancel_without_runs_cancel_capability(profile: Profile) -> None:
    """Cleared 403 vs oss-default 404 (run not found, gate permissive)."""
    app = _build_app(profile)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/runs/run-x/cancel")
    expected = _expected_status_for_gate(profile, permissive_status=404)
    assert resp.status_code == expected, (
        f"profile={profile.name!r}: expected {expected}; got {resp.status_code}: {resp.text!r}"
    )
    if profile.default_deny_capabilities:
        body_lower = resp.text.lower()
        assert "cleared profile" in body_lower or "not granted" in body_lower, (
            f"403 message should mention cleared / not granted; got {resp.text!r}"
        )


# --------------------------------------------------------------------------- #
# Test 2: pause without ``runs:pause``                                         #
# --------------------------------------------------------------------------- #


async def test_pause_without_runs_pause_capability(profile: Profile) -> None:
    """Cleared 403 vs oss-default 404 on missing run."""
    app = _build_app(profile)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/runs/run-x/pause")
    expected = _expected_status_for_gate(profile, permissive_status=404)
    assert resp.status_code == expected, (
        f"profile={profile.name!r}: expected {expected}; got {resp.status_code}: {resp.text!r}"
    )


# --------------------------------------------------------------------------- #
# Test 3: respond without ``runs:respond``                                     #
# --------------------------------------------------------------------------- #


async def test_respond_without_runs_respond_capability(profile: Profile) -> None:
    """Cleared 403 vs oss-default 404 on missing run.

    The respond route requires a JSON body; we send the minimum valid
    shape so the route reaches the gate (cleared) or the missing-run
    handler (oss-default). The ``response`` field accepts arbitrary
    JSON per :class:`_RespondRequest`.
    """
    app = _build_app(profile)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/runs/run-x/respond",
            json={"response": {"answer": "ok"}},
        )
    expected = _expected_status_for_gate(profile, permissive_status=404)
    assert resp.status_code == expected, (
        f"profile={profile.name!r}: expected {expected}; got {resp.status_code}: {resp.text!r}"
    )


# --------------------------------------------------------------------------- #
# Test 4: counterfactual without ``counterfactual:run``                       #
# --------------------------------------------------------------------------- #


async def test_counterfactual_without_capability(profile: Profile) -> None:
    """Cleared 403 vs oss-default 503 (no checkpointer in deps).

    The counterfactual route's gate is ``counterfactual:run``. Cleared
    profile + missing grant -> 403 at the gate. OSS-default + missing
    grant -> permissive fallthrough; the handler then raises 503
    because ``deps["checkpointer"]`` is unset (the test's empty deps
    is the simplest probe of "gate did not deny"). The 503 (rather
    than the cleared 403) is what proves the gate was permissive.
    """
    app = _build_app(profile)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/runs/run-x/counterfactual",
            json={"step": 0, "mutation": {}},
        )
    if profile.default_deny_capabilities:
        assert resp.status_code == 403, (
            f"cleared expected 403; got {resp.status_code}: {resp.text!r}"
        )
    else:
        # Permissive fallthrough -> handler reaches for ``checkpointer``,
        # not wired in the test -> 503.
        assert resp.status_code == 503, (
            f"oss-default expected 503 (no checkpointer); got {resp.status_code}: {resp.text!r}"
        )


# --------------------------------------------------------------------------- #
# Test 5: artifacts:read without grant                                         #
# --------------------------------------------------------------------------- #


async def test_artifacts_read_without_capability(profile: Profile) -> None:
    """Cleared 403 vs oss-default 503 (no artifact_store in deps).

    Mirrors the ``test_artifact_get_under_*`` pair in
    :file:`tests/integration/serve/test_artifacts_endpoints.py`. The
    permissive path here yields 503 (not 404) because ``deps`` lacks
    an ``artifact_store`` -- the route returns 503 before
    :class:`ArtifactNotFound` would 404. Both 503 and 404 are
    "gate permissive" outcomes; the assertion accepts either.
    """
    app = _build_app(profile)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/v1/artifacts/{'0' * 32}")
    if profile.default_deny_capabilities:
        assert resp.status_code == 403, (
            f"cleared expected 403; got {resp.status_code}: {resp.text!r}"
        )
    else:
        # Permissive fallthrough -> handler 503's because ``artifact_store``
        # is not in deps. (404 also acceptable if a future test wires
        # the store; left as a permissive ``in {404, 503}`` band so the
        # next refactor doesn't bounce this assertion.)
        assert resp.status_code in {404, 503}, (
            f"oss-default expected gate-permissive (404|503); got {resp.status_code}: {resp.text!r}"
        )


# --------------------------------------------------------------------------- #
# Test 6: Pack-signing missing-signature surface                              #
# --------------------------------------------------------------------------- #


def _make_keypair() -> tuple[bytes, bytes, str]:
    """Generate a fresh Ed25519 keypair and a derived 16-char key_id."""
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    pub_pem = pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    pub_der = pub.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    key_id = hashlib.sha256(pub_der).hexdigest()[:16]
    return priv_pem, pub_pem, key_id


def _build_signed_pack(tmp_path: Path) -> tuple[Path, str, bytes, str]:
    """Build a signed pack tree and return ``(pack_dir, token, pub_pem, key_id)``."""
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "manifest.yaml").write_bytes(b"id: harbor.bosun.test\nversion: 1.0\n")
    (pack / "rules.clp").write_bytes(b";; minimal\n")
    priv_pem, pub_pem, key_id = _make_keypair()
    token = sign_pack(pack, priv_pem, key_id)
    return pack, token, pub_pem, key_id


def test_pack_signing_unknown_key_under_profile(profile: Profile, tmp_path: Path) -> None:
    """Cleared raises :class:`PackSignatureError`; oss-default WARN-logs.

    Drives :func:`harbor.bosun.signing.verify_pack` directly because
    the pack-load wiring inside :func:`create_app`'s lifespan is
    deferred to Phase 2 task 2.30 (FR-65). The engine-level surface
    is the canonical contract for the profile divergence per
    locked design §17 Decision #4 and is what the cleared deployment
    guide pins.

    Scenario: a pack signed by a fresh key, verified against a
    :class:`StaticTrustStore` that does NOT include that key. Under
    cleared, ``verify_pack`` raises ``PackSignatureError`` with
    ``reason="untrusted-key"``. Under oss-default, ``verify_pack``
    returns ``VerifyResult(verified=False, reason="untrusted-key")``.
    """
    pack, token, _pub_pem, _key_id = _build_signed_pack(tmp_path)
    # Empty allow-list -> the signed key is not trusted under either
    # profile. Cleared raises; oss-default warns + returns False.
    static_store = StaticTrustStore(allowed_keys={})

    if profile.signature_verify_mandatory:
        with pytest.raises(PackSignatureError) as exc_info:
            verify_pack(pack, token, static_store, profile)
        # PackSignatureError stores ``reason`` in ``.context`` per
        # HarborError's kwarg-context convention.
        reason = exc_info.value.context.get("reason")
        assert reason == "untrusted-key", (
            f"cleared profile reason should be 'untrusted-key'; got {reason!r} "
            f"(full context: {exc_info.value.context!r})"
        )
    else:
        result = verify_pack(pack, token, static_store, profile)
        assert result.verified is False, (
            f"oss-default expected VerifyResult(verified=False); got {result!r}"
        )
        assert result.reason == "untrusted-key", (
            f"oss-default reason should be 'untrusted-key'; got {result.reason!r}"
        )


# --------------------------------------------------------------------------- #
# Test 7: --allow-side-effects startup flag                                    #
# --------------------------------------------------------------------------- #


def test_allow_side_effects_startup_flag(
    profile: Profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleared rejects ``--allow-side-effects`` at startup; oss-default boots.

    Drives the typer ``harbor serve`` command via
    :class:`typer.testing.CliRunner`. The cleared profile raises
    :class:`ProfileViolationError` BEFORE any I/O, so ``CliRunner``
    sees a non-zero exit code. The oss-default profile reaches the
    uvicorn boot phase; we monkeypatch :class:`uvicorn.Server` to a
    no-op stub so the test does not actually start a listener -- the
    assertion is "the gate did not raise + uvicorn was reached".
    """
    runner = CliRunner()

    # Mount the serve command as a single-command typer app. Using
    # ``app.command()`` (no name) keeps the CLI surface flat so the
    # CliRunner argv does not need to repeat the command name. With a
    # named subcommand, single-command typer apps collapse the wrapper
    # and reject the literal subcommand name as an "unexpected extra
    # argument" -- the unnamed registration is the standard pattern
    # for invoking a single typer command via CliRunner.
    import typer  # local import: keeps top-level import surface tight

    app = typer.Typer()
    app.command()(serve_cmd)

    if profile.signature_verify_mandatory:
        # Cleared profile path: the CLI gate raises ProfileViolationError
        # before any I/O. CliRunner surfaces this as a non-zero exit
        # code; the exception is caught + recorded in result.exception.
        result = runner.invoke(
            app,
            ["--profile", "cleared", "--allow-side-effects"],
        )
        assert result.exit_code != 0, (
            f"cleared + --allow-side-effects expected non-zero exit; got "
            f"exit_code={result.exit_code}, output={result.output!r}"
        )
        # The exception chain carries the violation message.
        assert result.exception is not None, (
            "cleared path should record a ProfileViolationError on result.exception"
        )
        msg_lower = str(result.exception).lower()
        assert "cleared" in msg_lower or "violation" in msg_lower or "not permitted" in msg_lower, (
            f"exit error should mention cleared / violation / not permitted; got "
            f"{result.exception!r}"
        )
    else:
        # OSS-default path: monkeypatch uvicorn.Server to a no-op stub.
        # The cmd boots via ``uvicorn.Server(uvicorn.Config(...)).run()``
        # (T15, e69a815); we stub it on the ``harbor.cli.serve``
        # module-level reference (the name the cmd actually calls) so
        # the patch hits the in-use binding rather than only the
        # upstream module. Same idiom as test_cli_serve.py.
        import harbor.cli.serve as cli_serve_mod

        called = {"hit": False}

        class _StubServer:
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                pass

            def run(self) -> None:
                called["hit"] = True

        monkeypatch.setattr(cli_serve_mod.uvicorn, "Server", _StubServer)
        result = runner.invoke(
            app,
            ["--profile", "oss-default", "--allow-side-effects"],
        )

        assert result.exit_code == 0, (
            f"oss-default + --allow-side-effects expected zero exit; got "
            f"exit_code={result.exit_code}, output={result.output!r}, "
            f"exception={result.exception!r}"
        )
        assert called["hit"] is True, (
            "oss-default path should reach uvicorn.Server.run (gate did not block)"
        )
