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
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from graph.state import AssetTier, ProvenanceEvent, RunState
from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
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
                "provenance": [event],
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


def _rrf_fuse(ranked_lists: list[list[str]]) -> dict[str, float]:
    """Reciprocal-rank fusion of several ranked id lists → {id: rrf_score}."""
    scores: dict[str, float] = {}
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
            return {"priors": priors, "provenance": [event], "pipeline_phase": "retrieval"}
        except Exception as exc:
            logger.exception("RetrievalPriors failed: %s", exc)
            return {"last_error": f"RetrievalPriors: {exc}", "pipeline_phase": "retrieval"}
