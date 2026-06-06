# SPDX-License-Identifier: Apache-2.0
"""Structural-hash incorporates ``PackMount.requires.*`` (FR-40, AC-3.5).

Pins task 2.24's structural-hash extension. Two PackMount-bearing IR
docs that differ ONLY in ``requires.stargraph_facts_version`` (or
``requires.api_version``) must produce different ``graph_hash`` values
-- otherwise a downstream pack version bump could quietly resume a
checkpointed run against an incompatible host.

The extension is **additive**: an IR doc whose every PackMount has
``requires=None`` (the back-compat default) keeps the same canonical
dict shape as before, so existing graph hashes do not drift. This is
verified by ``test_pack_requires_none_keeps_legacy_hash_stable``.
"""

from __future__ import annotations

import pytest

from stargraph.graph.hash import structural_hash
from stargraph.ir._models import (
    IRDocument,
    NodeSpec,
    PackMount,
    PackRequires,
)


def _build_ir(governance: list[PackMount]) -> IRDocument:
    """Tiny IRDocument shell with a single node and the supplied ``governance``.

    Amended per T18: compile state_schema to a BaseModel subclass and inject
    via model_copy before returning. Mirrors Graph.__init__:384 pattern, since
    structural_hash now force-louds on raw dict state_schema (FR-6).
    """
    from stargraph.graph.definition import (
        _compile_state_schema,  # pyright: ignore[reportPrivateUsage]
    )

    ir = IRDocument(
        ir_version="1.0.0",
        id="run:test",
        nodes=[NodeSpec(id="n0", kind="test")],
        governance=governance,
    )
    compiled = _compile_state_schema({}, graph_id="ir-pack-requires-hash")
    return ir.model_copy(update={"state_schema": compiled})  # type: ignore[arg-type]


@pytest.mark.unit
def test_pack_requires_stargraph_facts_version_changes_structural_hash() -> None:
    """Two IR docs identical except for ``requires.stargraph_facts_version`` differ."""
    ir_a = _build_ir(
        [
            PackMount(
                id="bosun.budgets",
                version="1.0",
                requires=PackRequires(stargraph_facts_version="1.0", api_version="1"),
            )
        ]
    )
    ir_b = _build_ir(
        [
            PackMount(
                id="bosun.budgets",
                version="1.0",
                requires=PackRequires(stargraph_facts_version="2.0", api_version="1"),
            )
        ]
    )
    h_a = structural_hash(ir_a, rule_pack_versions=[])
    h_b = structural_hash(ir_b, rule_pack_versions=[])
    assert h_a != h_b


@pytest.mark.unit
def test_pack_requires_api_version_changes_structural_hash() -> None:
    """Two IR docs identical except for ``requires.api_version`` differ."""
    ir_a = _build_ir(
        [
            PackMount(
                id="bosun.audit",
                version="1.0",
                requires=PackRequires(stargraph_facts_version="1.0", api_version="1"),
            )
        ]
    )
    ir_b = _build_ir(
        [
            PackMount(
                id="bosun.audit",
                version="1.0",
                requires=PackRequires(stargraph_facts_version="1.0", api_version="2"),
            )
        ]
    )
    h_a = structural_hash(ir_a, rule_pack_versions=[])
    h_b = structural_hash(ir_b, rule_pack_versions=[])
    assert h_a != h_b


@pytest.mark.unit
def test_pack_requires_none_keeps_legacy_hash_stable() -> None:
    """IR with empty governance produces the same hash as before the 2.24 extension.

    Stability anchor: the structural-hash for an IR doc whose every
    PackMount has ``requires=None`` (or whose ``governance`` is empty)
    must equal the structural-hash computed without any governance
    awareness. Captured by comparing the two-input legacy form
    (governance=[]) against a governance=[PackMount(requires=None)]
    shape -- both routes through the new code path must coincide.
    """
    legacy = _build_ir([])
    requires_none = _build_ir([PackMount(id="bosun.legacy", version="1.0", requires=None)])
    # Legacy: governance is empty, hash component must be omitted.
    h_legacy = structural_hash(legacy, rule_pack_versions=[])
    # requires=None across all mounts: same effective shape -> same hash.
    h_none = structural_hash(requires_none, rule_pack_versions=[])
    assert h_legacy == h_none


@pytest.mark.unit
def test_pack_requires_partial_only_one_field_set_changes_hash() -> None:
    """One-sided ``requires`` (only one field set) is hashed."""
    ir_a = _build_ir(
        [
            PackMount(
                id="bosun.x",
                version="1.0",
                requires=PackRequires(stargraph_facts_version="1.0"),
            )
        ]
    )
    ir_b = _build_ir(
        [
            PackMount(
                id="bosun.x",
                version="1.0",
                requires=PackRequires(api_version="1"),
            )
        ]
    )
    h_a = structural_hash(ir_a, rule_pack_versions=[])
    h_b = structural_hash(ir_b, rule_pack_versions=[])
    assert h_a != h_b


@pytest.mark.unit
def test_pack_requires_governance_pack_id_order_independent() -> None:
    """Sorting the governance list internally keeps hash insertion-order-stable."""
    pm1 = PackMount(
        id="bosun.a",
        version="1.0",
        requires=PackRequires(stargraph_facts_version="1.0", api_version="1"),
    )
    pm2 = PackMount(
        id="bosun.b",
        version="1.0",
        requires=PackRequires(stargraph_facts_version="1.0", api_version="1"),
    )
    h_ab = structural_hash(_build_ir([pm1, pm2]), rule_pack_versions=[])
    h_ba = structural_hash(_build_ir([pm2, pm1]), rule_pack_versions=[])
    assert h_ab == h_ba
