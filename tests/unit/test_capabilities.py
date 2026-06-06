# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :mod:`stargraph.security.capabilities` (NFR-7, design §3.11).

Covers the default-deny capability gate:

* Tools with no required permissions always pass.
* Cleared deployments (``default_deny=True``) refuse unscoped grants
  outright -- every required permission must match a *scoped* claim
  whose glob covers the request.
* Namespace selectors: a path-scoped grant
  (``fs.read:/workspace/*``) covers requests inside that scope; an
  unscoped request against a scoped grant is rejected.
* Dev mode (``default_deny=False``) honors unscoped grants as
  wildcard-of-name covers.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from stargraph.ir._models import ToolSpec
from stargraph.security import Capabilities, CapabilityClaim
from stargraph.tools import ReplayPolicy, SideEffects


def _spec(*permissions: str) -> ToolSpec:
    """Build a minimal :class:`ToolSpec` carrying ``permissions``."""
    return ToolSpec(
        name="t",
        namespace="ns",
        version="1.0.0",
        description="",
        input_schema={},
        output_schema={},
        side_effects=SideEffects.read,
        replay_policy=ReplayPolicy.recorded_result,
        permissions=list(permissions),
        cost_estimate=Decimal("0"),
    )


@pytest.mark.unit
def test_no_required_permissions_always_passes() -> None:
    """Tools declaring no permissions skip the gate entirely."""
    caps = Capabilities(default_deny=True)
    assert caps.check(_spec()) is True


@pytest.mark.unit
def test_default_deny_cleared_with_no_grants_denies_all() -> None:
    """A cleared deployment with no granted claims denies any required permission."""
    caps = Capabilities(default_deny=True)
    assert caps.check(_spec("fs.read:/workspace/file.txt")) is False


@pytest.mark.unit
def test_cleared_scoped_grant_covers_glob_request() -> None:
    """A scoped claim ``fs.read:/workspace/*`` covers a request under that path."""
    caps = Capabilities(
        default_deny=True,
        granted={CapabilityClaim(name="fs.read", scope="/workspace/*")},
    )
    assert caps.check(_spec("fs.read:/workspace/data.csv")) is True


@pytest.mark.unit
def test_cleared_refuses_unscoped_grant() -> None:
    """In cleared mode, an unscoped (``scope=None``) grant never matches."""
    caps = Capabilities(
        default_deny=True,
        granted={CapabilityClaim(name="fs.read", scope=None)},
    )
    assert caps.check(_spec("fs.read:/workspace/data.csv")) is False


@pytest.mark.unit
def test_cleared_refuses_unscoped_request_against_scoped_grant() -> None:
    """An unscoped request (``fs.read``) is too broad to match a scoped grant."""
    caps = Capabilities(
        default_deny=True,
        granted={CapabilityClaim(name="fs.read", scope="/workspace/*")},
    )
    assert caps.check(_spec("fs.read")) is False


@pytest.mark.unit
def test_cleared_scoped_grant_does_not_cover_outside_scope() -> None:
    """The scope glob must match -- requests outside ``/workspace/*`` fail."""
    caps = Capabilities(
        default_deny=True,
        granted={CapabilityClaim(name="fs.read", scope="/workspace/*")},
    )
    assert caps.check(_spec("fs.read:/etc/passwd")) is False


@pytest.mark.unit
def test_dev_mode_unscoped_grant_covers_any_scope() -> None:
    """In dev (``default_deny=False``), an unscoped grant covers any scope of the same name."""
    caps = Capabilities(
        default_deny=False,
        granted={CapabilityClaim(name="fs.read", scope=None)},
    )
    assert caps.check(_spec("fs.read:/anywhere")) is True
    assert caps.check(_spec("fs.read")) is True


@pytest.mark.unit
def test_name_must_match_exactly_no_glob_on_name() -> None:
    """Capability names match exactly -- ``fs.read`` does not satisfy ``fs.write``."""
    caps = Capabilities(
        default_deny=True,
        granted={CapabilityClaim(name="fs.read", scope="/workspace/*")},
    )
    assert caps.check(_spec("fs.write:/workspace/file.txt")) is False


@pytest.mark.unit
def test_check_requires_all_permissions_to_be_satisfied() -> None:
    """If any required permission is unmet, ``check`` returns ``False``."""
    caps = Capabilities(
        default_deny=True,
        granted={CapabilityClaim(name="fs.read", scope="/workspace/*")},
    )
    spec = _spec("fs.read:/workspace/a.txt", "net.fetch:https://api/*")
    assert caps.check(spec) is False
