from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RiskLevel(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AnalystDecision(StrEnum):
    CONFIRM = "confirm"
    REJECT = "reject"
    FLAG = "flag"


class Detection(BaseModel):
    detection_id: str = ""
    tile_id: str = ""
    geo_lat: float = 0.0
    geo_lon: float = 0.0
    confidence: float = 0.0
    obb_corners: list[list[float]] = Field(default_factory=list)
    vessel_length_m: float = 0.0
    class_id: int = 0
    class_name: str = ""
    dark_vessel: bool = False
    ais_mmsi: str | None = None
    ais_vessel_name: str | None = None
    ais_flag_state: str | None = None
    ais_vessel_type: str | None = None
    eez_name: str = ""
    distance_to_port_nm: float = 0.0
    distance_to_coast_nm: float = 0.0
    fishing_zone: bool = False
    geo_summary: str = ""
    risk_score: int = 0
    risk_level: RiskLevel = RiskLevel.LOW
    analyst_decision: AnalystDecision | None = None
    report_text: str = ""
    chip_artifact_ref: str = ""


class TileMetadata(BaseModel):
    tile_id: str = ""
    scene_id: str = ""
    file_path: str = ""
    timestamp: str = ""
    bounds_wkt: str = ""
    patch_count: int = 0


class RunMetrics(BaseModel):
    tiles_processed: int = 0
    total_detections: int = 0
    dark_vessels_flagged: int = 0
    ais_matched: int = 0
    false_positives_rejected: int = 0
    avg_confidence: float = 0.0
    processing_time_seconds: float = 0.0
    model_version: str = ""


class ModelMetrics(BaseModel):
    version: str = ""
    map50: float = 0.0
    map50_95: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    training_samples: int = 0
    holdout_samples: int = 0
    trained_at: str = ""


class SdwState(BaseModel):
    run_id: str = ""
    run_started_at: str = ""
    current_tile: TileMetadata = Field(default_factory=TileMetadata)
    tile_queue: list[str] = Field(default_factory=list)
    tiles_failed: int = 0
    failure_threshold: int = 5
    raw_detections: list[Detection] = Field(default_factory=list)
    detections: list[Detection] = Field(default_factory=list)
    detection_count: int = 0
    ais_query_bbox: str = ""
    ais_query_time_window_min: int = 30
    ais_match_radius_m: int = 500
    has_low_confidence_detections: bool = False
    low_conf_threshold: float = 0.4
    low_conf_count: int = 0
    current_tile_id: str = ""
    analyst_corrections: list[dict[str, Any]] = Field(default_factory=list)
    response_decision: str = ""
    run_metrics: RunMetrics = Field(default_factory=RunMetrics)
    model_version: str = "v1.0"
    last_error: str = ""
    pipeline_phase: str = "ingest"
    # Configurable risk scoring weights (AC-6.4)
    # Configurable risk scoring weights (AC-6.4)
    risk_weight_dark_vessel: int = 40
    risk_weight_sensitive_eez: int = 20
    risk_weight_far_from_port: int = 10
    risk_weight_large_vessel: int = 10
    risk_weight_confidence_max: int = 20
    # Per-node performance timing (AC-1.3)
    perf_marks: dict[str, float] = Field(default_factory=dict)


class RetrainState(BaseModel):
    corrections_count: int = 0
    original_training_samples: int = 0
    merged_training_samples: int = 0
    champion_version: str = ""
    champion_map50: float = 0.0
    challenger_version: str = ""
    challenger_map50: float = 0.0
    challenger_wins: bool = False
    promoted: bool = False
    retrain_metrics: ModelMetrics = Field(default_factory=ModelMetrics)


# ---------------------------------------------------------------------------
# Evolution (self-improvement) state
# ---------------------------------------------------------------------------


class ProposalRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ProposalStatus(StrEnum):
    DRAFT = "draft"
    EVALUATED = "evaluated"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    VERIFIED = "verified"


class ProposalCategory(StrEnum):
    THRESHOLD = "threshold"
    HYPERPARAMETER = "hyperparameter"
    PREPROCESSING = "preprocessing"
    MODEL_ARCHITECTURE = "model_architecture"
    NODE_ADDITION = "node_addition"
    NODE_REMOVAL = "node_removal"
    FLOW_CHANGE = "flow_change"
    RULE_CHANGE = "rule_change"
    DATA_SOURCE = "data_source"


class EvolutionProposal(BaseModel):
    proposal_id: str = ""
    category: ProposalCategory = ProposalCategory.THRESHOLD
    risk: ProposalRisk = ProposalRisk.LOW
    status: ProposalStatus = ProposalStatus.DRAFT
    title: str = ""
    description: str = ""
    rationale: str = ""
    expected_improvement: str = ""
    baseline_metric: float = 0.0
    experiment_metric: float = 0.0
    delta_pct: float = 0.0
    requires_human_approval: bool = False
    approved_by: str = ""
    applied_at: str = ""


class EvolutionState(BaseModel):
    run_id: str = ""
    cycle_number: int = 0
    # Observation phase
    recent_run_count: int = 0
    avg_detection_count: float = 0.0
    avg_confidence: float = 0.0
    avg_processing_seconds: float = 0.0
    false_positive_rate: float = 0.0
    dark_vessel_rate: float = 0.0
    current_model_version: str = ""
    current_model_map50: float = 0.0
    # Analysis phase
    identified_weaknesses: list[str] = Field(default_factory=list)
    improvement_opportunities: list[str] = Field(default_factory=list)
    # Proposal phase
    proposals: list[EvolutionProposal] = Field(default_factory=list)
    active_proposal: EvolutionProposal = Field(default_factory=EvolutionProposal)
    # Experiment phase
    experiment_run_id: str = ""
    experiment_baseline: float = 0.0
    experiment_result: float = 0.0
    experiment_passed: bool = False
    # Governance
    auto_approved: bool = False
    human_approval_required: bool = False
    governance_decision: str = ""
    governance_reason: str = ""
    # Training data curation
    curated_samples_count: int = 0
    training_data_path: str = ""
    # State
    last_error: str = ""
    phase: str = "observe"
