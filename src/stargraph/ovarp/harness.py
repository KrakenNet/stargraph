# SPDX-License-Identifier: Apache-2.0
"""Shared construction path for OVARP-attestable StarGraph ticks (ADR-0012).

:func:`build_attestable_run` is the **single** factory used by *both* sides of
the producer-runtime replay contract:

* the producer (the receipt sink, :mod:`stargraph.ovarp.sink`) builds a run,
  dispatches the governance tick, and emits an OVARP receipt for it;
* the reproducer (``stargraph ovarp-reproduce``, :mod:`stargraph.cli.ovarp_reproduce`)
  rebuilds the *same* run from the receipt's replay bundle and re-runs the tick.

Because both go through one construction path, a re-run reconstructs a
byte-identical tick. :func:`producer_output_from_checkpoint` is the shared
projection both sides apply to the authoritative :class:`Checkpoint`
``dispatch_node`` computes, so neither re-derives node outputs — the attested
``producer_output`` is a deterministic, wall-clock-free view of the checkpoint.

Fathom wiring uses only Fathom's public loader API (``load_templates`` /
``load_modules`` / ``load_rules``). Both the ``stargraph_action`` routing-output
template and one mirror deftemplate per ``Annotated[T, Mirror(...)]`` state field
are registered through ``load_templates`` — that single public call populates
*both* the CLIPS deftemplate (so rules compile + assert) *and* the engine's
Python template registry (so ``engine.query("stargraph_action")`` reads the
asserted routing facts back). The adapter's own
``register_stargraph_action_template`` is deliberately **not** used: it registers
the CLIPS side only, leaving the query-side registry unpopulated.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml
from fathom import Engine

from stargraph.errors import StargraphRuntimeError
from stargraph.fathom import FathomAdapter
from stargraph.graph import Graph, GraphRun
from stargraph.ir import IRDocument, mirrored_fields
from stargraph.nodes.base import EchoNode, NodeBase
from stargraph.runtime.dispatch import dispatch_node

if TYPE_CHECKING:
    from datetime import datetime

    from pydantic import BaseModel

    from stargraph.checkpoint.protocol import Checkpoint, RunSummary
    from stargraph.ir._models import NodeSpec

__all__ = [
    "FATHOM_ROUTER_DECODING_DIGEST",
    "FATHOM_ROUTER_MODEL_DIGEST",
    "CapturingCheckpointer",
    "build_attestable_run",
    "factvector_from_state",
    "governance_decision",
    "load_bundle_from_store",
    "materialize_pack_dir",
    "producer_output_from_checkpoint",
    "reproduce_from_bundle",
]


def _sha256_prefixed(data: bytes) -> str:
    """``sha256:<hex>`` over ``data`` — OVARP's content-address form."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


# Pinned RATS reference values for the Fathom-router reproducer (ADR-0012). The
# emit side binds these into the signed receipt's ``replay_trace``; the reproducer
# refuses any request whose pinned model/decoding digests differ from the runtime
# it actually implements. Both sides import these constants so they cannot drift.
# The identity strings mirror OVARP's producer-runtime golden vector for
# consistency, but ownership of the deterministic Fathom router lives here.
FATHOM_ROUTER_MODEL_DIGEST: str = _sha256_prefixed(b"stargraph-model/fathom-router@v1")
FATHOM_ROUTER_DECODING_DIGEST: str = _sha256_prefixed(
    b"decoding{temperature:0,top_p:1,seed_bound:true}"
)


# Every mirrored fact carries the six provenance slots (written by
# ``assert_with_provenance``) plus the ``value`` payload slot (written by
# ``mirror_state`` as ``str(field)``). Types match ``_sanitize_provenance_slot``
# output: ``_step`` is the integer step index, the rest are strings.
_MIRROR_SLOTS: list[dict[str, Any]] = [
    {"name": "_origin", "type": "string"},
    {"name": "_source", "type": "string"},
    {"name": "_run_id", "type": "string"},
    {"name": "_step", "type": "integer"},
    {"name": "_confidence", "type": "string"},
    {"name": "_timestamp", "type": "string"},
    {"name": "value", "type": "string"},
]

