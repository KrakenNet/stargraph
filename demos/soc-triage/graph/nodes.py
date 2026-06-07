# SPDX-License-Identifier: Apache-2.0
"""SOC Triage++ — custom graph node implementations.

Only the nodes that have no stargraph builtin live here; the rest of the
topology (``retrieval`` / ``ml`` / ``dspy`` / ``governance`` / ``interrupt`` /
``write_artifact``) is wired from builtins in ``graph/stargraph.yaml`` (task
1.29). Each custom node subclasses :class:`stargraph.nodes.base.NodeBase` and
returns a dict of state-field mutations merged by the execution loop (FR-11);
nodes never mutate state in place.

``module:ClassName`` nodes are constructed **zero-arg** by
:func:`stargraph.cli.run._build_node_registry` (``NodeSpec.config`` is ignored for
custom refs), so any per-run input is read from :class:`RunState`, not from the
constructor.

Custom nodes:

* :class:`IngestAlert` — reads one ``data/alerts_sample.jsonl`` line into the
  alert fields of :class:`RunState` and builds the ONNX feature vector (the
  ``MLNode`` downstream reads it via its configured ``input_field``).
* :class:`RetrievalPriors` — RRF over the ``data/priors/`` historical-outcome
  records (matched on signature / asset tier) into ``state.priors``. Stargraph's
  builtin ``RetrievalNode`` needs a live vector/graph/doc ``store_resolver``,
  so this self-contained JSONL fusion is wired as a ``module:Class`` node
  instead (zero-arg, builds at serve boot regardless of store wiring).
* :class:`SocWriteArtifact` — persists the analyst case note (``state.reason``)
  to ``.artifacts/<run_id>-soc-case-note.md`` and patches the resulting path
  into ``state.case_note_ref``. Replaces stargraph's builtin ``write_artifact``
  node: that node's :class:`~stargraph.nodes.artifacts.WriteArtifactContext`
  protocol requires ``step`` / ``artifact_store`` / ``is_replay`` attributes
  the serve-path :class:`~stargraph.graph.run.GraphRun` does not carry (the
  scheduler hard-codes ``GraphRun(...)`` with no artifact-store wiring), so the
  builtin raises ``AttributeError`` the moment a resumed HITL run advances past
  the gate — the post-respond hang the spec ``.progress.md`` 5.3 entry records.
  This demo-local writer needs only ``ctx.run_id`` (which ``GraphRun`` always
  carries), mirroring :class:`AuditChain`, so the resumed run completes the
  ``write_artifact → audit → halt`` tail end-to-end.
* :class:`AuditChain` — terminal sink that seals the run's append-only
  ``provenance`` trail into a per-run hash-chained JSONL audit record under
  ``.audit/<run_id>.jsonl``. Replaces the silent ``passthrough`` EchoNode so
  the audit step *records* the evidence chain rather than echoing
  ``state.message``. Each line carries the SHA-256 of ``(prev_sha256 +
  canonical record)`` so deletion / reorder / edit of any provenance fact is
  detectable offline. (The full Ed25519/JWS serve-side sink —
  :class:`stargraph.audit.jsonl.ChainedJSONLAuditSink` — cannot be wired through
  ``serve_soc`` deps today; see the gap note in the spec ``.progress.md``.)
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from graph.state import AssetTier, ProvenanceEvent, RunState
from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Demo package root (…/demos/soc-triage); relative ``alerts_path`` from state
# resolves against it so the node works regardless of the serve cwd.
_DEMO_ROOT = Path(__file__).resolve().parents[1]

# Asset-tier one-hot column order — MUST match scripts/train_severity.py:
#   [asset_tier_dev, asset_tier_staging, asset_tier_prod]
_TIER_ORDER: tuple[AssetTier, ...] = (AssetTier.DEV, AssetTier.STAGING, AssetTier.PROD)


def _tier_onehot(tier: AssetTier) -> list[float]:
    """One-hot encode the asset tier in the train_severity.py column order."""
    return [1.0 if tier is col else 0.0 for col in _TIER_ORDER]


def _append_provenance(state: BaseModel, event: ProvenanceEvent) -> list[ProvenanceEvent]:
    """Return the run's provenance trail with ``event`` appended (audit chain).

    Stargraph's sequential node merge is last-write-wins
    (``state.model_copy(update=outputs)`` — the typed list-append reducer is
    FR-11 "later"), so a node returning a bare ``[event]`` would *replace* the
    trail and collapse it to a single entry. Reading the prior
    ``state.provenance`` and returning the full extended list keeps the
    append-only audit chain intact across every node firing.
    """
    prior: list[ProvenanceEvent] = list(getattr(state, "provenance", []) or [])
    prior.append(event)
    return prior


def _resolve_path(alerts_path: str) -> Path:
    """Resolve a (possibly relative) alerts path against the demo root."""
    p = Path(alerts_path)
    return p if p.is_absolute() else _DEMO_ROOT / p


def _select_line(lines: list[str], *, alert_id: str, alert_index: int) -> dict[str, Any]:
    """Pick the alert record: ``alert_id`` match wins, else the Nth line.

    Blank lines are skipped. Raises :class:`IndexError` / :class:`KeyError`
    when nothing matches so mis-configuration surfaces loudly (caught by
    :meth:`IngestAlert.execute` and recorded on ``last_error``).
    """
    records = [json.loads(line) for line in lines if line.strip()]
    if alert_id:
        for rec in records:
            if str(rec.get("alert_id", "")) == alert_id:
                return rec
        raise KeyError(f"alert_id {alert_id!r} not found")
    return records[alert_index]


class IngestAlert(NodeBase):
    """Read one alert from ``data/alerts_sample.jsonl`` into the run state.

    Maps the JSON SIEM-alert fields onto :class:`RunState`'s alert fields and
    builds the ``[severity_raw, asset_tier_dev, asset_tier_staging,
    asset_tier_prod, source_reputation, hour_of_day, repeat_count]`` float32
    feature vector (order is the contract with ``scripts/train_severity.py``)
    into ``features`` for the downstream ONNX ``MLNode``. Records an ``ingest``
    provenance event so the audit chain starts with the raw alert + features.
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del ctx  # provenance keyed by node id elsewhere; not read here
        try:
            alerts_path: str = getattr(state, "alerts_path", RunState().alerts_path)
            alert_index: int = getattr(state, "alert_index", 0)
            alert_id: str = getattr(state, "alert_id", "")

            path = _resolve_path(alerts_path)
            if not path.exists():
                return {
                    "last_error": f"IngestAlert: alerts file not found: {path}",
                    "pipeline_phase": "ingest",
                }

            lines = path.read_text(encoding="utf-8").splitlines()
            rec = _select_line(lines, alert_id=alert_id, alert_index=alert_index)

            # Field mapping (JSON → state). Unknown tiers fall back to dev.
            tier_raw = str(rec.get("asset_tier", AssetTier.DEV.value)).lower()
            valid_tiers = {t.value for t in AssetTier}
            tier = AssetTier(tier_raw) if tier_raw in valid_tiers else AssetTier.DEV

            severity_raw = float(rec.get("severity_raw", 0.0))
            source_reputation = float(rec.get("source_reputation", 0.0))
            hour_of_day = int(rec.get("hour_of_day", 0))
            repeat_count = int(rec.get("repeat_count", 0))

            # Feature vector — order is the train_severity.py contract.
            features: list[float] = [
                severity_raw,
                *_tier_onehot(tier),
                source_reputation,
                float(hour_of_day),
                float(repeat_count),
            ]

            resolved_id = str(rec.get("alert_id", "")) or alert_id

            event = ProvenanceEvent(
                node="ingest",
                kind="ingest",
                summary=f"ingested alert {resolved_id or '<unknown>'}",
                detail={"alert_id": resolved_id, "features": features},
            )

            return {
                "alert_id": resolved_id,
                "source": str(rec.get("source", "")),
                "signature": str(rec.get("signature", "")),
                "severity_raw": severity_raw,
                "asset_id": str(rec.get("asset_id", "")),
                "asset_tier": tier,
                "source_reputation": source_reputation,
                "timestamp": str(rec.get("timestamp", "")),
                "hour_of_day": hour_of_day,
                "repeat_count": repeat_count,
                "raw_alert": dict(rec),
                "features": features,
                "provenance": _append_provenance(state, event),
                "pipeline_phase": "ingest",
            }
        except Exception as exc:
            logger.exception("IngestAlert failed: %s", exc)
            return {"last_error": f"IngestAlert: {exc}", "pipeline_phase": "ingest"}


