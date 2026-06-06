# SPDX-License-Identifier: Apache-2.0
"""``check_pack_compat`` load-time gate tests (FR-39, AC-3.2).

Pins the load-time pack version-compat check added in task 2.23.
Per design §3.2 / §7.4, a pack mount with ``requires`` declares the
``stargraph_facts_version`` + plugin ``api_version`` it was authored
against; :func:`stargraph.ir._versioning.check_pack_compat` enforces the
match at pack-load (NOT runtime) and raises
:class:`stargraph.errors.PackCompatError` on mismatch -- silent runtime
drift is not allowed (FR-6 force-loud).

Acceptance bullets:

* ``requires=None`` -- accept (back-compat for legacy two-field mounts).
* matching versions -- accept.
* mismatched ``stargraph_facts_version`` -- raise.
* mismatched ``api_version`` -- raise.
* one-sided requirement (only one of the two set) -- compare only the
  set field; the unset one is "no requirement".
"""

from __future__ import annotations

import pytest

from stargraph.errors import PackCompatError
from stargraph.ir._models import PackMount, PackRequires
from stargraph.ir._versioning import check_pack_compat


@pytest.mark.unit
def test_check_pack_compat_accepts_when_requires_is_none() -> None:
    """Legacy two-field mount (``requires=None``) is always accepted."""
    pm = PackMount(id="legacy", version="1.0")
    # Should not raise.
    check_pack_compat(pm, stargraph_facts_version="1.0", api_version="1")


@pytest.mark.unit
def test_check_pack_compat_accepts_matching_versions() -> None:
    """Both fields set + both match -> accept."""
    pm = PackMount(
        id="bosun.budgets",
        version="1.0",
        requires=PackRequires(stargraph_facts_version="1.0", api_version="1"),
    )
    check_pack_compat(pm, stargraph_facts_version="1.0", api_version="1")


@pytest.mark.unit
def test_check_pack_compat_raises_on_stargraph_facts_version_mismatch() -> None:
    """``stargraph_facts_version`` mismatch raises :class:`PackCompatError`."""
    pm = PackMount(
        id="bosun.audit",
        version="1.0",
        requires=PackRequires(stargraph_facts_version="2.0", api_version="1"),
    )
    with pytest.raises(PackCompatError) as exc_info:
        check_pack_compat(pm, stargraph_facts_version="1.0", api_version="1")
    # The error context should name the mismatched field for operators.
    assert "stargraph_facts_version" in str(exc_info.value) or (
        exc_info.value.context.get("field") == "stargraph_facts_version"
    )


@pytest.mark.unit
def test_check_pack_compat_raises_on_api_version_mismatch() -> None:
    """``api_version`` mismatch raises :class:`PackCompatError`."""
    pm = PackMount(
        id="bosun.safety",
        version="1.0",
        requires=PackRequires(stargraph_facts_version="1.0", api_version="2"),
    )
    with pytest.raises(PackCompatError) as exc_info:
        check_pack_compat(pm, stargraph_facts_version="1.0", api_version="1")
    assert "api_version" in str(exc_info.value) or (
        exc_info.value.context.get("field") == "api_version"
    )


@pytest.mark.unit
def test_check_pack_compat_one_sided_requirement_only_checks_set_field() -> None:
    """Only ``stargraph_facts_version`` set -> only that field is enforced."""
    pm = PackMount(
        id="bosun.retries",
        version="1.0",
        requires=PackRequires(stargraph_facts_version="1.0"),
    )
    # api_version on the mount is None -> no requirement -> any value accepted.
    check_pack_compat(pm, stargraph_facts_version="1.0", api_version="999")

    pm2 = PackMount(
        id="bosun.x",
        version="1.0",
        requires=PackRequires(api_version="1"),
    )
    check_pack_compat(pm2, stargraph_facts_version="999.999.999", api_version="1")


@pytest.mark.unit
def test_pack_compat_error_inherits_from_stargraph_error() -> None:
    """:class:`PackCompatError` is a :class:`StargraphError` subclass (FR-24)."""
    from stargraph.errors import StargraphError

    assert issubclass(PackCompatError, StargraphError)