# The routing-output template the governance rules assert (kind + target is all
# ``extract_actions`` reads for a ``goto``). Registered via ``load_templates`` so
# the same call that builds the CLIPS deftemplate also lands it in the engine's
# Python registry, which ``engine.query("stargraph_action")`` requires to read
# the asserted facts back. ``kind`` mirrors the vocabulary of the runtime's
# ``STARGRAPH_ACTION_DEFTEMPLATE`` (``stargraph.fathom._template``).
_STARGRAPH_ACTION_TEMPLATE: dict[str, Any] = {
    "name": "stargraph_action",
    "slots": [
        {
            "name": "kind",
            "type": "symbol",
            "allowed_values": ["goto", "parallel", "halt", "retry", "assert", "retract"],
        },
        {"name": "target", "type": "string", "default": ""},
        {"name": "reason", "type": "string", "default": ""},
    ],
}


class CapturingCheckpointer:
    """No-op :class:`~stargraph.checkpoint.protocol.Checkpointer` that keeps the last write.

    ``dispatch_node`` requires a non-``None`` checkpointer and calls only
    :meth:`write` on the single-tick path; the reproducer reads :attr:`last` to
    project ``producer_output``. The remaining protocol methods exist to satisfy
    the structural type and are never exercised here.
    """

    def __init__(self) -> None:
        self.last: Checkpoint | None = None

    async def bootstrap(self) -> None:
        return None

    async def write(self, checkpoint: Checkpoint) -> None:
        self.last = checkpoint

    async def read_latest(self, run_id: str) -> Checkpoint | None:
        del run_id
        return None

    async def read_at_step(self, run_id: str, step: int) -> Checkpoint | None:
        del run_id, step
        return None

    async def list_runs(
        self, *, since: datetime | None = None, limit: int = 100
    ) -> list[RunSummary]:
        del since, limit
        return []


def _build_node(spec: NodeSpec) -> NodeBase:
    """Resolve a ``NodeSpec.kind`` to a :class:`NodeBase` for an attestable graph.

    Attestable graphs use only an ``echo`` placeholder target or a
    ``module.path:ClassName`` ref to a :class:`NodeBase` subclass — a deliberately
    narrow subset of the CLI's node-kind table (``cli/run.py``), kept here so the
    harness (and the reproducer subprocess) need not import the full typer CLI
    surface.
    """
    kind = spec.kind
    if ":" in kind:
        module_path, _, class_name = kind.partition(":")
        obj: Any = getattr(importlib.import_module(module_path), class_name, None)
        if not (isinstance(obj, type) and issubclass(obj, NodeBase)):
            raise StargraphRuntimeError(f"ovarp harness: {kind!r} is not a NodeBase subclass")
        return obj()
    if kind == "echo":
        return EchoNode()
    raise StargraphRuntimeError(
        f"ovarp harness: unsupported node kind {kind!r}; attestable graphs use "
        "'echo' or a 'module.path:ClassName' NodeBase ref"
    )


def _mirror_template_defs(state_cls: type[BaseModel]) -> list[dict[str, Any]]:
    """One Mirror-shaped deftemplate def per distinct mirror template name of ``state_cls``.

    Each carries the ``_MIRROR_SLOTS`` shape (six provenance slots + ``value``). Fields
    that share a template name collapse to one def. The single source of the mirror
    deftemplate shape for *both* the in-stack adapter (:func:`_build_fathom_adapter`)
    and the lowering pack dir (:func:`materialize_pack_dir`) — so the offline lowered
    IR and the in-stack CLIPS templates can never drift.
    """
    defs: dict[str, dict[str, Any]] = {}
    for rm in mirrored_fields(state_cls).values():
        defs.setdefault(rm.template, {"name": rm.template, "slots": _MIRROR_SLOTS})
    return list(defs.values())


