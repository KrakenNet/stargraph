# SPDX-License-Identifier: Apache-2.0
"""Tool-id consolidation + canonical-broker invariants.

Verifies:
  - Every IR with a broker node declares ``nautilus.broker_request@1``.
  - No IR declares duplicate broker tool entries (consolidation done).
  - Tool ids match `tool-id-mapping.md` master table (no orphans).
  - artifacts.write declared in every IR that uses write_artifact nodes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

GRAPH_DIR = Path(__file__).resolve().parent.parent
MAIN_IR = GRAPH_DIR / "harbor.yaml"
PHASE0_IR = GRAPH_DIR / "phase0" / "doctrine_ingest.yaml"
PHASE6_IR = GRAPH_DIR / "phase6" / "offline_learning.yaml"
TRIGGERED_IRS = sorted((GRAPH_DIR / "triggered").glob("*.yaml"))
ALL_IRS = [MAIN_IR, PHASE0_IR, PHASE6_IR, *TRIGGERED_IRS]
TOOL_MAPPING_DOC = GRAPH_DIR / "tool-id-mapping.md"

CANONICAL_BROKER_ID = "nautilus.broker_request"
CANONICAL_BROKER_VERSION = "1"


def _load(p: Path) -> dict:
    return yaml.safe_load(p.read_text())


def _tool_ids(ir_path: Path) -> list[tuple[str, str | None]]:
    doc = _load(ir_path)
    return [(t["id"], t.get("version")) for t in doc.get("tools", [])]


# Source the kind-mapping from the smoke-test stub map so a single table
# governs both structural smoke tests AND tool-id consolidation tests.
from demos.cve_remediation.graph.tests.test_smoke import (  # noqa: E402
    _KIND_FROM_STUB,
)

_BROKER_KINDS = {"broker"} | {
    promoted for promoted, short in _KIND_FROM_STUB.items() if short == "broker"
}
_WRITE_ARTIFACT_KINDS = {"write_artifact"} | {
    promoted for promoted, short in _KIND_FROM_STUB.items() if short == "write_artifact"
}


def _has_broker_node(ir_path: Path) -> bool:
    doc = _load(ir_path)
    return any(n.get("kind") in _BROKER_KINDS for n in doc.get("nodes", []))


def _has_write_artifact_node(ir_path: Path) -> bool:
    doc = _load(ir_path)
    return any(n.get("kind") in _WRITE_ARTIFACT_KINDS for n in doc.get("nodes", []))


# --- canonical broker invariant ---------------------------------------------


@pytest.mark.parametrize("ir_path", ALL_IRS, ids=lambda p: p.name)
def test_broker_ir_declares_canonical_broker_tool(ir_path: Path) -> None:
    if not _has_broker_node(ir_path):
        pytest.skip("no broker nodes in this IR")
    tools = _tool_ids(ir_path)
    matches = [t for t in tools if t[0] == CANONICAL_BROKER_ID]
    assert len(matches) == 1, (
        f"{ir_path.name}: expected exactly one {CANONICAL_BROKER_ID} entry, "
        f"found {len(matches)}: {matches}"
    )
    assert matches[0][1] == CANONICAL_BROKER_VERSION, (
        f"{ir_path.name}: broker version is {matches[0][1]!r}, "
        f"expected {CANONICAL_BROKER_VERSION!r}"
    )


@pytest.mark.parametrize("ir_path", ALL_IRS, ids=lambda p: p.name)
def test_no_duplicate_tool_ids(ir_path: Path) -> None:
    tools = _tool_ids(ir_path)
    ids = [t[0] for t in tools]
    duplicates = {i for i in ids if ids.count(i) > 1}
    assert not duplicates, f"{ir_path.name} has duplicate tool ids: {duplicates}"


@pytest.mark.parametrize("ir_path", ALL_IRS, ids=lambda p: p.name)
def test_no_legacy_pernode_broker_ids(ir_path: Path) -> None:
    """Pre-consolidation we had nautilus.servicenow_change_request etc.
    Those should all be gone — consolidated to nautilus.broker_request."""
    legacy = {
        "nautilus.servicenow_change_request",
        "nautilus.docplus_publish",
        "nautilus.gnmi_query",
        "nautilus.influx_query",
        "nautilus.pagerduty_post",
        "nautilus.health_probe",
        "nautobot.asset_lookup",
        "cmdb.ci_lookup",
        "cargonet.deploy_lab",
        "cargonet.probe",
    }
    tools = {t[0] for t in _tool_ids(ir_path)}
    overlap = tools & legacy
    assert not overlap, f"{ir_path.name} still declares legacy ids: {overlap}"


@pytest.mark.parametrize("ir_path", ALL_IRS, ids=lambda p: p.name)
def test_artifact_emitting_ir_declares_artifacts_write(ir_path: Path) -> None:
    if not _has_write_artifact_node(ir_path):
        pytest.skip("no write_artifact nodes in this IR")
    tools = {t[0] for t in _tool_ids(ir_path)}
    assert "artifacts.write" in tools, (
        f"{ir_path.name} has write_artifact nodes but no artifacts.write tool decl"
    )


# --- referential integrity with the mapping doc ------------------------------


def _ids_in_doc() -> set[str]:
    text = TOOL_MAPPING_DOC.read_text()
    # Match `<tool.id@version>` rows in the master table.
    import re
    return set(re.findall(r"`([a-z][a-z0-9_]*\.[a-z0-9_]+)@", text))


def test_every_declared_tool_has_a_mapping_entry() -> None:
    declared: set[str] = set()
    for ir_path in ALL_IRS:
        for tid, _ in _tool_ids(ir_path):
            declared.add(tid)
    documented = _ids_in_doc()
    undocumented = declared - documented
    assert not undocumented, (
        f"tools declared in IR but missing from tool-id-mapping.md: {undocumented}"
    )


def test_no_dangling_mapping_entries() -> None:
    declared: set[str] = set()
    for ir_path in ALL_IRS:
        for tid, _ in _tool_ids(ir_path):
            declared.add(tid)
    documented = _ids_in_doc()
    dangling = documented - declared
    assert not dangling, (
        f"tool-id-mapping.md lists ids no IR uses: {dangling}"
    )


def test_canonical_broker_id_matches_harbor_registry() -> None:
    """The single registered Harbor tool today is nautilus.broker_request@1.
    Confirm our canonical id matches what the registry resolves."""
    from harbor.tools.nautilus.broker_request import broker_request

    spec = broker_request.spec  # attached by @tool decorator
    assert spec.namespace == "nautilus", f"broker_request namespace={spec.namespace!r}"
    assert spec.name == "broker_request", f"broker_request name={spec.name!r}"
    assert spec.version == "1", f"broker_request version={spec.version!r}"

    # And our canonical ID + version constants match.
    assert f"{spec.namespace}.{spec.name}" == CANONICAL_BROKER_ID
    assert spec.version == CANONICAL_BROKER_VERSION
