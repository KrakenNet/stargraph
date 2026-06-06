# SPDX-License-Identifier: Apache-2.0
"""Integration: profile-conditional capability default-deny enforcement (task 2.36).

Per FR-32 / FR-69 / AC-4.1 / design §11.1: the cleared profile is
**default-deny** for capability gates -- an unset capability on the
:class:`AuthContext.capability_grants` set returns 403 even if the
route's ``require(...)`` factory previously waved-through. The
oss-default profile remains **default-permissive** -- unset capability
allows the request to proceed (the existing Phase-1 gate behavior).

The test installs a custom :class:`AuthProvider` that returns a
deliberately-empty ``capability_grants`` set so the cleared-vs-oss
divergence is the only behavioral difference between the two
parametrised cases. The cancel route is the canonical "default-deny
under cleared" probe (per the design §5.1 routes table and the spec's
list of 7 flagged routes: ``runs:cancel``, ``runs:pause``,
``runs:respond``, ``runs:counterfactual``, ``artifacts:read``,
``artifacts:write``, ``tools:broker_request``).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from stargraph.serve.api import create_app
from stargraph.serve.auth import AuthContext
from stargraph.serve.profiles import ClearedProfile, OssDefaultProfile

pytestmark = pytest.mark.serve


class _NoGrantAuthProvider:
    """Auth provider that returns ``actor='anon'`` with NO capability grants.

    Used to exercise the gate's profile-conditional default-deny branch
    in isolation: the cleared profile must 403 on every gated route;
    the oss-default profile must 200/202 (permissive fallthrough).
    """

    async def authenticate(self, request: Any) -> AuthContext:
        return AuthContext(
            actor="anonymous",
            capability_grants=set(),
            session_id=None,
        )


@pytest.mark.serve
@pytest.mark.integration
async def test_cleared_profile_rejects_ungranted_cancel() -> None:
    """Cleared profile + missing ``runs:cancel`` grant -> 403."""
    deps: dict[str, Any] = {"runs": {}}
    app = create_app(ClearedProfile(), deps=deps)
    # Override the auth provider that ClearedProfile.auth_provider_factory
    # wired (default = MtlsProvider, which would 401 the test's request
    # for a different reason). The no-grant provider waves through
    # authentication but provides zero capabilities.
    app.state.auth_provider = _NoGrantAuthProvider()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/runs/run-x/cancel")

    assert response.status_code == 403, response.text
    assert "cleared profile" in response.text.lower() or "not granted" in response.text.lower()


@pytest.mark.serve
@pytest.mark.integration
async def test_oss_default_profile_allows_ungranted_cancel() -> None:
    """OSS-default profile + missing ``runs:cancel`` grant -> permissive fallthrough.

    Without the grant, the cleared profile would 403; the oss-default
    profile lets the request pass the capability gate and proceeds to
    the route handler. Since the run does not exist, the handler
    returns 404 (not 403). The 404 (rather than 403) is the contract
    being asserted -- the gate did not deny.
    """
    deps: dict[str, Any] = {"runs": {}}
    app = create_app(OssDefaultProfile(), deps=deps)
    app.state.auth_provider = _NoGrantAuthProvider()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/runs/run-x/cancel")

    assert response.status_code == 404, response.text
