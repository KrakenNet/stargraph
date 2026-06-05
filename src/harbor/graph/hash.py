# SPDX-License-Identifier: Apache-2.0
"""JCS structural hash for harbor graphs (FR-4).

Two deterministic, content-addressable hashes:

* :func:`structural_hash` — sha256 hex digest over the RFC 8785 (JCS)
  canonicalization of the four FR-4 amendment 3 components computed from an
  :class:`~harbor.ir._models.IRDocument` and the run's rule-pack triples.
  Floats are forbidden at any nesting depth (RFC 8785 leaves IEEE-754
  round-trip semantics unstable across runtimes, FR-4 forbids them
  outright); a float anywhere in the canonical payload raises
  :class:`ValueError`.

* :func:`runtime_hash` — sha256 hex digest of ``f"{python_version}\\x00{harbor_version}"``.
  Pinned separately from the structural hash so that an upgrade of the
  Python interpreter or harbor distribution invalidates cached runs even
  when the IR is byte-identical (per design §3.1.1).

The four canonicalization components per FR-4 amendment 3 (built by
:func:`structural_hash`):

(a) **Topology.** Node IDs lex-sorted; edges tuple-sorted by
    ``(src, dst, port)``. Edges are derived from rules' ``then`` action
    lists (``GotoAction.target``, ``ParallelAction.targets``,
    ``RetryAction.target``) — the IR has no native edge list in this POC.
(b) **Node signatures.** ``{node_id: type(node).model_json_schema()}`` keyed
    by node ID, then JCS-lex-sorted at encode time.
(c) **State schema.** ``state_schema.model_json_schema(mode='serialization')``
    when ``state_schema`` is a Pydantic ``BaseModel`` subclass; otherwise
    ``repr(state_schema)`` as a fallback (current POC stores
    ``state_schema`` as a ``dict[str, str]`` placeholder until the model
    compiler lands in task 1.10).
(d) **Rule pack triples.** ``sorted(rule_pack_versions)`` over the
    ``[(pack_id, sha256, version)]`` list (lex-sort by tuple).

The internal :func:`_structural_hash_dict` primitive is the JCS+sha256 core
that both the public :func:`structural_hash` and the dict-based property
tests (task 1.8) call into.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any, cast

import rfc8785
from pydantic import BaseModel

from harbor.errors import IRValidationError
from harbor.ir._models import GotoAction, ParallelAction, RetryAction

if TYPE_CHECKING:
    from harbor.ir._models import IRDocument, PackMount

__all__ = ["runtime_hash", "structural_hash"]

# RFC 8785 / JSON's safe integer domain (IEEE-754 double-precision exact range).
# rfc8785.dumps raises IntegerDomainError outside this range; harbor encodes
# such ints as a sentinel-tagged string to keep the hash total + deterministic
# without mis-encoding them as floats.
_JCS_INT_MAX = 2**53 - 1
_JCS_INT_MIN = -(2**53 - 1)
_BIGINT_PREFIX = "__harbor_bigint__:"


def _reject_floats(value: Any) -> None:
    """Recursively walk ``value``; raise :class:`ValueError` on any ``float``.

    ``bool`` is a subclass of ``int`` in Python and is allowed; we explicitly
    test against the ``float`` type (not ``isinstance(numbers.Real)``) to keep
    bools and ints permitted.
    """
    if type(value) is float:
        raise ValueError("float fields forbidden in hashed payload (FR-4)")
    if isinstance(value, dict):
        for v in cast("dict[Any, Any]", value).values():
            _reject_floats(v)
    elif isinstance(value, (list, tuple)):
        for item in cast("list[Any] | tuple[Any, ...]", value):
            _reject_floats(item)


def _normalize(value: Any) -> Any:
    """Apply two FR-4 normalizations to ``value`` before JCS canonicalization.

    1. **Big-int tagging.** Ints outside the JCS-safe integer domain
       (``±(2**53 - 1)``) are replaced with the string
       ``"__harbor_bigint__:<digits>"``. JSON's float-safe integer range
       collapses larger ints to nearest doubles; tagging them as strings
       keeps the hash total and deterministic without misrepresenting the
       value.
    2. **Topology lex-sort** (FR-4 rule (a)). When a dict has a ``topology``
       key whose value is a dict containing ``nodes`` and/or ``edges`` lists,
       those lists are returned in sorted order: ``nodes`` lex-sorted as
       strings, ``edges`` tuple-sorted as ``(src, dst, port)`` triples.
    """
    if isinstance(value, bool):
        # bool subclasses int; keep it as-is (rfc8785 handles native bools).
        return value
    if isinstance(value, int) and (value > _JCS_INT_MAX or value < _JCS_INT_MIN):
        return f"{_BIGINT_PREFIX}{value}"
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for k, v in cast("dict[Any, Any]", value).items():
            if k == "topology" and isinstance(v, dict):
                topo: dict[Any, Any] = {}
                for tk, tv in cast("dict[Any, Any]", v).items():
                    if tk == "nodes" and isinstance(tv, list):
                        topo[tk] = sorted(
                            (_normalize(item) for item in cast("list[Any]", tv)),
                            key=str,
                        )
                    elif tk == "edges" and isinstance(tv, list):
                        # Tuple-sort by (src, dst, port) — coerce each edge
                        # to a 3-tuple of strings for stable ordering.
                        topo[tk] = sorted(
                            (_normalize(item) for item in cast("list[Any]", tv)),
                            key=_edge_sort_key,
                        )
                    else:
                        topo[tk] = _normalize(tv)
                out[k] = topo
            else:
                out[k] = _normalize(v)
        return out
    if isinstance(value, list):
        return [_normalize(item) for item in cast("list[Any]", value)]
    return value


def _edge_sort_key(edge: Any) -> tuple[str, str, str]:
    """Return a ``(src, dst, port)`` 3-string-tuple sort key for an edge.

    Accepts edges as 2-tuples/lists ``(src, dst)`` (port defaults to ``""``)
    or 3-tuples/lists ``(src, dst, port)``. Anything else falls back to its
    ``str()`` form in the ``src`` slot so sort remains total even on
    unexpected shapes (defensive — production edges go through
    :func:`_topology_from_ir` which always emits 3-tuples).
    """
    if isinstance(edge, (list, tuple)):
        items = [str(item) for item in cast("list[Any] | tuple[Any, ...]", edge)]
        if len(items) >= 3:
            return (items[0], items[1], items[2])
        if len(items) == 2:
            return (items[0], items[1], "")
    return (str(edge), "", "")  # pyright: ignore[reportUnknownArgumentType]


def _topology_from_ir(ir: IRDocument) -> dict[str, list[Any]]:
    """Derive ``{"nodes": [...], "edges": [...]}`` from an IRDocument (rule (a)).

    Nodes: lex-sorted list of ``node.id`` strings.

    Edges: derived from each rule's ``then`` action list. Per the POC IR
    (which has no native edge list), the source of an edge is the rule's
    ``id`` and the destination is the action's ``target`` (or each entry of
    ``targets`` for :class:`ParallelAction`). Port discriminates the action
    kind so a goto and a retry to the same target produce distinct edges.
    Edges are returned as 3-tuples ``(src, dst, port)`` and tuple-sorted.
    """
    nodes_sorted = sorted(node.id for node in ir.nodes)
    edges: list[tuple[str, str, str]] = []
    for rule in ir.rules:
        for action in rule.then:
            if isinstance(action, GotoAction):
                edges.append((rule.id, action.target, "goto"))
            elif isinstance(action, ParallelAction):
                for target in action.targets:
                    edges.append((rule.id, target, "parallel"))
            elif isinstance(action, RetryAction):
                edges.append((rule.id, action.target, "retry"))
            # Halt/Assert/Retract are not graph edges.
    edges_sorted = sorted(edges)
    return {"nodes": nodes_sorted, "edges": edges_sorted}


def _node_signatures(ir: IRDocument) -> dict[str, dict[str, Any]]:
    """Map ``{node_id: type(node).model_json_schema()}`` (rule (b)).

    JCS canonicalization will lex-sort the resulting dict by ``node_id`` at
    encode time, so we need not sort here.
    """
    return {node.id: type(node).model_json_schema() for node in ir.nodes}


def _pack_requires_component(governance: list[PackMount]) -> list[Any] | None:
    """Return the additive ``pack_requires`` hash component (FR-40, AC-3.5).

    Walks ``ir.governance`` and emits a sorted list of
    ``[pack_id, harbor_facts_version_or_None, api_version_or_None]``
    triples for every :class:`PackMount` whose ``requires`` is not
    ``None``. PackMounts with ``requires=None`` (the back-compat
    default) are skipped entirely so the canonical dict shape stays
    byte-identical with pre-2.24 hashes for legacy IR docs.

    Returns ``None`` when no PackMount in ``governance`` has a populated
    ``requires`` block; the caller then omits the ``pack_requires`` key
    from the canonical dict, preserving every hash that was computed
    before this component existed.

    The triples are sorted by ``pack_id`` so insertion-order in
    ``ir.governance`` does not affect the hash.
    """
    triples: list[tuple[str, str | None, str | None]] = []
    for pm in governance:
        if pm.requires is None:
            continue
        triples.append((pm.id, pm.requires.harbor_facts_version, pm.requires.api_version))
    if not triples:
        return None
    triples.sort(key=lambda t: t[0])
    # Emit as plain lists (JCS-friendly) -- tuples encode identically but
    # lists keep the canonical dict reload-stable through json.loads.
    return [list(t) for t in triples]


def _coerce_schema_floats(value: Any) -> Any:
    """Recursively replace ``float`` values in a JSON Schema dict with ints or strings.

    Pydantic emits float defaults (e.g. ``0.0``, ``0.4``) in
    ``model_json_schema()`` output for models with ``float`` fields.
    FR-4 forbids floats in the hashed payload.  This helper converts:

    * Lossless floats (``0.0``, ``1.0``) -> ``int`` (``0``, ``1``)
    * Lossy floats (``0.4``) -> deterministic string (``"0.4"``)

    Applied only to JSON Schema metadata (defaults, examples); runtime
    state values are never hashed here.
    """
    if type(value) is float:
        as_int = int(value)
        if float(as_int) == value:
            return as_int
        return str(value)
    if isinstance(value, dict):
        return {k: _coerce_schema_floats(v) for k, v in cast("dict[Any, Any]", value).items()}
    if isinstance(value, (list, tuple)):
        return [_coerce_schema_floats(item) for item in cast("list[Any] | tuple[Any, ...]", value)]
    return value


def _state_schema_signature(state_schema: Any) -> Any:
    """Return rule (c)'s state-schema component.

    If ``state_schema`` is a Pydantic ``BaseModel`` *subclass*, return its
    serialization-mode JSON schema (the schema produced by
    ``model_dump(mode='json')``, which is what gets persisted across runs).
    All other inputs raise :class:`~harbor.errors.IRValidationError` (FR-6
    force-loud). Callers that previously fed raw ``dict[str, str]`` must
    compile via :func:`harbor.graph.definition._compile_state_schema` first;
    ``Graph.__init__`` already does this.

    Float defaults from Pydantic's JSON Schema output are coerced via
    :func:`_coerce_schema_floats` so the payload satisfies FR-4's no-float
    invariant.
    """
    if isinstance(state_schema, type) and issubclass(state_schema, BaseModel):
        raw = state_schema.model_json_schema(mode="serialization")
        return _coerce_schema_floats(raw)
    raise IRValidationError(
        "state_schema must be a BaseModel subclass at the structural-hash boundary",
        path="state_schema",
        expected="type[BaseModel]",
        actual=type(cast("object", state_schema)).__name__,
        hint=(
            "callers via Graph.__init__ are already compiled; "
            "direct callers must compile via _compile_state_schema"
        ),
    )


def _structural_hash_dict(payload: dict[str, Any]) -> str:
    """Hex-encoded sha256 of the JCS canonicalization of ``payload`` (FR-4).

    The dict-in / hex-out primitive that backs both the public
    :func:`structural_hash` (which builds a four-component canonical dict
    from an IRDocument first) and the property tests pinned by task 1.8.

    Steps:
        1. Recursively reject any ``float`` field at any nesting depth.
        2. Normalize topology: lex-sort ``nodes`` and tuple-sort ``edges``
           by ``(src, dst, port)`` inside any ``topology`` subtree.
        3. Tag JCS-out-of-range ints as ``__harbor_bigint__:<digits>``.
        4. Canonicalize via :func:`rfc8785.dumps` (lex-sorts object keys).
        5. Return ``hashlib.sha256(jcs_bytes).hexdigest()``.

    Raises:
        ValueError: if ``payload`` contains a ``float`` at any depth.
    """
    _reject_floats(payload)
    canonical = _normalize(payload)
    jcs_bytes = rfc8785.dumps(canonical)
    return hashlib.sha256(jcs_bytes).hexdigest()


def structural_hash(
    ir: IRDocument,
    *,
    rule_pack_versions: list[tuple[str, str, str]],
) -> str:
    """Hex-encoded sha256 over the four FR-4 amendment 3 components.

    Builds a canonical dict with:

    (a) ``topology``: lex-sorted node IDs + ``(src, dst, port)``-sorted edges
        derived from rules' ``then`` actions.
    (b) ``node_signatures``: ``{node_id: model_json_schema()}`` per node.
    (c) ``state_schema``: ``model_json_schema(mode='serialization')`` when
        the IR's ``state_schema`` is a ``BaseModel`` subclass; otherwise its
        ``repr()``.
    (d) ``rule_pack_versions``: ``sorted(rule_pack_versions)`` —
        ``[(pack_id, sha256, version)]`` triples, lex-sorted by tuple.

    The dict is then float-rejected and JCS+sha256-hashed by
    :func:`_structural_hash_dict`.

    Args:
        ir: The compiled IR document whose graph topology + node/state
            signatures contribute to the structural identity.
        rule_pack_versions: ``[(pack_id, sha256, version)]`` triples for
            every rule pack mounted in this run; ordering does not affect
            the hash (sorted internally).

    Returns:
        Hex sha256 of the JCS-encoded canonical dict.

    Raises:
        ValueError: if any computed component contains a ``float``.
    """
    canonical: dict[str, Any] = {
        "topology": _topology_from_ir(ir),
        "node_signatures": _node_signatures(ir),
        "state_schema": _state_schema_signature(ir.state_schema),
        "rule_pack_versions": sorted(rule_pack_versions),
    }
    # Additive: only emit ``pack_requires`` when at least one PackMount
    # in ``ir.governance`` declares non-None requires (FR-40, AC-3.5).
    # When absent, the canonical dict shape matches pre-2.24 hashes
    # byte-identically -- existing checkpoints don't drift.
    pack_requires = _pack_requires_component(ir.governance)
    if pack_requires is not None:
        canonical["pack_requires"] = pack_requires
    return _structural_hash_dict(canonical)


def runtime_hash(python_version: str, harbor_version: str) -> str:
    """Hex-encoded sha256 of ``python_version`` + NUL + ``harbor_version``.

    Pinned separately from :func:`structural_hash` (design §3.1.1) so an
    interpreter or distribution upgrade invalidates cached runs even when
    the IR payload is byte-identical.
    """
    encoded = f"{python_version}\x00{harbor_version}".encode()
    return hashlib.sha256(encoded).hexdigest()
