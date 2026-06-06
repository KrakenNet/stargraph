# SPDX-License-Identifier: Apache-2.0
"""IR-vs-Nautilus prototype fit (AC-18.1-4, FR-34, NFR-8).

Consumes a real Nautilus rule-pack YAML (subset of HIPAA PHI access-control
rules from ``nautilus/rule-packs/data-routing-hipaa/rules/phi-access-control.yaml``,
copied to ``tests/fixtures/nautilus/policy.yaml`` for stable test reference)
and probes whether the Stargraph IR portable subset can express the rule shape.

Two outcomes are accepted:

1. **Lossless fit.** Each Nautilus rule maps cleanly to a :class:`stargraph.ir.RuleSpec`
   inside an :class:`stargraph.ir.IRDocument`; the document validates.
2. **Documented gap.** A field cannot be mapped (e.g. structured ``conditions``
   list, Jinja templated ``denial_reason``, ``salience_band``). The test then
   asserts that ``docs/concepts/ir.md`` carries a "Nautilus prototype gaps"
   section so the gap is tracked before schema freeze.

This is the Phase 1 risk-mitigation prototype only -- full Nautilus → IR
mapping is the engine-spec concern. The smallest portable subset checked
here is: rule ``id`` (from ``name``) and a placeholder ``when`` clause built
from the first condition's ``field``. Everything else is intentionally
out-of-scope; surfacing the gap is the point.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from stargraph.ir import IRDocument, NodeSpec, RuleSpec

FIXTURE_PATH: Path = Path(__file__).parent.parent / "fixtures" / "nautilus" / "policy.yaml"
IR_DOCS_PATH: Path = Path(__file__).parent.parent.parent / "docs" / "concepts" / "ir.md"


def _load_policy() -> dict[str, Any]:
    """Parse the Nautilus fixture YAML into a plain dict."""
    raw = yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), f"expected mapping at top level, got {type(raw)!r}"
    return cast("dict[str, Any]", raw)


def test_nautilus_yaml_parseable() -> None:
    """Fixture must be valid YAML with a Nautilus-shaped ``rules`` block."""
    policy = _load_policy()
    assert "rules" in policy, "Nautilus rule packs must declare a 'rules:' section"
    rules = cast("list[dict[str, Any]]", policy["rules"])
    assert isinstance(rules, list) and len(rules) >= 1, (
        f"expected non-empty rules list, got {rules!r}"
    )
    for rule in rules:
        assert "name" in rule, f"rule missing 'name': {rule!r}"
        assert "conditions" in rule, f"rule missing 'conditions': {rule!r}"


def test_nautilus_minimal_subset_maps_to_ir() -> None:
    """Each rule's ``name`` survives into a Stargraph :class:`RuleSpec`.

    The portable subset deliberately covers only ``id`` (from Nautilus
    ``name``) and an empty ``then``. Everything else (structured
    ``conditions``, Jinja templating, ``salience_band``) is the documented
    gap that test_nautilus_gap_recorded covers.
    """
    policy = _load_policy()
    rules = cast("list[dict[str, Any]]", policy["rules"])

    ir_rules = [RuleSpec(id=str(rule["name"])) for rule in rules]
    doc = IRDocument(
        ir_version="1.0.0",
        id="run:nautilus-fit",
        nodes=[NodeSpec(id="entry", kind="rule")],
        rules=ir_rules,
    )

    assert len(doc.rules) == len(rules)
    assert [r.id for r in doc.rules] == [str(rule["name"]) for rule in rules]


def test_nautilus_gap_recorded_in_ir_docs() -> None:
    """Documented gaps in IR vs Nautilus must be tracked in ``ir.md``.

    The portable IR subset cannot yet round-trip Nautilus's structured
    ``conditions`` list, ``denial_reason`` Jinja templates, or numeric
    ``salience``/``salience_band`` -- :class:`RuleSpec` exposes only ``id``
    plus a free-form ``when`` string and a list of :data:`Action`. Until the
    engine spec lifts those into the IR, the gap belongs in
    ``docs/concepts/ir.md`` so reviewers see it before schema freeze.
    """
    text = IR_DOCS_PATH.read_text(encoding="utf-8")
    assert "Nautilus prototype gaps" in text, (
        f"docs/concepts/ir.md must carry a 'Nautilus prototype gaps' section "
        f"to record IR-vs-Nautilus fit gaps before schema freeze; got:\n{text[:400]}"
    )


@pytest.mark.parametrize(
    "field",
    ["template", "regulation", "title", "action"],
)
def test_nautilus_pack_metadata_present(field: str) -> None:
    """Top-level Nautilus rule-pack metadata is present in the fixture.

    These four fields are not part of the Stargraph IR portable subset today;
    the test asserts only that the fixture preserves them so future tasks
    can decide where they belong (PluginManifest vs governance pack mount
    vs out-of-scope).
    """
    policy = _load_policy()
    assert field in policy, f"Nautilus pack metadata missing '{field}': {policy!r}"
