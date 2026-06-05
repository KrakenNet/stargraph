# SPDX-License-Identifier: Apache-2.0
"""Unit tests for `harbor.serve.profiles` (Task 3.1).

Covers:

* :func:`select_profile` env-var rung — ``HARBOR_PROFILE=cleared``
  returns :class:`ClearedProfile`; unset / unknown returns
  :class:`OssDefaultProfile` (the default fallback rung).
* TOML rung via ``harbor.toml`` ``[serve.cleared].auth_provider`` —
  the ``api_key`` and ``bearer_jwt`` overrides are honored by
  :class:`ClearedProfile`'s ``auth_provider_factory`` default.
* :class:`OssDefaultProfile` field invariants (all gates open).
* :class:`ClearedProfile` field invariants (all gates closed).
* ``auth_provider_factory`` default selection: oss-default →
  :class:`BypassAuthProvider`, cleared → :class:`MtlsProvider`.

The CLI rung and ``profile = "..."`` top-level TOML rung are deferred
per the module docstring (POC slice handles env + default only). We
exercise the implemented rungs and assert that the CLI / top-level
TOML behaviour matches the documented "fall through to default"
contract: setting an unrecognised env value falls through to
:class:`OssDefaultProfile`.

Requirements: FR-29, FR-30, AC-1.2. Design: §11.1, §11.2, §17 #5.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harbor.serve.auth import (
    ApiKeyProvider,
    BearerJwtProvider,
    BypassAuthProvider,
    MtlsProvider,
)
from harbor.serve.profiles import (
    ClearedProfile,
    OssDefaultProfile,
    select_profile,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.unit
def test_select_profile_env_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    """``HARBOR_PROFILE=cleared`` selects :class:`ClearedProfile` (env rung wins)."""
    monkeypatch.setenv("HARBOR_PROFILE", "cleared")
    profile = select_profile()
    assert isinstance(profile, ClearedProfile)
    assert profile.name == "cleared"


@pytest.mark.unit
def test_select_profile_default_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No env var → default rung returns :class:`OssDefaultProfile`."""
    monkeypatch.delenv("HARBOR_PROFILE", raising=False)
    profile = select_profile()
    assert isinstance(profile, OssDefaultProfile)
    assert profile.name == "oss-default"


@pytest.mark.unit
def test_select_profile_unknown_env_falls_through_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown env values fall through to :class:`OssDefaultProfile` (POC contract).

    The module docstring documents that unknown values fall through to
    the default rather than raising; the Phase 2 implementation will
    swap this for a strict registry lookup once CLI / TOML rungs land.
    """
    monkeypatch.setenv("HARBOR_PROFILE", "not-a-real-profile")
    profile = select_profile()
    assert isinstance(profile, OssDefaultProfile)


@pytest.mark.unit
def test_oss_default_profile_fields_all_open() -> None:
    """:class:`OssDefaultProfile` has all gates open (developer-friendly)."""
    profile = OssDefaultProfile()
    assert profile.name == "oss-default"
    assert profile.tls_required is False
    assert profile.signature_verify_mandatory is False
    assert profile.default_deny_capabilities is False
    assert profile.audit_required is False
    # auth_provider_factory defaults to BypassAuthProvider per design §17 #5.
    assert profile.auth_provider_factory is BypassAuthProvider


@pytest.mark.unit
def test_cleared_profile_fields_all_closed() -> None:
    """:class:`ClearedProfile` has all gates closed (air-gap defaults)."""
    profile = ClearedProfile()
    assert profile.name == "cleared"
    assert profile.tls_required is True
    assert profile.signature_verify_mandatory is True
    assert profile.default_deny_capabilities is True
    assert profile.audit_required is True
    # auth_provider_factory default is MtlsProvider per design §17 #5.
    assert profile.auth_provider_factory is MtlsProvider


@pytest.mark.unit
def test_oss_default_auth_factory_returns_bypass_provider() -> None:
    """Calling the OSS-default factory yields a :class:`BypassAuthProvider`."""
    profile = OssDefaultProfile()
    factory = profile.auth_provider_factory
    assert factory is not None
    instance = factory()
    assert isinstance(instance, BypassAuthProvider)


@pytest.mark.unit
def test_cleared_auth_factory_default_is_mtls() -> None:
    """No ``harbor.toml`` override → cleared profile default factory is mTLS."""
    profile = ClearedProfile()
    factory = profile.auth_provider_factory
    assert factory is MtlsProvider


@pytest.mark.unit
def test_cleared_auth_factory_toml_override_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``[serve.cleared].auth_provider = "api_key"`` selects :class:`ApiKeyProvider`."""
    toml = tmp_path / "harbor.toml"
    toml.write_text(
        '[serve.cleared]\nauth_provider = "api_key"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    profile = ClearedProfile()
    assert profile.auth_provider_factory is ApiKeyProvider


@pytest.mark.unit
def test_cleared_auth_factory_toml_override_bearer_jwt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``[serve.cleared].auth_provider = "bearer_jwt"`` selects :class:`BearerJwtProvider`."""
    toml = tmp_path / "harbor.toml"
    toml.write_text(
        '[serve.cleared]\nauth_provider = "bearer_jwt"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    profile = ClearedProfile()
    assert profile.auth_provider_factory is BearerJwtProvider


@pytest.mark.unit
def test_cleared_auth_factory_unknown_toml_value_falls_through_to_mtls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unknown ``auth_provider`` value falls through to mTLS default."""
    toml = tmp_path / "harbor.toml"
    toml.write_text(
        '[serve.cleared]\nauth_provider = "not-a-real-provider"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    profile = ClearedProfile()
    assert profile.auth_provider_factory is MtlsProvider
