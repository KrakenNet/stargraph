# SPDX-License-Identifier: Apache-2.0
"""``PackMount.requires`` round-trip tests (FR-39, AC-3.1).

Pins the new :class:`PackRequires` IR sub-model and the optional
``PackMount.requires`` field added in task 2.22:

* default ``requires=None`` keeps the legacy two-field shape (``id`` +
  ``version``) byte-identical under canonical dumps -- no graph-hash
  drift for IR docs that don't opt into version compat.
* ``requires=PackRequires(...)`` round-trips through
  :func:`stargraph.ir.dumps` + :func:`stargraph.ir.loads` so the wire shape
  is stable across reloads.
* ``PackRequires`` inherits ``extra='forbid'`` from :class:`IRBase`;
  unknown keys reject (FR-6, AC-9.1).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from stargraph.ir import PackMount, dumps, loads
from stargraph.ir._models import PackRequires


@pytest.mark.unit
def test_pack_requires_round_trips_through_canonical_dumps() -> None:
    """``PackMount.requires`` survives ``dumps`` + ``loads`` byte-identical."""
    pm = PackMount(
        id="bosun.budgets",
        version="1.0",
        requires=PackRequires(stargraph_facts_version="1.0", api_version="1"),
    )
    text = dumps(pm)
    reloaded = loads(text, PackMount)
    assert reloaded == pm
    assert dumps(reloaded) == text


@pytest.mark.unit
def test_pack_requires_default_none_omits_field_from_canonical_dump() -> None:
    """Default ``requires=None`` is excluded by ``exclude_defaults=True``.

    Existing ``PackMount(id=..., version=...)`` callers must not see a
    new ``requires`` key in their canonical dumps -- otherwise structural
    hashes for legacy IR docs would silently drift.
    """
    pm = PackMount(id="x", version="1.0")
    text = dumps(pm)
    assert "requires" not in text


@pytest.mark.unit
def test_pack_requires_partial_fields_round_trip() -> None:
    """Either field of ``PackRequires`` may be set independently."""
    pm = PackMount(
        id="bosun.audit",
        version="1.0",
        requires=PackRequires(stargraph_facts_version="1.0"),
    )
    reloaded = loads(dumps(pm), PackMount)
    assert reloaded == pm
    assert reloaded.requires is not None
    assert reloaded.requires.stargraph_facts_version == "1.0"
    assert reloaded.requires.api_version is None


@pytest.mark.unit
def test_pack_requires_rejects_extra_keys() -> None:
    """:class:`PackRequires` inherits ``extra='forbid'`` from IRBase."""
    with pytest.raises(PydanticValidationError):
        PackRequires(stargraph_facts_version="1.0", unknown="x")  # type: ignore[call-arg]


@pytest.mark.unit
def test_pack_mount_back_compat_no_requires_kwarg() -> None:
    """``PackMount(id=..., version=...)`` still constructs without ``requires``."""
    pm = PackMount(id="legacy", version="1.0")
    assert pm.requires is None
