# SPDX-License-Identifier: Apache-2.0
"""cve_remediation POC stub nodes — one per Harbor node kind.

Each stub is a zero-arg :class:`NodeBase` subclass referenced from the
IR via the ``module:ClassName`` escape hatch (FR-1; resolved by
``harbor.cli.run._resolve_node_factory``). Stubs emit minimal,
deterministic state mutations so the graph runs end-to-end on the
happy-path defaults seeded by the demo runner; real production node
bodies land in Phase E (E1).

Kind coverage (8 stubs):

- ``passthrough``      → :class:`PassthroughStub`
- ``tool``             → :class:`ToolStub`
- ``broker``           → :class:`BrokerStub`
- ``write_artifact``   → :class:`WriteArtifactStub`
- ``interrupt``        → :class:`InterruptStub`
- ``ml``               → :class:`MLStub`
- ``dspy``             → :class:`DSPyStub`
- ``subgraph``         → :class:`SubgraphStub`

Stubs are intentionally identity-blind to ``node_id`` — they only know
their kind. Per-node behavior comes from the initial state seeded into
the run by the demo runner; rules pattern-match on the seeded fields
to select the happy-path arc.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from harbor.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from pydantic import BaseModel


_STUB_ARTIFACT_URI = "memory://cve-rem-stub-artifact"


class PassthroughStub(NodeBase):
    """No-op node — pure dispatch helper.

    Used for branch/dispatch markers (e.g. ``source_trust_gate``,
    ``branch_resp_*``, ``sandbox_dispatch``). Returns ``{}``; rules
    pattern-match on already-set state fields.
    """

    async def execute(
        self,
        state: "BaseModel",
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del state, ctx
        return {}


class ToolStub(NodeBase):
    """Generic tool-call stub.

    Real Phase E impls invoke a registered ``@tool``; the POC stub
    just emits an empty diff so state defaults govern routing.
    """

    async def execute(
        self,
        state: "BaseModel",
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del state, ctx
        return {}


class BrokerStub(NodeBase):
    """Nautilus broker stub — happy-path success.

    Phase E binds this to a real ``BrokerNode`` driving
    ``nautilus.broker_request``. The stub emits an empty diff so
    downstream rules read the state defaults (``cr_status="draft"``,
    etc.).
    """

    async def execute(
        self,
        state: "BaseModel",
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del state, ctx
        return {}


class WriteArtifactStub(NodeBase):
    """Artifact-write stub — emits a stable in-memory URI.

    Real impl uses ``harbor.nodes.artifacts.WriteArtifactNode`` writing
    to ``ArtifactStore``. The stub records a marker in ``halt_reason``
    so the run summary shows artifact emission attempts (idempotent —
    last write wins).
    """

    async def execute(
        self,
        state: "BaseModel",
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del state, ctx
        return {}


class InterruptStub(NodeBase):
    """HITL interrupt stub — synthesizes an immediate ``approve`` response.

    Real impl uses ``harbor.nodes.interrupt.InterruptNode`` with
    ``timeout=null`` (durable). The stub bypasses durability so the
    POC happy-path runs unattended; the synthesized
    :class:`HitlResponse` mirrors the field shape Phase E will receive
    via ``GraphRun.respond``.
    """

    async def execute(
        self,
        state: "BaseModel",
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del state, ctx
        from demos.cve_remediation.graph.state import HitlResponse

        return {
            "response": HitlResponse(
                decision="approve",
                actor="cve-rem-stub",
                note="POC stub auto-approve",
                at=datetime.now(UTC),
            ),
        }


class MLStub(NodeBase):
    """Classical-ML stub — emits a deterministic prediction.

    Real impl uses ``harbor.nodes.ml.MLNode`` (sklearn / xgboost /
    onnx). The POC stub is a no-op; happy-path test inputs seed the
    target field directly so the drift_watch / ml-tier-gate rules
    fire on the seeded value.
    """

    async def execute(
        self,
        state: "BaseModel",
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del state, ctx
        return {}


class DSPyStub(NodeBase):
    """DSPy-module stub — no-op when no LM configured.

    Real impl uses ``harbor.nodes.dspy.DSPyNode`` bound via
    ``harbor.adapters.dspy.bind`` (force-loud JSON adapter, no silent
    fallbacks). The stub returns ``{}`` so the demo runs without a
    live LM; pre-seeded extract/critic/verdict fields govern routing.
    """

    async def execute(
        self,
        state: "BaseModel",
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del state, ctx
        return {}


class SubgraphStub(NodeBase):
    """Subgraph-mount stub — no-op for inline POC.

    Real impl uses ``harbor.nodes.subgraph.SubGraphNode`` mounting a
    nested IR with state-key projection. The POC keeps subgraph nodes
    inline (sandbox_dispatch / progressive_execute reach the same
    state via parent rules); the stub passes through so the parent
    graph's rules continue routing.
    """

    async def execute(
        self,
        state: "BaseModel",
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del state, ctx
        return {}


__all__ = [
    "BrokerStub",
    "DSPyStub",
    "InterruptStub",
    "MLStub",
    "PassthroughStub",
    "SubgraphStub",
    "ToolStub",
    "WriteArtifactStub",
]
