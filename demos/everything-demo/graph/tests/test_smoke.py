# SPDX-License-Identifier: Apache-2.0
"""Smoke tests for the everything-demo IR scaffold.

Verifies structurally (no engine boot) that the IR exercises every
capability the demo claims:

- All node kinds present (echo, dspy, ml, retrieval, tool, broker,
  memory_write, write_artifact, interrupt, subgraph, plus
  ``module:Class`` custom NodeBase imports).
- All seven action variants present in routing rules: goto, halt,
  parallel, retry, assert, retract, interrupt.
- All five store kinds wired (vector, graph, doc, memory, fact).
- All three trigger kinds referenced (manual, cron, webhook).
- Six governance packs mounted (4 stock Bosun + 2 custom).
- HITL gate is durable (timeout: null).
- Sub-graph reference resolves to an on-disk file.
"""

from __future__ import annotations

from pathlib import Path

import yaml

GRAPH_DIR = Path(__file__).resolve().parent.parent
MAIN_IR = GRAPH_DIR / "stargraph.yaml"
SUBGRAPH_IR = GRAPH_DIR / "subgraphs" / "enrichment.yaml"
TRIGGERS_YAML = GRAPH_DIR / "triggers.yaml"
NAUTILUS_YAML = GRAPH_DIR / "nautilus.yaml"
SKILLS_YAML = GRAPH_DIR / "skills" / "triage_skill.yaml"
ROUTING_PACK = GRAPH_DIR / "packs" / "routing" / "pack.yaml"
SAFETY_PACK = GRAPH_DIR / "packs" / "safety" / "pack.yaml"


def _load(p: Path) -> dict:
    return yaml.safe_load(p.read_text())


# ---------------------------------------------------------------------------
# IR structural integrity
# ---------------------------------------------------------------------------


def test_main_ir_loads() -> None:
    ir = _load(MAIN_IR)
    assert ir["ir_version"] == "1.0.0"
    assert ir["id"] == "graph:everything-demo"
    assert isinstance(ir["nodes"], list) and len(ir["nodes"]) > 10


def test_state_class_resolves() -> None:
    ir = _load(MAIN_IR)
    assert ir["state_class"].endswith(":RunState")


# ---------------------------------------------------------------------------
# Node-kind coverage
# ---------------------------------------------------------------------------


REQUIRED_KINDS = {
    "echo",
    "dspy",
    "ml",
    "retrieval",
    "tool",
    "broker",
    "memory_write",
    "write_artifact",
    "interrupt",
    "subgraph",
    "passthrough",
}


def test_all_node_kinds_present() -> None:
    ir = _load(MAIN_IR)
    seen = {n["kind"] for n in ir["nodes"]}
    missing = REQUIRED_KINDS - seen
    assert not missing, f"missing node kinds: {sorted(missing)}"


def test_custom_module_class_nodes_present() -> None:
    ir = _load(MAIN_IR)
    custom = [n for n in ir["nodes"] if ":" in n["kind"]]
    # Three custom NodeBase classes wired: StartSentinel, BranchResponse,
    # LookupHistoryCaller.
    assert len(custom) >= 3
    classes = {n["kind"].rsplit(":", 1)[1] for n in custom}
    assert {"StartSentinel", "BranchResponse", "LookupHistoryCaller"} <= classes


# ---------------------------------------------------------------------------
# Action-variant coverage
# ---------------------------------------------------------------------------


REQUIRED_ACTIONS = {"goto", "halt", "parallel", "retry", "assert", "retract", "interrupt"}


def _all_action_kinds(rules: list[dict]) -> set[str]:
    kinds: set[str] = set()
    for r in rules:
        for action in r.get("then", []):
            kinds.add(action["kind"])
    return kinds


def test_all_action_kinds_present() -> None:
    ir = _load(MAIN_IR)
    seen = _all_action_kinds(ir["rules"])
    missing = REQUIRED_ACTIONS - seen
    assert not missing, f"missing action kinds: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Stores / triggers / governance
# ---------------------------------------------------------------------------


