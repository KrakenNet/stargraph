# SPDX-License-Identifier: Apache-2.0
"""The skill smith verify gate — the "always works" contract for skill bundles.

A skill is a graph bundle plus a registerable :class:`~stargraph.skills.base.Skill`
manifest. The three-tier shape + subprocess isolation live in
:mod:`stargraph.skills._smith.gate`; this module supplies the *skill* contract:
assemble the bundle (``state.py`` + ``nodes.py`` + an auto-wired ``graph.yaml`` +
an auto-wired ``manifest.yaml`` + ``test_nodes.py``), then in a subprocess (1) LOAD
the subgraph into a real :class:`stargraph.graph.Graph` and RUN it to a terminal
``done`` (the graph works, the declared outputs are produced), AND (2) construct
the ``Skill`` from the manifest and assert it validates — a real ``kind``, a
replay-safe ``state_schema`` (no ``set`` fields), and declared output channels that
cover what the fixture expects. Because both asserts are on real objects, a
trivially-passing generated unit test cannot land a skill whose subgraph does not
run or whose manifest is not a valid, registerable skill.

``graph.yaml`` and ``manifest.yaml`` are auto-assembled here (not LLM-emitted) so
the wiring (``state_class`` / ``kind`` paths, the ``subgraph`` + ``state_schema``
refs) is correct by construction; the model only writes the state model, the node
logic, and the manifest's domain fields (kind, description, requires, prompt).

TRUST BOUNDARY: see :mod:`stargraph.skills._smith.gate` - tiers 2-3 execute
LLM-generated code in a subprocess (process isolation, not a sandbox); the contract
tier additionally LOADS and RUNS the generated subgraph.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import yaml

from stargraph.skills._smith.gate import (
    RUN_GRAPH_PRELUDE,
    VerifierResult,
    all_passed,
    make_contract_tier,
    run_tiered_gate,
)
from stargraph.skills.graphsmith.gate import assemble_graph_yaml

__all__ = [
    "GRAPH_FILE",
    "MANIFEST_FILE",
    "NODES_FILE",
    "STATE_FILE",
    "TEST_FILE",
    "VerifierResult",
    "all_passed",
    "assemble_graph_yaml",
    "assemble_manifest_yaml",
    "run_full_gate",
    "verify_sources",
]

STATE_FILE = "state.py"
NODES_FILE = "nodes.py"
GRAPH_FILE = "graph.yaml"
MANIFEST_FILE = "manifest.yaml"
TEST_FILE = "test_nodes.py"

_VALID_KINDS = ("agent", "workflow", "utility")


def assemble_manifest_yaml(
    skill_name: str,
    kind: str,
    description: str,
    requires: list[str],
    system_prompt: str,
) -> str:
    """Wire the skill's domain fields into a ``manifest.yaml`` the loader validates.

    The ``state_schema`` ref (``state:State``) and the ``subgraph`` ref
    (``graph.yaml``) are fixed to the bundle's own files so they resolve by
    construction; the model supplies only ``kind`` / ``description`` / ``requires``
    / ``system_prompt``. ``version`` is pinned (a freshly-built skill is 0.1.0).
    """
    data: dict[str, Any] = {
        "id": skill_name or "skill",
        "version": "0.1.0",
        "kind": kind,
        "description": description,
        "state_schema": "state:State",
        "subgraph": GRAPH_FILE,
        "requires": list(requires),
    }
    if system_prompt.strip():
        data["system_prompt"] = system_prompt
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


# The contract driver: the shared run-graph prelude (load + run the assembled
# subgraph to a terminal ``done`` with the fixture's ``expects`` produced) plus the
# skill-only extension — construct the ``Skill`` from ``manifest.yaml`` and assert it
# is a valid, registerable skill (a real ``kind``, a replay-safe ``state_schema`` with
# no ``set`` fields, declared output channels covering what the fixture expects).
# Mirrors the headless `stargraph run` path PLUS the plugin loader's Skill validation.
_SKILL_CONTRACT = """\
import importlib

from stargraph.skills.base import Skill

try:
    manifest = yaml.safe_load(Path("manifest.yaml").read_text())
except Exception as e:
    _fail(f"manifest.yaml unreadable: {type(e).__name__}: {e}")

ref = str(manifest.get("state_schema", ""))
mod_name, _, cls_name = ref.partition(":")
try:
    state_cls = getattr(importlib.import_module(mod_name), cls_name)
except Exception as e:
    _fail(f"manifest state_schema ref {ref!r} did not resolve: {type(e).__name__}: {e}")

try:
    skill = Skill(
        name=str(manifest.get("id", "")),
        version=str(manifest.get("version", "0.1.0")),
        kind=manifest.get("kind", ""),
        description=str(manifest.get("description", "")),
        state_schema=state_cls,
        subgraph=manifest.get("subgraph"),
        system_prompt=manifest.get("system_prompt"),
        requires=list(manifest.get("requires", []) or []),
        tools=list(manifest.get("tools", []) or []),
    )
except Exception as e:
    _fail(f"manifest is not a valid Skill (kind / state_schema / fields?): {type(e).__name__}: {e}")

undeclared = [k for k in expects if k not in skill.declared_output_keys]
if undeclared:
    _fail(
        f"fixture expects {undeclared} but the skill does not declare them as "
        f"output channels {sorted(skill.declared_output_keys)}"
    )

print(json.dumps({"ok": True, "skill": skill.site_id, "kind": str(skill.kind)}))
"""

_CONTRACT_DRIVER = RUN_GRAPH_PRELUDE + _SKILL_CONTRACT


def run_full_gate(
    work_dir: Path,
    files: dict[str, str],
    *,
    fixture: dict[str, Any],
) -> list[VerifierResult]:
    """static → contract → tests in ``work_dir``, short-circuiting on first failure.

    The contract tier loads the assembled subgraph into a real ``Graph`` and runs it
    to ``done`` against ``fixture``, then constructs the ``Skill`` from the manifest
    and asserts it validates; see ``_CONTRACT_DRIVER``.
    """
    return run_tiered_gate(
        work_dir,
        files,
        contract_tier=make_contract_tier(
            _CONTRACT_DRIVER,
            {"fixture": fixture, "meta": {"run_id": "skillsmith-contract", "noun": "subgraph"}},
        ),
        test_file=TEST_FILE,
    )


def verify_sources(
    *,
    skill_name: str,
    kind: str,
    description: str,
    node_classes: list[str],
    state_source: str,
    nodes_source: str,
    test_source: str,
    requires: list[str],
    system_prompt: str,
    fixture: dict[str, Any],
) -> tuple[bool, list[VerifierResult]]:
    """Run the full gate on a raw skill bundle in a throwaway temp dir.

    The convenience entry point for callers that hold source strings rather than a
    work dir — ``skillsmith make``, the doctor preflight, and seed verification.
    Assembles ``graph.yaml`` + ``manifest.yaml`` from the domain fields. Returns
    ``(passed, results)``.
    """
    files = {
        STATE_FILE: state_source,
        NODES_FILE: nodes_source,
        GRAPH_FILE: assemble_graph_yaml(skill_name, node_classes),
        MANIFEST_FILE: assemble_manifest_yaml(
            skill_name, kind, description, requires, system_prompt
        ),
        TEST_FILE: test_source,
    }
    with tempfile.TemporaryDirectory(prefix="skillsmith-verify-") as d:
        results = run_full_gate(Path(d), files, fixture=fixture)
    return all_passed(results), results