# Reciprocal-rank-fusion constant (standard RRF k; smaller = sharper top-rank).
_RRF_K = 60

# Historical-outcome priors live here; created in task 1.32. Missing dir → no
# priors (graceful — the run still scores + decides, just without precedent).
_PRIORS_DIR = _DEMO_ROOT / "data" / "priors"


def _rrf_fuse[K](ranked_lists: Sequence[Sequence[K]]) -> dict[K, float]:
    """Reciprocal-rank fusion of several ranked key lists → {key: rrf_score}.

    Keys can be any hashable (record indices here, ids in the builtin
    RetrievalNode); the type parameter keeps the fusion type-clean regardless.
    """
    scores: dict[K, float] = {}
    for ranked in ranked_lists:
        for rank, key in enumerate(ranked):
            scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + rank + 1)
    return scores


class RetrievalPriors(NodeBase):
    """Fuse historical-outcome priors for the current alert via RRF.

    Reads every ``data/priors/*.jsonl`` record, ranks them under two cheap
    signals — exact ``signature`` match and ``asset_tier`` match — then fuses
    the two ranked lists with Reciprocal Rank Fusion (the same fusion the
    builtin :class:`~stargraph.nodes.retrieval.RetrievalNode` uses) and writes the
    top precedents to ``state.priors`` for the downstream ``dspy`` triage step.

    Self-contained on purpose: the builtin RetrievalNode requires a live
    ``store_resolver`` callable that a config-only IR node can't supply, so
    this node does a file-backed RRF over the seeded priors instead. When the
    priors dir is absent (before task 1.32) it returns an empty list — the
    pipeline degrades to "no precedent" rather than crashing.
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del ctx
        try:
            if not _PRIORS_DIR.is_dir():
                return {"priors": [], "pipeline_phase": "retrieval"}

            records: list[dict[str, Any]] = []
            for path in sorted(_PRIORS_DIR.glob("*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        records.append(json.loads(line))

            signature = str(getattr(state, "signature", ""))
            asset_tier = str(getattr(state, "asset_tier", ""))

            by_signature = [
                i for i, r in enumerate(records) if str(r.get("signature", "")) == signature
            ]
            by_tier = [
                i for i, r in enumerate(records) if str(r.get("asset_tier", "")) == asset_tier
            ]

            fused = _rrf_fuse([by_signature, by_tier])
            top = sorted(fused, key=lambda i: fused[i], reverse=True)[:5]
            priors = [records[i] for i in top]

            event = ProvenanceEvent(
                node="retrieval",
                kind="retrieval",
                summary=f"fused {len(priors)} prior(s) from {len(records)} record(s)",
                detail={
                    "signature": signature,
                    "asset_tier": asset_tier,
                    "prior_count": len(priors),
                },
            )
            return {
                "priors": priors,
                "provenance": _append_provenance(state, event),
                "pipeline_phase": "retrieval",
            }
        except Exception as exc:
            logger.exception("RetrievalPriors failed: %s", exc)
            return {"last_error": f"RetrievalPriors: {exc}", "pipeline_phase": "retrieval"}


# Per-run case-note artifacts land here (one markdown file per run), keyed by
# run id. Kept under the demo root so the artifact ships with the demo checkout.
# `.artifacts/` is created on first write.
_ARTIFACT_DIR = _DEMO_ROOT / ".artifacts"


class SocWriteArtifact(NodeBase):
    """Persist the analyst case note to ``.artifacts/<run_id>-soc-case-note.md``.

    The demo-local stand-in for stargraph's builtin ``write_artifact`` node. The
    builtin requires a :class:`~stargraph.nodes.artifacts.WriteArtifactContext`
    (``run_id`` / ``step`` / ``bus`` / ``artifact_store`` / ``is_replay`` /
    ``fathom``); the ``stargraph serve`` scheduler hard-codes ``GraphRun(...)``
    without an ``artifact_store`` (nor ``step`` / ``is_replay``) attribute, so
    the builtin's ``runtime_checkable`` protocol guard raises ``AttributeError``
    as soon as a resumed HITL run reaches the node — the post-respond hang the
    spec ``.progress.md`` 5.3 entry diagnosed. This node reads only ``ctx.run_id``
    (always present on ``GraphRun``) + ``state.reason`` and writes the case note
    itself, so the resumed run advances ``write_artifact → audit → halt`` to
    completion. Records a ``write_artifact`` provenance event so the artifact
    write is sealed into the audit chain downstream.
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        run_id = getattr(ctx, "run_id", "") or getattr(state, "run_id", "") or "unknown-run"
        reason: str = str(getattr(state, "reason", "") or "")
        disposition = getattr(state, "disposition", "")
        disposition_value = getattr(disposition, "value", disposition)
        try:
            _ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
            safe_run = run_id.replace("/", "_").replace(":", "_")
            artifact_path = _ARTIFACT_DIR / f"{safe_run}-soc-case-note.md"

            alert_id = str(getattr(state, "alert_id", "") or "")
            analyst_decision = str(getattr(state, "analyst_decision", "") or "")
            content = (
                f"# SOC case note — {alert_id or run_id}\n\n"
                f"- disposition: {disposition_value}\n"
                f"- analyst_decision: {analyst_decision or '(pending)'}\n\n"
                f"{reason or '(no triage reason recorded)'}\n"
            )
            artifact_path.write_text(content, encoding="utf-8")

            event = ProvenanceEvent(
                node="write_artifact",
                kind="write_artifact",
                summary=f"wrote case note → {artifact_path.name}",
                detail={
                    "case_note_ref": str(artifact_path),
                    "disposition": str(disposition_value),
                },
            )
            return {
                "case_note_ref": str(artifact_path),
                "provenance": _append_provenance(state, event),
                "pipeline_phase": "write_artifact",
            }
        except Exception as exc:
            logger.exception("SocWriteArtifact failed: %s", exc)
            return {"last_error": f"SocWriteArtifact: {exc}", "pipeline_phase": "write_artifact"}


