# SPDX-License-Identifier: Apache-2.0
"""Phase-3 task 3.27: Nautilus distribution-pin + upgrade-path lock-in (FR-49, FR-50, AC-6.6).

Pins the in-tree Nautilus integration to ``nautilus-rkm==0.1.2`` and
documents the upgrade procedure for future bumps. The test is the
canonical "did the pin drift?" canary -- a CI failure here means
``pyproject.toml`` was bumped without re-running the full upgrade
checklist.

Pin source of truth: **distribution metadata** via
:func:`importlib.metadata.version`, NOT the import-name attribute
``nautilus.__version__``. The in-tree ``nautilus`` package may carry
its own ``__version__`` constant that drifts independently of the
distribution name (e.g. on local dev installs from a sibling
worktree); the distribution metadata reflects the actual ``pyproject``
pin and is the bytewise contract Stargraph depends on.

Upgrade procedure when bumping ``nautilus-rkm``:

1. Edit :file:`pyproject.toml` -- change ``nautilus-rkm==0.1.2`` to the
   new pin (e.g. ``==0.1.3``).
2. Run ``uv sync`` to install the new version locally.
3. Re-run the :mod:`stargraph.nodes.nautilus.schemas` JSON-schema re-export
   script (or the auto-regen entry-point if wired) so the in-tree
   schema mirrors the new distribution shape.
4. Run the composition tests to verify the IR + node wiring still
   exercise the new surface end-to-end::

       uv run pytest tests/integration/serve/test_nautilus_composition.py

5. Update the pin assertion in this file (the assertion is the gate;
   leaving it untouched makes the test fail until the operator
   confirms the upgrade was deliberate)::

       uv run pytest tests/integration/serve/test_nautilus_upgrade_path.py

6. If shape drift is detected (a ``BrokerResponse`` field renamed/
   removed, or a ``Broker.arequest`` signature change), follow the
   stargraph-foundation §15 deprecation flow (deprecate the old field
   for one minor, ship the new field side-by-side, then drop the
   old field on the following minor).

Refs: tasks.md §3.27; design §8.6; FR-49, FR-50, AC-6.6.
"""

from __future__ import annotations

import importlib.metadata

import pytest
from nautilus import Broker, BrokerResponse  # pyright: ignore[reportMissingTypeStubs]

pytestmark = [pytest.mark.serve, pytest.mark.integration]


_PINNED_VERSION = "0.1.5"
"""The canonical ``nautilus-rkm`` distribution pin per :file:`pyproject.toml`."""


@pytest.mark.serve
def test_nautilus_distribution_pin_is_0_1_5() -> None:
    """``importlib.metadata.version("nautilus-rkm") == "0.1.5"``.

    Distribution name is ``nautilus-rkm`` (per :file:`pyproject.toml`'s
    ``[project] dependencies`` block); the import name is ``nautilus``.
    Use the distribution name with :func:`importlib.metadata.version`
    -- the import-side ``nautilus.__version__`` (if it exists) may
    drift independently on local dev installs.

    A failure here means :file:`pyproject.toml` was bumped without the
    operator running through the upgrade-path checklist documented in
    this module's docstring. Bump the assertion only after composition
    tests pass + JSON-schema re-export is current.
    """
    actual = importlib.metadata.version("nautilus-rkm")
    assert actual == _PINNED_VERSION, (
        f"nautilus-rkm distribution pin drift: "
        f"expected {_PINNED_VERSION!r}, got {actual!r}. "
        f"Re-run the upgrade-path checklist before bumping the pin."
    )


@pytest.mark.serve
def test_nautilus_import_surface_is_stable() -> None:
    """The ``nautilus`` import surface still exposes ``Broker`` + ``BrokerResponse``.

    Asserts the three structural contract slices Stargraph's integration
    depends on:

    1. :class:`nautilus.Broker` is importable and has an ``arequest``
       attribute that is callable (the canonical async request entry
       point :class:`stargraph.nodes.nautilus.BrokerNode` invokes).
    2. :class:`nautilus.BrokerResponse` is importable and a Pydantic
       model (carries ``model_json_schema``).
    3. :meth:`BrokerResponse.model_json_schema` returns a serialization-
       mode JSON schema dict (the canonical wire shape Stargraph's IR
       export consumes).

    A break here surfaces upstream API drift before any composition
    test exercises the deeper wiring; the failure mode is a clear
    "import surface changed" message instead of a cascading downstream
    failure.
    """
    # 1. Broker.arequest callable
    assert hasattr(Broker, "arequest"), (
        "nautilus.Broker no longer exposes 'arequest' -- upstream API drift; "
        "follow stargraph-foundation §15 deprecation flow."
    )
    assert callable(Broker.arequest), (
        "nautilus.Broker.arequest is no longer callable -- upstream API drift."
    )

    # 2. BrokerResponse is a Pydantic model
    assert hasattr(BrokerResponse, "model_json_schema"), (
        "nautilus.BrokerResponse is no longer a Pydantic model "
        "(missing model_json_schema) -- upstream API drift."
    )

    # 3. model_json_schema(mode="serialization") returns a dict
    schema = BrokerResponse.model_json_schema(mode="serialization")
    assert isinstance(schema, dict), (
        f"BrokerResponse.model_json_schema(mode='serialization') should "
        f"return a dict; got {type(schema).__name__}"
    )
    assert "properties" in schema, (
        f"BrokerResponse.model_json_schema serialization output is missing "
        f"the 'properties' key; upstream Pydantic shape changed: {schema!r}"
    )


@pytest.mark.serve
def test_brokerresponse_carries_canonical_fields() -> None:
    """The ``BrokerResponse`` Pydantic model carries the canonical CVE-triage fields.

    Locks in the field set Stargraph's :class:`BrokerNode` envelope code
    depends on (provenance carries ``request_id`` as ``external_id``).
    A field rename or removal surfaces here before downstream node
    wiring breaks.

    The canonical field set per ``nautilus-rkm==0.1.5``:
    ``request_id``, ``data``, ``sources_queried``, ``sources_denied``,
    ``sources_skipped``, ``sources_errored``, ``scope_restrictions``,
    ``attestation_token``, ``duration_ms``, ``cap_breached``,
    ``fact_set_hash``, ``source_session_signatures``.
    """
    expected_fields = {
        "request_id",
        "data",
        "sources_queried",
        "sources_denied",
        "sources_skipped",
        "sources_errored",
        "scope_restrictions",
        "attestation_token",
        "duration_ms",
        "cap_breached",
        "fact_set_hash",
        "source_session_signatures",
    }
    actual_fields = set(BrokerResponse.model_fields.keys())
    missing = expected_fields - actual_fields
    assert not missing, (
        f"nautilus.BrokerResponse is missing canonical field(s) {missing!r}; "
        f"upstream API drift -- follow stargraph-foundation §15 deprecation flow. "
        f"Actual fields: {sorted(actual_fields)!r}"
    )