def _build_fathom_adapter(state_cls: type[BaseModel], pack_text: str, tmp: Path) -> FathomAdapter:
    """Stand up a :class:`FathomAdapter` that routes on the state's mirrored facts.

    Registers, via Fathom's public ``load_templates``, the ``stargraph_action``
    routing-output template plus one mirror deftemplate per
    ``Annotated[T, Mirror(...)]`` field of ``state_cls``, then the pack's declared
    module (focused) and the routing (or governance) rules.
    """
    engine = Engine(default_decision="deny")
    adapter = FathomAdapter(engine)

    # ``stargraph_action`` first (routing packs assert it; governance packs ignore
    # it), then one deftemplate per distinct mirror template name.
    template_defs = [_STARGRAPH_ACTION_TEMPLATE, *_mirror_template_defs(state_cls)]
    tfile = tmp / "templates.yaml"
    tfile.write_text(yaml.safe_dump({"templates": template_defs}), encoding="utf-8")
    engine.load_templates(str(tfile))

    module_name = cast("str", cast("dict[str, Any]", yaml.safe_load(pack_text))["module"])
    mfile = tmp / "modules.yaml"
    mfile.write_text(
        yaml.safe_dump(
            {
                "modules": [{"name": module_name, "description": "OVARP attestable routing"}],
                "focus_order": [module_name],
            }
        ),
        encoding="utf-8",
    )
    engine.load_modules(str(mfile))

    pfile = tmp / "pack.yaml"
    pfile.write_text(pack_text, encoding="utf-8")
    engine.load_rules(str(pfile))
    return adapter


def build_attestable_run(
    *,
    ir_dict: dict[str, Any],
    state_values: dict[str, Any],
    fathom_pack_text: str,
    run_id: str,
    checkpointer: Any,
) -> tuple[GraphRun, list[NodeSpec]]:
    """Construct a Fathom-wired :class:`GraphRun` ready to dispatch one tick.

    ``ir_dict`` is the graph IR (its ``state_class`` resolves to a Pydantic
    model with ``Mirror`` fields); ``state_values`` seed that model;
    ``fathom_pack_text`` is the routing pack YAML. Returns the run plus its node
    list (the caller picks the node to dispatch).
    """
    ir = IRDocument.model_validate(ir_dict)
    g = Graph(ir)
    state_cls = g.state_schema
    initial_state = state_cls(**state_values)
    node_registry = {node.id: _build_node(node) for node in ir.nodes}
    with tempfile.TemporaryDirectory() as td:
        # load_templates/modules/rules compile into the engine; the temp files
        # are only needed for the duration of those calls.
        adapter = _build_fathom_adapter(state_cls, fathom_pack_text, Path(td))
    run = GraphRun(
        run_id=run_id,
        graph=g,
        initial_state=initial_state,
        node_registry=node_registry,
        checkpointer=checkpointer,
        fathom=adapter,
    )
    return run, list(ir.nodes)


def factvector_from_state(run: GraphRun, state: dict[str, Any]) -> dict[str, Any]:
    """Project a committed state dict into the OVARP FactVector the lowered pack evaluates.

    Derives the vector from the *same* projection CLIPS used in-stack —
    :meth:`FathomAdapter.mirror_state` — by rehydrating the state model and mapping
    each ``AssertSpec`` to ``{"template": …, "data": <slots>}``. Reusing that one
    projection is load-bearing: the offline vector OVARP re-evaluates (verify step 4)
    must be the same fact set the engine asserted, and sharing ``mirror_state`` means
    the two cannot drift should the mapping ever grow past the ``str(value)`` POC.
    Pass the tick's **post-node** state (``checkpoint.state``) — that is what was mirrored.
    """
    model = run.graph.state_schema.model_validate(state)
    specs = run.fathom.mirror_state(model, {})
    return {"facts": [{"template": s.template, "data": dict(s.slots)} for s in specs]}


