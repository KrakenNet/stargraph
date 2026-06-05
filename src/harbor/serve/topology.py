# SPDX-License-Identifier: Apache-2.0
"""IR → UI topology derivation.

Turns an :class:`~harbor.ir.IRDocument` into a flat, UI-renderable graph
shape: typed edges (``goto``, ``loop``, ``parallel``, ``goto_back``,
``interrupt_gate``) keyed by source/target node ids, plus terminal-node
flags. Consumers:

* StarGraph topology endpoint (``GET /v1/graphs/{graph_id}``).
* ``harbor inspect`` future "structure" view.

The IR has no first-class edges -- routing is encoded in
``RuleSpec.then[]`` actions. This module walks every rule, picks an
owning node id (``RuleSpec.node_id`` -- post-1.1.0 explicit, or filled
in by :func:`harbor.ir.backfill_rule_node_ids` for 1.0.0 docs), and
emits one or more :class:`UIEdge` per action.

Action → edge mapping (BACKEND_ARCHITECTURE.md §5):

==================  ==================  =========================================
``Action.kind``     ``UIEdge.kind``     Notes
==================  ==================  =========================================
``goto``            ``goto``            target == owner → ``loop`` instead.
``goto`` (back)     ``goto_back``       target's grid row precedes owner's row.
``retry``           ``loop``            self-edge by definition.
``parallel``        ``parallel``        one edge per ``targets[]``; ``strategy``
                                        and ``join`` carried via :class:`UIEdge`.
``interrupt``       ``interrupt_gate``  no target; owner flagged ``hitl=True``.
``halt``            (no edge)           owner flagged ``is_terminal=True``.
``assert`` /        (no edge)           fact-only actions; not topology.
``retract``
==================  ==================  =========================================

``goto_back`` vs ``goto`` is a render hint -- when the target appears
above the source in the current node ordering (rough proxy for "earlier
in the flow"), the UI draws a back-arc instead of a forward edge. The
underlying IR action is still :class:`~harbor.ir.GotoAction`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harbor.ir._backfill import backfill_rule_node_ids

if TYPE_CHECKING:
    from collections.abc import Iterable

    from harbor.ir import IRDocument

__all__ = [
    "TopologyResponse",
    "UIEdge",
    "UINode",
    "derive_topology",
]


@dataclass(slots=True)
class UINode:
    """UI-side node projection.

    Attributes:
        id: Stable node id (matches :attr:`harbor.ir.NodeSpec.id`).
        kind: Raw IR ``kind`` (short slug or ``module.path:ClassName``).
        is_entry: ``True`` when no edge targets this node and at least
            one rule originates from it; the topology layer surfaces this
            so the UI can highlight the entry without a separate hint.
        is_terminal: ``True`` when the node owns a ``halt`` action OR
            no rule on the node emits any outgoing edge.
        hitl: ``True`` when the node owns an ``interrupt`` action.
    """

    id: str
    kind: str
    is_entry: bool = False
    is_terminal: bool = False
    hitl: bool = False


@dataclass(slots=True)
class UIEdge:
    """UI-side edge projection.

    One :class:`UIEdge` per routing action. ``rule_id`` is always set so
    the UI can request rule details (``GET /v1/rules/{rule_id}``).
    """

    id: str
    source: str
    target: str
    kind: str  # "goto" | "loop" | "parallel" | "goto_back" | "interrupt_gate"
    rule_id: str
    label: str | None = None
    strategy: str | None = None
    join: str | None = None
    backoff_ms: int | None = None


@dataclass(slots=True)
class TopologyResponse:
    """Full topology payload for ``GET /v1/graphs/{graph_id}``."""

    graph_id: str
    ir_version: str
    nodes: list[UINode] = field(default_factory=list[UINode])
    edges: list[UIEdge] = field(default_factory=list[UIEdge])
    orphan_rules: list[str] = field(default_factory=list[str])


def derive_topology(doc: IRDocument) -> TopologyResponse:
    """Walk ``doc.rules`` and emit a :class:`TopologyResponse`.

    Mutates ``doc`` only via :func:`backfill_rule_node_ids` (fills in
    ``RuleSpec.node_id`` for legacy 1.0.0 documents); rule and node
    objects are otherwise read-only.

    Rules whose owning node cannot be determined (``node_id is None``
    after backfill, or pointing at an unknown node) are reported in
    :attr:`TopologyResponse.orphan_rules` and contribute no edges. The
    UI surfaces these as a separate "unowned rules" list rather than
    silently dropping them.
    """
    backfill_rule_node_ids(doc)
    node_index = {n.id: n for n in doc.nodes}
    ui_nodes: dict[str, UINode] = {n.id: UINode(id=n.id, kind=n.kind) for n in doc.nodes}

    # Track who has incoming / outgoing edges so we can flag entry /
    # terminal nodes after the walk.
    out_edges: dict[str, int] = {n.id: 0 for n in doc.nodes}
    in_edges: dict[str, int] = {n.id: 0 for n in doc.nodes}

    edges: list[UIEdge] = []
    orphan: list[str] = []
    # Row index for back-edge heuristic: position in the canonical
    # ``doc.nodes`` order. Forward = target index > source index.
    row_of = {n.id: i for i, n in enumerate(doc.nodes)}

    for rule in doc.rules:
        owner = rule.node_id
        if owner is None or owner not in node_index:
            orphan.append(rule.id)
            continue
        for j, action in enumerate(rule.then):
            edge_id = f"{rule.id}#{j}"
            if action.kind == "goto":
                target = action.target
                if target not in node_index:
                    continue
                edge_kind = (
                    "loop"
                    if target == owner
                    else "goto_back"
                    if row_of[target] < row_of[owner]
                    else "goto"
                )
                edges.append(
                    UIEdge(
                        id=edge_id,
                        source=owner,
                        target=target,
                        kind=edge_kind,
                        rule_id=rule.id,
                    )
                )
                out_edges[owner] += 1
                in_edges[target] += 1
            elif action.kind == "retry":
                target = action.target
                if target not in node_index:
                    continue
                edges.append(
                    UIEdge(
                        id=edge_id,
                        source=owner,
                        target=target,
                        kind="loop",
                        rule_id=rule.id,
                        backoff_ms=action.backoff_ms or None,
                    )
                )
                out_edges[owner] += 1
                in_edges[target] += 1
            elif action.kind == "parallel":
                for k, target in enumerate(action.targets):
                    if target not in node_index:
                        continue
                    edges.append(
                        UIEdge(
                            id=f"{rule.id}#{j}.{k}",
                            source=owner,
                            target=target,
                            kind="parallel",
                            rule_id=rule.id,
                            strategy=action.strategy or None,
                            join=action.join or None,
                        )
                    )
                    out_edges[owner] += 1
                    in_edges[target] += 1
            elif action.kind == "interrupt":
                ui_nodes[owner].hitl = True
                # Synthetic gate edge (source == target) so the UI can
                # render a self-marker without a real outgoing flow.
                edges.append(
                    UIEdge(
                        id=edge_id,
                        source=owner,
                        target=owner,
                        kind="interrupt_gate",
                        rule_id=rule.id,
                    )
                )
            elif action.kind == "halt":
                ui_nodes[owner].is_terminal = True
            # assert / retract: fact-only, no topology contribution.

    # Entry/terminal post-pass: a node with zero in-edges + any out-edge
    # is an entry; a node with any in-edge + zero out-edge (and not an
    # explicit halt owner) is also terminal.
    for nid, node in ui_nodes.items():
        if in_edges[nid] == 0 and out_edges[nid] > 0:
            node.is_entry = True
        if out_edges[nid] == 0 and in_edges[nid] > 0:
            node.is_terminal = True

    return TopologyResponse(
        graph_id=doc.id,
        ir_version=doc.ir_version,
        nodes=list(_ordered(ui_nodes.values(), [n.id for n in doc.nodes])),
        edges=edges,
        orphan_rules=orphan,
    )


def _ordered(items: Iterable[UINode], order: list[str]) -> Iterable[UINode]:
    """Yield nodes in declaration order, matching :attr:`IRDocument.nodes`."""
    by_id = {n.id: n for n in items}
    for nid in order:
        if nid in by_id:
            yield by_id[nid]
