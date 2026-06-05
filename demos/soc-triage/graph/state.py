# SPDX-License-Identifier: Apache-2.0
"""SOC Triage++ — graph run state.

:class:`RunState` is the Pydantic state model the soc-triage++ graph starts
from and threads through every node (``IngestAlert`` → ``retrieval`` → ``ml``
risk_score → ``dspy`` triage_decide → Bosun ``soc-policy`` gate → ``interrupt``
analyst_gate → ``write_artifact`` → audit).

It follows the :class:`demos.sentinel_dark_watch.graph.state.SdwState`
convention: a single ``pydantic.BaseModel`` with field defaults so the graph
can start from a bare ``RunState()`` and let nodes fill fields in via the
field-merge registry (FR-11). Nodes never mutate state in place — each
``execute`` returns a dict keyed by the fields it touches.

Sections:

* **Alert** — the ingested SIEM alert (``IngestAlert`` populates these from a
  ``data/alerts_sample.jsonl`` line).
* **Features** — the ``[severity_raw, asset_tier_dev, asset_tier_staging,
  asset_tier_prod, source_reputation, hour_of_day, repeat_count]`` float32
  vector ``IngestAlert`` builds for the ONNX ``MLNode`` (order is the
  load-bearing contract with ``scripts/train_severity.py``).
* **Risk** — the ``MLNode`` output (label) plus confidence / probabilities the
  soc-policy ``confidence < 0.6`` rule reads.
* **Disposition / reason** — the ``dspy`` triage_decide outputs.
* **Provenance** — the append-only fact/event trail (every node + every
  policy firing records here) that backs the audit chain.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class AssetTier(StrEnum):
    """Asset criticality tier — drives the one-hot feature + soc-policy rules."""

    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class Disposition(StrEnum):
    """Triage outcome — set by ``triage_decide`` and routed by soc-policy."""

    AUTO_REMEDIATE = "auto_remediate"
    ESCALATE = "escalate"
    DISMISS = "dismiss"
    NEEDS_HUMAN = "needs_human"


class ProvenanceEvent(BaseModel):
    """One entry in the run's append-only provenance trail.

    ``node`` names the producing node (or policy pack); ``kind`` is a coarse
    category (``ingest`` / ``ml`` / ``decision`` / ``policy`` / ``artifact``);
    ``summary`` is a human-readable line; ``detail`` carries structured facts
    (feature vector, rule id, probabilities, …) for the audit chain.
    """

    node: str = ""
    kind: str = ""
    summary: str = ""
    detail: dict[str, object] = Field(default_factory=dict)


class RunState(BaseModel):
    """Run state for the soc-triage++ graph."""

    # -- Run identity --------------------------------------------------
    run_id: str = ""
    pipeline_phase: str = "ingest"
    last_error: str = ""

    # -- Alert source selection (read by IngestAlert) ------------------
    # ``module:ClassName`` nodes are constructed zero-arg, so the alert the
    # run should ingest is selected via state (not node config). Default
    # path resolves relative to the demo package; ``alert_index`` picks the
    # JSONL line, ``alert_id`` (if set) wins over the index.
    alerts_path: str = "data/alerts_sample.jsonl"
    alert_index: int = 0
    alert_id: str = ""

    # -- Ingested alert fields -----------------------------------------
    source: str = ""
    signature: str = ""
    severity_raw: float = 0.0
    asset_id: str = ""
    asset_tier: AssetTier = AssetTier.DEV
    source_reputation: float = 0.0
    timestamp: str = ""
    hour_of_day: int = 0
    repeat_count: int = 0
    raw_alert: dict[str, object] = Field(default_factory=dict)

    # -- ONNX feature vector (order is the train_severity.py contract) --
    # [severity_raw, asset_tier_dev, asset_tier_staging, asset_tier_prod,
    #  source_reputation, hour_of_day, repeat_count]
    features: list[float] = Field(default_factory=list)

    # -- Risk (MLNode output_field=risk + confidence handling) ---------
    # The ONNX model surfaces output[0]=label int64; the probabilities/
    # confidence (for the soc-policy confidence<0.6 rule) are filled by the
    # graph from output[1]. risk: 0=low / 1=medium / 2=high.
    risk: int = 0
    risk_confidence: float = 0.0
    risk_probabilities: list[float] = Field(default_factory=list)

    # -- Retrieval (RRF over data/priors/) -----------------------------
    priors: list[dict[str, object]] = Field(default_factory=list)

    # -- Triage decision (dspy) ----------------------------------------
    disposition: Disposition = Disposition.NEEDS_HUMAN
    reason: str = ""

    # -- HITL (analyst_gate interrupt) ---------------------------------
    analyst_decision: str = ""

    # -- Artifact (write_artifact) -------------------------------------
    case_note_ref: str = ""

    # -- Provenance / audit trail (append-only) ------------------------
    provenance: list[ProvenanceEvent] = Field(default_factory=list)