def governance_decision(run: GraphRun) -> str:
    """The Fathom governance decision the tick produced in-stack (``allow``/``deny``/…).

    Read from the adapter's ``last_evaluation`` (captured during dispatch's Fathom
    step). Shared by the emit sink and the reproducer so the attested outcome is
    derived one way on both sides of the replay contract — a divergence would break
    the ``result.digest`` match. Fail-closed if the tick produced no decision.

    ``last_evaluation`` is per-adapter mutable state, so a governance attestation
    assumes **single-tick** dispatch (the sink reads it in the same tick it was set).
    A parallel-dispatch governance graph would need the decision carried on the
    :class:`Checkpoint` instead.
    """
    fathom = run.fathom
    ev = fathom.last_evaluation if fathom is not None else None
    decision = ev.decision if ev is not None else None
    if decision is None:
        raise StargraphRuntimeError("ovarp: governance tick produced no Fathom decision")
    return cast("str", decision)


def materialize_pack_dir(
    state_cls: type[BaseModel], rules_text: str, dest: Path, pack_name: str
) -> Path:
    """Write a ``lower-fathom``-ready Fathom pack dir under ``dest/pack_name``.

    Lays out the ``templates/`` ``modules/`` ``rules/`` directory layout
    ``ovarp lower-fathom`` consumes. Templates are the Mirror-shaped deftemplates
    synthesized from ``state_cls`` (the *same* :func:`_mirror_template_defs` the
    in-stack adapter registers — no drift) and deliberately **exclude**
    ``stargraph_action`` (its slot ``default``s are outside the lowering profile,
    and a governance pack never asserts it). The module is the rules' declared
    ``module`` (focused). The pack dir leaf is ``pack_name`` so the lowered IR
    ``id`` equals ``pack_name`` and is stable across emits (reproducible receipt).

    ``pack_name`` becomes a path component under ``dest``, so it is validated as a
    single path segment first — a value with ``/``, ``..``, or an absolute root
    would escape ``dest`` (``pathlib`` drops the base on an absolute join), driving
    ``mkdir``/``write_text`` outside the caller's temp dir. Today ``pack_name`` is
    operator config, but this is a public entry point; the guard mirrors the
    content-address check in :func:`load_bundle_from_store`.
    """
    if Path(pack_name).parts != (pack_name,) or pack_name in ("", ".", ".."):
        raise StargraphRuntimeError(
            f"ovarp lower-fathom: pack_name {pack_name!r} is not a single path segment"
        )
    pack_dir = dest / pack_name
    for sub in ("templates", "modules", "rules"):
        (pack_dir / sub).mkdir(parents=True)
    (pack_dir / "templates" / "access.yaml").write_text(
        yaml.safe_dump({"templates": _mirror_template_defs(state_cls)}), encoding="utf-8"
    )
    module_name = cast("str", cast("dict[str, Any]", yaml.safe_load(rules_text))["module"])
    (pack_dir / "modules" / "governance.yaml").write_text(
        yaml.safe_dump(
            {
                "modules": [{"name": module_name, "description": "OVARP governance pack"}],
                "focus_order": [module_name],
            }
        ),
        encoding="utf-8",
    )
    (pack_dir / "rules" / "access.yaml").write_text(rules_text, encoding="utf-8")
    return pack_dir


def _outcome_from_next_action(next_action: dict[str, Any] | None) -> str:
    """Encode a checkpoint's ``next_action`` as the routing outcome string.

    ``goto`` → ``"goto:<target>"``; ``halt`` → ``"halt"``. ``None`` is a static
    ``continue`` (not produced by the governance ticks this attests).
    """
    if next_action is None:
        return "continue"
    kind = next_action.get("kind")
    if kind == "goto":
        return f"goto:{next_action['target']}"
    if kind == "halt":
        return "halt"
    return str(kind) if kind is not None else "continue"


def producer_output_from_checkpoint(
    checkpoint: Checkpoint, *, outcome: str | None = None
) -> dict[str, Any]:
    """Project the authoritative :class:`Checkpoint` into the attested output body.

    Shared by the sink (emit) and the reproducer so both derive an identical
    ``producer_output`` from one source. Deliberately excludes the checkpoint's
    wall-clock ``timestamp`` — the attested decision must not depend on it — so
    the projection is a pure, reproducible function of the tick's facts.

    ``outcome`` overrides the outcome string. Routing attestations leave it
    ``None`` (the outcome is derived from ``checkpoint.next_action``); a governance
    attestation passes the Fathom decision (``allow``/``deny``/…), which the
    ``next_action`` does not carry.
    """
    return {
        "outcome": outcome
        if outcome is not None
        else _outcome_from_next_action(checkpoint.next_action),
        "node": checkpoint.last_node,
        "step": checkpoint.step,
        "state": checkpoint.state,
        "side_effects_hash": checkpoint.side_effects_hash,
    }