def test_all_five_store_kinds_present() -> None:
    ir = _load(MAIN_IR)
    stores = ir["stores"]
    for kind in ("vector", "graph", "doc", "memory", "fact"):
        assert kind in stores, f"missing store kind: {kind}"


def test_all_three_trigger_kinds_referenced() -> None:
    ir = _load(MAIN_IR)
    triggers = ir["triggers"]
    for kind in ("manual", "cron", "webhook"):
        assert triggers.get(kind), f"missing trigger kind: {kind}"


def test_triggers_yaml_resolves_referenced_ids() -> None:
    ir = _load(MAIN_IR)
    cfg = _load(TRIGGERS_YAML)
    referenced = {
        ref["id"] for kind in ("manual", "cron", "webhook") for ref in ir["triggers"][kind]
    }
    declared = {spec["id"] for kind in ("manual", "cron", "webhook") for spec in cfg.get(kind, [])}
    assert referenced <= declared, f"un-declared trigger ids: {referenced - declared}"


def test_six_governance_packs_mounted() -> None:
    ir = _load(MAIN_IR)
    pack_ids = {p["id"] for p in ir["governance"]}
    expected = {
        "stargraph.bosun.budgets",
        "stargraph.bosun.audit",
        "stargraph.bosun.safety_pii",
        "stargraph.bosun.retries",
        "demo.routing",
        "demo.safety",
    }
    assert expected <= pack_ids, f"missing packs: {expected - pack_ids}"


# ---------------------------------------------------------------------------
# HITL invariants
# ---------------------------------------------------------------------------


def test_hitl_gate_is_durable() -> None:
    ir = _load(MAIN_IR)
    interrupt_actions = [
        action for r in ir["rules"] for action in r.get("then", []) if action["kind"] == "interrupt"
    ]
    assert len(interrupt_actions) >= 1
    for a in interrupt_actions:
        assert a.get("timeout", "missing") is None, "HITL must use timeout: null"


def test_hitl_followed_by_branch_response() -> None:
    ir = _load(MAIN_IR)
    node_ids = [n["id"] for n in ir["nodes"]]
    assert "hitl_review" in node_ids
    assert "branch_response" in node_ids
    # branch_response must come after hitl_review in declaration order
    assert node_ids.index("branch_response") > node_ids.index("hitl_review")


# ---------------------------------------------------------------------------
# Sub-graph + sidecar files
# ---------------------------------------------------------------------------


def test_subgraph_file_exists_and_loads() -> None:
    sub = _load(SUBGRAPH_IR)
    assert sub["id"] == "subgraph:enrichment"
    assert any(n["kind"] == "passthrough" for n in sub["nodes"])


def test_nautilus_yaml_declares_at_least_one_source() -> None:
    cfg = _load(NAUTILUS_YAML)
    assert isinstance(cfg.get("sources"), list) and len(cfg["sources"]) >= 1


def test_skill_manifest_references_state_class() -> None:
    skill = _load(SKILLS_YAML)
    assert skill["state_schema_ref"].endswith(":RunState")
    assert skill["kind"] == "agent"


def test_custom_packs_declare_required_metadata() -> None:
    for path in (ROUTING_PACK, SAFETY_PACK):
        pack = _load(path)
        assert pack["api_version"] == "1"
        assert pack["stargraph_facts_version"] == "1.0"
        assert pack["flavor"] in ("routing", "governance")


# ---------------------------------------------------------------------------
# Capability declarations
# ---------------------------------------------------------------------------


def test_capability_set_covers_all_side_effecting_paths() -> None:
    ir = _load(MAIN_IR)
    caps = set(ir["capabilities"]["required"])
    expected = {
        "tools:lookup_history",
        "tools:notify_user",
        "tools:broker_request",
        "runs:respond",
        "db.episodes:write",
        "db.facts:write",
        "artifacts:write",
    }
    assert expected <= caps


# ---------------------------------------------------------------------------
# Checkpointer
# ---------------------------------------------------------------------------


def test_checkpointer_every_node_exit() -> None:
    ir = _load(MAIN_IR)
    cp = ir["checkpoints"]
    assert cp["every"] == "node-exit"
    assert cp["store"].startswith("sqlite:")