# Per-run audit records land here (one hash-chained JSONL file per run). Kept
# under the demo root so the artifact ships with the demo checkout and the
# auditor walkthrough can `cat` it. `.audit/` is created on first write.
_AUDIT_DIR = _DEMO_ROOT / ".audit"


def _canonical(record: dict[str, Any]) -> bytes:
    """Stable bytes for hashing a record (sorted keys, no whitespace drift)."""
    return json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")


class AuditChain(NodeBase):
    """Seal the run's provenance trail into a hash-chained JSONL audit record.

    The terminal audit step. Instead of the silent ``EchoNode`` (which just
    copies ``state.message``), this walks the append-only ``state.provenance``
    list and writes one JSONL line per event to ``.audit/<run_id>.jsonl``. Each
    line carries ``prev_sha256`` linkage and ``sha256 = H(prev_sha256 ||
    canonical(record))`` so the chain is tamper-evident offline: deleting,
    reordering, or editing any provenance fact breaks the next line's hash.

    This is the demo-local stand-in for stargraph's Ed25519/JWS
    :class:`~stargraph.audit.jsonl.ChainedJSONLAuditSink`. That sink is the real
    cryptographic mechanism but the ``stargraph serve`` run path never drains the
    event bus into it (only the ``stargraph run`` CLI tees the bus to a sink), so
    it cannot be mounted through ``serve_soc`` deps without new stargraph-lib
    code (out of scope, NFR-8 / Phase-2). The hash chain here gives the demo a
    meaningful, verifiable audit artifact today; the gap is documented in the
    spec ``.progress.md``.
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        run_id = getattr(ctx, "run_id", "") or getattr(state, "run_id", "") or "unknown-run"
        provenance: list[Any] = list(getattr(state, "provenance", []) or [])
        try:
            _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
            # Filesystem-safe filename — run ids carry a `graph:` prefix.
            safe_run = run_id.replace("/", "_").replace(":", "_")
            audit_path = _AUDIT_DIR / f"{safe_run}.jsonl"

            prev = "0" * 64  # genesis link
            lines: list[str] = []
            for seq, ev in enumerate(provenance):
                payload = ev.model_dump(mode="json") if hasattr(ev, "model_dump") else dict(ev)
                record = {
                    "run_id": run_id,
                    "seq": seq,
                    "ts": datetime.now(UTC).isoformat(),
                    "event": payload,
                    "prev_sha256": prev,
                }
                digest = hashlib.sha256(prev.encode("utf-8") + _canonical(record)).hexdigest()
                record["sha256"] = digest
                lines.append(json.dumps(record))
                prev = digest

            audit_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

            seal = ProvenanceEvent(
                node="audit",
                kind="audit",
                summary=f"sealed {len(provenance)} provenance fact(s) → {audit_path.name}",
                detail={
                    "audit_path": str(audit_path),
                    "event_count": len(provenance),
                    "chain_head": prev,
                },
            )
            return {
                "provenance": _append_provenance(state, seal),
                "case_note_ref": getattr(state, "case_note_ref", ""),
                "pipeline_phase": "audit",
            }
        except Exception as exc:
            logger.exception("AuditChain failed: %s", exc)
            return {"last_error": f"AuditChain: {exc}", "pipeline_phase": "audit"}