async def reproduce_from_bundle(store_dir: str, bundle_digest: str) -> dict[str, Any]:
    """Rebuild the attested tick from its replay bundle and return ``producer_output``.

    The reproduction half of the producer-runtime replay contract (ADR-0012):
    reads the ``ovarp_replay_bundle`` v1 bundle from the content-addressed store,
    reconstructs the identical :class:`GraphRun` via :func:`build_attestable_run`,
    dispatches the pinned node/step, and projects the authoritative checkpoint.
    OVARP hashes ``JCS(output)`` against the receipt's ``result.digest`` — so the
    only guarantee this side owes is that the tick is byte-reconstructable, which
    the shared factory + :func:`producer_output_from_checkpoint` provide.
    """
    bundle = load_bundle_from_store(store_dir, bundle_digest)
    if bundle.get("ovarp_replay_bundle") != "v1":
        raise StargraphRuntimeError(
            f"ovarp reproduce: unknown replay-bundle version {bundle.get('ovarp_replay_bundle')!r}"
        )
    producer = cast("dict[str, Any]", bundle["producer"])
    cp_sink = CapturingCheckpointer()
    run, nodes = build_attestable_run(
        ir_dict=producer["graph"],
        state_values=producer["pre_state"],
        fathom_pack_text=producer["fathom_pack"],
        run_id="reproduce",
        checkpointer=cp_sink,
    )
    node_id = producer["node_id"]
    node = next((n for n in nodes if n.id == node_id), None)
    if node is None:
        raise StargraphRuntimeError(f"ovarp reproduce: bundle node {node_id!r} not in graph")
    await dispatch_node(run, nodes, node, run.initial_state, int(producer["step"]))
    if cp_sink.last is None:
        raise StargraphRuntimeError("ovarp reproduce: tick wrote no checkpoint")
    if bundle.get("governance"):
        # Governance tick: the attested outcome is the in-stack Fathom decision (not a
        # routing target). Re-derive it via the shared helper — the same one the emit
        # sink used — so producer_output is byte-identical and OVARP's result.digest
        # match holds (verify step 6).
        return producer_output_from_checkpoint(cp_sink.last, outcome=governance_decision(run))
    return producer_output_from_checkpoint(cp_sink.last)


def load_bundle_from_store(store_dir: str, bundle_digest: str) -> dict[str, Any]:
    """Read + integrity-check an OVARP replay bundle from the content-addressed store.

    The store lays artifacts out as ``<store>/blobs/<sha256hex>`` (raw JCS
    bytes). OVARP already integrity-checks the bundle before invoking the
    reproducer; this re-verifies defensively so a corrupt store fails loudly
    here rather than silently reproducing the wrong tick.

    ``bundle_digest`` originates in the (untrusted) receipt ``replay_trace``, so
    its hex tail is validated as a real sha256 content-address *before* it is
    used as a path component — a crafted digest (``sha256:../../etc/x`` or one
    naming ``/dev/zero``) must never reach ``read_bytes`` and drive a path
    traversal or unbounded read at verify time.
    """
    hexpart = bundle_digest.split(":", 1)[-1]
    if len(hexpart) != 64 or any(c not in "0123456789abcdef" for c in hexpart):
        raise StargraphRuntimeError(
            f"ovarp bundle integrity: {bundle_digest!r} is not a sha256 content-address"
        )
    raw = (Path(store_dir) / "blobs" / hexpart).read_bytes()
    actual = _sha256_prefixed(raw)
    if actual != f"sha256:{hexpart}":
        raise StargraphRuntimeError(
            f"ovarp bundle integrity: {bundle_digest} bytes hash to {actual}"
        )
    return cast("dict[str, Any]", json.loads(raw))
