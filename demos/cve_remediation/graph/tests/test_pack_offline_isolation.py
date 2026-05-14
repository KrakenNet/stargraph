# SPDX-License-Identifier: Apache-2.0
"""Integration: ``cve_rem.offline_isolation`` — CLIPS round-trip tests."""

from __future__ import annotations

import pytest
from fathom import Engine

from ._pack_helpers import load_pack_rules, violations

pytestmark = pytest.mark.integration


def _engine() -> Engine:
    eng = Engine(default_decision="allow")
    load_pack_rules(eng, "cve_rem.offline_isolation")
    return eng


def test_inbound_from_localhost_no_violation() -> None:
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.network_edge (edge_id "e1") (direction "inbound") '
        '(source_zone "localhost") (dest_zone "localhost") '
        '(port 22) (opened_at "t"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    assert violations(eng) == []


def test_inbound_from_production_emits_halt() -> None:
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.network_edge (edge_id "e2") (direction "inbound") '
        '(source_zone "production") (dest_zone "phase6-host") '
        '(port 8080) (opened_at "t"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    v = violations(eng)
    assert any(x["kind"] == "isolation-inbound-from-production" for x in v)


def test_egress_to_approved_drop_no_violation() -> None:
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.network_edge (edge_id "e3") (direction "outbound") '
        '(source_zone "phase6-host") (dest_zone "approved-drop") '
        '(port 443) (opened_at "t"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    assert violations(eng) == []


def test_egress_to_unauthorized_zone_emits_halt() -> None:
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.network_edge (edge_id "e4") (direction "outbound") '
        '(source_zone "phase6-host") (dest_zone "internet") '
        '(port 443) (opened_at "t"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    v = violations(eng)
    assert any(x["kind"] == "isolation-egress-unauthorized" for x in v)


def test_replica_load_with_active_redaction_pack_no_violation() -> None:
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.redaction_pack (pack_hash "rp-1") '
        '(signed_by "krakntrust") (active "true"))'
    )
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.replica_load (load_id "L1") (replica_schema "eval") '
        '(redaction_pack_hash "rp-1") (loaded_at "t"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    assert violations(eng) == []


def test_replica_load_without_redaction_pack_emits_halt() -> None:
    eng = _engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.replica_load (load_id "L2") (replica_schema "eval") '
        '(redaction_pack_hash "") (loaded_at "t"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    v = violations(eng)
    assert any(x["kind"] == "isolation-replica-no-redaction" for x in v)


def test_replica_load_with_stale_redaction_pack_emits_halt() -> None:
    eng = _engine()
    # Active pack is rp-1, but load references rp-old.
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.redaction_pack (pack_hash "rp-1") '
        '(signed_by "krakntrust") (active "true"))'
    )
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(cve_rem.replica_load (load_id "L3") (replica_schema "eval") '
        '(redaction_pack_hash "rp-old") (loaded_at "t"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    v = violations(eng)
    assert any(x["kind"] == "isolation-replica-stale-redaction" for x in v)
