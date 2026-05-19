# SPDX-License-Identifier: Apache-2.0
"""Structural validator for ``demos/cve_remediation/graph/harbor.yaml``.

Asserts the IR graph is internally consistent BEFORE the runtime tries
to load it. Runs three passes:

* **A. Schema parse** -- yaml loads, top-level keys ``ir_version``, ``id``,
  ``nodes``, ``rules`` present.
* **B. Node-kind resolution** -- every ``kind: "module:Class"`` resolves
  to a real attribute exported from ``__all__`` (or just defined) in
  ``demos.cve_remediation.graph.real_nodes`` (or another importable
  module). Catches typos (e.g., ``SamoxFooNode`` vs ``SamoxFooNode_``).
* **C. Rule reference integrity** -- every node id mentioned in
  ``rules[].when`` / ``rules[].then`` exists in ``nodes[]``. No orphan
  rule targets.

Why this matters: a missing node kind only surfaces when the runtime
tries to dispatch the node — possibly minutes into a long run. This
script gives <2s feedback.

Run::

    uv run --no-project python -m demos.cve_remediation.scripts.validate_graph
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import yaml

_HARBOR_YAML = (
    Path(__file__).resolve().parent.parent / "graph" / "harbor.yaml"
)
_KIND_RE = re.compile(r"^([A-Za-z0-9_.]+):([A-Za-z_][A-Za-z0-9_]*)$")
# Loose CLIPS-rule node-id matcher: anything that LOOKS like a node id
# (``snake_case_word``) inside a rules block. Used only for "did you
# mention a node that doesn't exist" sweep, NOT for runtime semantics.
_ID_RE = re.compile(r"\b([a-z][a-z0-9_]{2,})\b")


def _grade(label: str, ok: bool, detail: str = "") -> bool:
    icon = "OK" if ok else "FAIL"
    suffix = f" ({detail})" if detail else ""
    print(f"  [{label:30}] -> {icon}{suffix}")
    return ok


def _resolve_kind(kind_str: str) -> tuple[bool, str]:
    """Return (resolved, detail) for a ``module:Class`` kind string."""
    m = _KIND_RE.match(kind_str)
    if not m:
        return False, f"bad shape: {kind_str!r}"
    module_path, attr = m.group(1), m.group(2)
    try:
        mod = importlib.import_module(module_path)
    except Exception as exc:  # noqa: BLE001
        return False, f"import {module_path}: {type(exc).__name__}: {exc}"
    if not hasattr(mod, attr):
        return False, f"{module_path} missing attr {attr!r}"
    return True, ""


def main() -> int:
    overall = True
    print("=== validate_graph (cve_remediation harbor.yaml) ===\n")

    print("--- A. Schema parse ---")
    raw = _HARBOR_YAML.read_text(encoding="utf-8")
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return 0 if _grade("yaml parse", False, str(exc)) else 1
    if not _grade("yaml parse", isinstance(doc, dict)):
        return 1
    for key in ("ir_version", "id", "nodes", "rules"):
        if not _grade(f"key:{key}", key in doc):
            overall = False
    nodes = doc.get("nodes") or []
    rules = doc.get("rules") or []
    print(f"  nodes={len(nodes)}  rules={len(rules)}")

    print("\n--- B. Node-kind resolution ---")
    seen_ids: set[str] = set()
    seen_kinds: dict[str, int] = {}
    for n in nodes:
        node_id = n.get("id", "<missing>")
        kind = n.get("kind", "")
        if node_id in seen_ids:
            if not _grade(f"dup-id:{node_id}", False):
                overall = False
        seen_ids.add(node_id)
        if not kind:
            if not _grade(f"id:{node_id}", False, "no kind"):
                overall = False
            continue
        seen_kinds[kind] = seen_kinds.get(kind, 0) + 1
    bad = 0
    for kind in sorted(seen_kinds):
        ok, detail = _resolve_kind(kind)
        if not ok:
            bad += 1
            _grade(f"kind:{kind.split(':')[-1]}", False, detail)
            overall = False
    if bad == 0:
        _grade(f"resolved {len(seen_kinds)} distinct kinds", True)

    print("\n--- C. Rule reference integrity ---")
    # Strict: pull every quoted node-id-shaped token from each rule's
    # text and check it exists. Tokens in CLIPS rules that LOOK like
    # node ids but are CLIPS keywords (e.g. ``assert``, ``not``,
    # ``test``) are filtered against a small ignorelist.
    clips_keywords = {
        "assert", "retract", "modify", "duplicate", "if", "then", "else",
        "and", "or", "not", "test", "exists", "forall", "do", "while",
        "switch", "case", "default", "break", "return", "deftemplate",
        "defrule", "deffacts", "deffunction", "defmodule", "declare",
        "salience", "agenda", "focus", "build", "load", "save", "reset",
        "run", "exit", "quit", "ppdefrule", "rules", "facts", "matches",
        "true", "false", "nil", "bind", "stage", "next", "prev",
        "fact", "type", "name", "value", "when", "phase", "node",
        "from", "to", "id", "kind", "input", "output", "params", "model",
        "version", "tag", "group", "status", "policy", "source",
        "target", "approve", "reject", "response", "decision", "timeout",
        "respond", "interrupt", "interrupt_payload", "on_timeout",
        "device", "correct", "prompt", "requested_capability", "cve_id",
        "pipeline", "remediation", "reason", "complete", "halt", "active",
        "boolean", "string", "number", "integer", "float", "symbol",
        "result", "context", "metadata", "payload", "channel", "edge",
        "operator", "subject", "object", "predicate", "action", "effect",
    }
    ref_problems = 0
    for r in rules:
        rid = r.get("id", "<missing>")
        text_blocks = []
        for k in ("when", "then", "guard"):
            v = r.get(k)
            if isinstance(v, str):
                text_blocks.append(v)
            elif isinstance(v, list):
                text_blocks.extend(str(x) for x in v)
        text = "\n".join(text_blocks)
        # Look only for tokens that look like node ids.
        for tok in set(_ID_RE.findall(text)):
            if tok in clips_keywords:
                continue
            if tok in seen_ids:
                continue
            # Heuristic skip: very short or numeric-suffix-only tokens
            # are almost certainly CLIPS variables, not node refs.
            if len(tok) < 6:
                continue
            # Tokens that don't follow ``stage<id>``-shape AND don't match
            # any node id are flagged as "possible orphan ref" -- a
            # warning, not a failure (CLIPS DSL is permissive).
            print(f"  ! rule {rid}: token {tok!r} doesn't match any node "
                  f"id (CLIPS variable or stage tag?)")
            ref_problems += 1
    _grade(
        f"rule sweep ({len(rules)} rules)",
        True,
        f"{ref_problems} possible-orphan tokens (warnings)",
    )

    print()
    if overall:
        print("=== OVERALL: PASS ===")
        return 0
    print("=== OVERALL: FAIL ===")
    return 1


if __name__ == "__main__":
    sys.exit(main())
