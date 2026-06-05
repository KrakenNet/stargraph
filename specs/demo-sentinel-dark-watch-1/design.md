---
spec: demo-sentinel-dark-watch-1
phase: design
created: 2026-05-26
---

# Technical Design: Sentinel Dark Watch

## Overview

Maritime SAR surveillance pipeline built on Harbor (graph engine) and Nautilus (data broker). Sentinel-1 SAR tiles flow through YOLO-OBB vessel detection, AIS correlation flags dark vessels, DSPy agents enrich with geo-context and draft intel reports, analysts review/correct via Streamlit, and a nightly retrain sub-graph closes the self-improvement loop. Architecture mirrors CVE-rem: `docker-compose.yml` + `bootstrap.py` + `harbor.yaml` IR + Pydantic state + real node implementations + `serve_sdw.py` + `nautilus.yaml`.

Two Harbor graphs: a main pipeline graph (`harbor.yaml`) handling ingest-through-reporting, and a retrain sub-graph (`retrain.yaml`) handling correction collection, model fine-tuning, champion/challenger promotion. The retrain graph is invoked via `SubGraphNode` from the main graph's cron trigger or manually via `make retrain`. Streamlit UI is fully decoupled -- talks to `harbor serve` REST/WS on port 9001. All geo dependencies (`rasterio`, `geopandas`, `shapely`, `ultralytics`) are gated behind a new `[sdw]` extra in the monorepo `pyproject.toml`.

## Architecture Diagram

```mermaid
graph TB
    subgraph Docker["Docker Compose"]
        PG[(PostGIS 5441)]
        Redis[(Redis 6391)]
        LLM[llm-shim 41001]
    end

    subgraph HarborServe["harbor serve :9001"]
        API[FastAPI REST/WS]
        Loop[Graph Loop]
        CP[SQLite Checkpointer]
        AR[Filesystem ArtifactStore]
        MR[ModelRegistry SQLite]
    end

    subgraph MainGraph["Main Pipeline Graph"]
        Ingest[SARIngestNode] --> YOLO[YOLOInferenceNode]
        YOLO --> NMS[NMSDeduplicationNode]
        NMS --> LandMask[LandMaskFilterNode]
        LandMask --> AIS[AISCorrelationNode]
        AIS --> Geo[GeoContextNode<br/>NodeBase + DSPy]
        Geo --> Risk[RiskScoringNode]
        Risk --> Report[ReportingNode<br/>NodeBase + DSPy]
        Report --> Chip[WriteArtifactNode<br/>SAR chip]
        Chip --> Review[AnalystReviewNode<br/>InterruptNode]
        Review --> Collect[MetricsCollectorNode]
        Collect --> Done[action_done]
    end

    subgraph RetrainGraph["Retrain Sub-Graph"]
        RC[RetrainCollectNode] --> RT[RetrainTrainNode]
        RT --> CC[ChampionChallengerNode]
        CC --> RM[RetrainMetricsNode]
    end

    subgraph External["External Services"]
        AISStream[AISStream.io WS]
        Ollama[Ollama :41001]
    end

    subgraph UI["Streamlit :8501"]
        Map[Map View]
        Panel[Detection Panel]
        Metrics[Metrics Dashboard]
    end

    AISStream -->|ais_ingest.py| PG
    AIS -->|BrokerNode| PG
    Geo -->|DSPy| LLM
    Report -->|DSPy| LLM
    UI -->|REST/WS| API
    Review -->|POST /respond| UI
    YOLO -->|ONNX session| MR
    Loop --> CP
    Chip --> AR
```

## Harbor Graph Design

### Main Pipeline Graph (`graph/harbor.yaml`)

```yaml
ir_version: "1.0.0"
id: "graph:sdw-pipeline"

nodes:
  # ---------- Phase 1: Ingest ----------
  - id: sar_ingest
    kind: "demos.sentinel_dark_watch.graph.nodes:SARIngestNode"
  - id: yolo_inference
    kind: "demos.sentinel_dark_watch.graph.nodes:YOLOInferenceNode"
  - id: nms_dedup
    kind: "demos.sentinel_dark_watch.graph.nodes:NMSDeduplicationNode"
  - id: land_mask_filter
    kind: "demos.sentinel_dark_watch.graph.nodes:LandMaskFilterNode"

  # ---------- Phase 2: Correlate ----------
  - id: ais_correlation
    kind: "demos.sentinel_dark_watch.graph.nodes:AISCorrelationNode"

  # ---------- Phase 3: Enrich + Score ----------
  - id: geo_context
    kind: "demos.sentinel_dark_watch.graph.nodes:GeoContextNode"
  - id: risk_scoring
    kind: "demos.sentinel_dark_watch.graph.nodes:RiskScoringNode"

  # ---------- Phase 4: Report + HITL ----------
  - id: reporting
    kind: "demos.sentinel_dark_watch.graph.nodes:ReportingNode"
  - id: emit_sar_chips
    kind: "demos.sentinel_dark_watch.graph.nodes:EmitSARChipsNode"
  - id: analyst_review
    kind: "demos.sentinel_dark_watch.graph.nodes:AnalystReviewNode"
  - id: branch_resp_review
    kind: "demos.sentinel_dark_watch.graph.nodes:PassthroughNode"

  # ---------- Phase 5: Metrics + Terminal ----------
  - id: metrics_collector
    kind: "demos.sentinel_dark_watch.graph.nodes:MetricsCollectorNode"
  - id: retrain_trigger
    kind: "demos.sentinel_dark_watch.graph.nodes:RetrainTriggerNode"
  - id: action_done
    kind: "demos.sentinel_dark_watch.graph.nodes:PassthroughNode"

state_class: "demos.sentinel_dark_watch.graph.state:SdwState"

tools:
  - id: nautilus.broker_request
    version: "1"

governance:
  - id: harbor.bosun.budgets
    version: "1.0"
    requires: { harbor_facts_version: "1.0", api_version: "1" }
  - id: harbor.bosun.audit
    version: "1.0"
    requires: { harbor_facts_version: "1.0", api_version: "1" }
  # v2: domain-specific packs (sdw.routing, sdw.active_learning) deferred

stores: []  # v2 — real store providers deferred

rules:
  # =========================================================================
  # Phase 1: Ingest → Detect → Filter
  # =========================================================================
  - id: r-ingest-to-yolo
    when: "?n <- (node-id (id sar_ingest))"
    then: [{ kind: goto, target: yolo_inference }]
  - id: r-yolo-to-nms
    when: "?n <- (node-id (id yolo_inference))"
    then: [{ kind: goto, target: nms_dedup }]
  - id: r-nms-to-landmask
    when: "?n <- (node-id (id nms_dedup))"
    then: [{ kind: goto, target: land_mask_filter }]

  # =========================================================================
  # Phase 2: AIS Correlation
  # =========================================================================
  - id: r-landmask-to-ais
    when: "?n <- (node-id (id land_mask_filter))"
    then: [{ kind: goto, target: ais_correlation }]

  # =========================================================================
  # Phase 3: Enrich + Score
  # =========================================================================
  - id: r-ais-to-geo
    when: "?n <- (node-id (id ais_correlation))"
    then: [{ kind: goto, target: geo_context }]
  - id: r-geo-to-risk
    when: "?n <- (node-id (id geo_context))"
    then: [{ kind: goto, target: risk_scoring }]

  # =========================================================================
  # Phase 4: Report + HITL
  # =========================================================================
  # FR-17: Active learning routing — mutually exclusive on has_low_confidence_detections.
  # Low-conf path: risk_scoring → analyst_review (HITL) → branch_resp_review → reporting → chips → metrics
  # Auto-accept path: risk_scoring → reporting → chips → metrics (no HITL, no branch_resp)
  - id: r-review-low-conf
    when: "?n <- (node-id (id risk_scoring)) (state (has_low_confidence_detections true))"
    then: [{ kind: goto, target: analyst_review }]
  - id: r-review-auto-accept
    when: "?n <- (node-id (id risk_scoring)) (state (has_low_confidence_detections false))"
    then: [{ kind: goto, target: reporting }]

  # HITL analyst review gate (low-conf path only)
  - id: r-analyst-gate
    when: "?n <- (node-id (id analyst_review))"
    then:
      - kind: interrupt
        prompt: "Review {detection_count} detections ({low_conf_count} low-confidence) for tile {current_tile_id}. Confirm/reject each."
        interrupt_payload:
          requested_capability: "runs:respond"
        requested_capability: "runs:respond"
        timeout: null
        on_timeout: "noop"
  # After HITL resolves, route to branch_resp_review for approve/reject handling
  - id: r-analyst-to-branch
    when: "?n <- (node-id (id analyst_review))"
    then: [{ kind: goto, target: branch_resp_review }]
  # Branch on analyst decision
  - id: r-branch-review-approve
    when: "?n <- (node-id (id branch_resp_review)) (response (decision approve))"
    then: [{ kind: goto, target: reporting }]
  - id: r-branch-review-reject
    when: "?n <- (node-id (id branch_resp_review)) (response (decision reject))"
    then: [{ kind: goto, target: reporting }]

  # Reporting → chips → metrics (shared by both paths)
  - id: r-report-to-chips
    when: "?n <- (node-id (id reporting))"
    then: [{ kind: goto, target: emit_sar_chips }]
  - id: r-chips-to-metrics
    when: "?n <- (node-id (id emit_sar_chips))"
    then: [{ kind: goto, target: metrics_collector }]

  # =========================================================================
  # Phase 5: Metrics + Terminal
  # =========================================================================
  - id: r-metrics-to-retrain
    when: "?n <- (node-id (id metrics_collector))"
    then: [{ kind: goto, target: retrain_trigger }]
  - id: r-retrain-to-done
    when: "?n <- (node-id (id retrain_trigger))"
    then: [{ kind: goto, target: action_done }]
  - id: r-done-halt
    when: "?n <- (node-id (id action_done))"
    then: [{ kind: halt, reason: "Pipeline run complete" }]
```

### Retrain Sub-Graph (`graph/retrain.yaml`)

```yaml
ir_version: "1.0.0"
id: "graph:sdw-retrain"

nodes:
  - id: collect_corrections
    kind: "demos.sentinel_dark_watch.graph.nodes:RetrainCollectNode"
  - id: retrain_model
    kind: "demos.sentinel_dark_watch.graph.nodes:RetrainTrainNode"
  - id: champion_challenger
    kind: "demos.sentinel_dark_watch.graph.nodes:ChampionChallengerNode"
  - id: retrain_metrics
    kind: "demos.sentinel_dark_watch.graph.nodes:RetrainMetricsNode"
  - id: retrain_done
    kind: "demos.sentinel_dark_watch.graph.nodes:PassthroughNode"

state_class: "demos.sentinel_dark_watch.graph.state:RetrainState"

rules:
  - id: r-collect-to-train
    when: "?n <- (node-id (id collect_corrections))"
    then: [{ kind: goto, target: retrain_model }]
  - id: r-train-to-eval
    when: "?n <- (node-id (id retrain_model))"
    then: [{ kind: goto, target: champion_challenger }]
  - id: r-eval-promote
    when: "?n <- (node-id (id champion_challenger)) (state (challenger_wins true))"
    then: [{ kind: goto, target: retrain_metrics }]
  - id: r-eval-reject
    when: "?n <- (node-id (id champion_challenger)) (state (challenger_wins false))"
    then: [{ kind: goto, target: retrain_metrics }]
  - id: r-metrics-done
    when: "?n <- (node-id (id retrain_metrics))"
    then: [{ kind: goto, target: retrain_done }]
  - id: r-retrain-halt
    when: "?n <- (node-id (id retrain_done))"
    then: [{ kind: halt, reason: "Retrain cycle complete" }]
```

### Nightly Retrain Cron Trigger (AC-9.1)

The retrain sub-graph runs nightly via a cron trigger, matching CVE-rem's triggered graph pattern. `serve_sdw.py` registers the retrain schedule using APScheduler (already available via `harbor serve` internals). Alternatively, an external cron job can `curl -X POST` the run endpoint.

```python
# In serve_sdw.py — register nightly retrain schedule
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()
scheduler.add_job(
    _trigger_retrain,
    "cron",
    hour=2, minute=0,  # 02:00 UTC nightly
    id="sdw-nightly-retrain",
)

async def _trigger_retrain():
    """POST to /v1/runs to spawn retrain sub-graph."""
    async with httpx.AsyncClient() as client:
        await client.post(
            "http://localhost:9001/v1/runs",
            json={"graph_id": "graph:sdw-retrain", "state": {}},
        )
```

Fallback: `just retrain-cron` installs a system crontab entry:
```just
retrain-cron:                         # AC-9.1: install nightly retrain cron
    (crontab -l 2>/dev/null; echo "0 2 * * * curl -sX POST http://localhost:9001/v1/runs -H 'Content-Type: application/json' -d '{\"graph_id\": \"graph:sdw-retrain\", \"state\": {}}'") | crontab -
```

### State Model (`graph/state.py`)

```python
from __future__ import annotations
from datetime import datetime
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


# --- Sub-models (carried as VALUES under flat top-level attrs) ---

class Detection(BaseModel):
    """Single vessel detection from YOLO inference."""
    detection_id: str = ""
    tile_id: str = ""
    geo_lat: float = 0.0
    geo_lon: float = 0.0
    confidence: float = 0.0
    obb_corners: list[list[float]] = Field(default_factory=list)
    vessel_length_m: float = 0.0
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
    """Metadata for a single SAR tile."""
    tile_id: str = ""
    scene_id: str = ""
    file_path: str = ""
    timestamp: str = ""
    bounds_wkt: str = ""
    patch_count: int = 0


class RunMetrics(BaseModel):
    """Per-run pipeline metrics."""
    tiles_processed: int = 0
    total_detections: int = 0
    dark_vessels_flagged: int = 0
    ais_matched: int = 0
    false_positives_rejected: int = 0
    avg_confidence: float = 0.0
    processing_time_seconds: float = 0.0
    model_version: str = ""


class ModelMetrics(BaseModel):
    """Model evaluation metrics for champion/challenger."""
    version: str = ""
    map50: float = 0.0
    map50_95: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    training_samples: int = 0
    holdout_samples: int = 0
    trained_at: str = ""


# --- Top-level state (flat attrs — Harbor field-merge keys on these) ---

class SdwState(BaseModel):
    """Main pipeline state. Flat top-level; sub-models as values."""
    # Identity
    run_id: str = ""
    run_started_at: str = ""

    # Phase 1: Ingest
    current_tile: TileMetadata = Field(default_factory=TileMetadata)
    tile_queue: list[str] = Field(default_factory=list)  # remaining tile IDs
    tiles_failed: int = 0
    failure_threshold: int = 5  # halt after N tile failures

    # Phase 1: Detection
    raw_detections: list[Detection] = Field(default_factory=list)
    detections: list[Detection] = Field(default_factory=list)  # post-NMS, post-land-mask
    detection_count: int = 0

    # Phase 2: Correlation
    ais_query_bbox: str = ""  # WKT for AIS position lookup
    ais_query_time_window_min: int = 30
    ais_match_radius_m: int = 500

    # Phase 3: Enrichment + Scoring
    has_low_confidence_detections: bool = False
    low_conf_threshold: float = 0.4
    low_conf_count: int = 0

    # AC-6.4: Configurable risk scoring weights (overridable via initial state or env vars)
    risk_weight_dark_vessel: int = 40
    risk_weight_sensitive_eez: int = 20
    risk_weight_far_from_port: int = 10
    risk_weight_large_vessel: int = 10
    risk_weight_confidence_max: int = 20

    # Phase 4: HITL
    current_tile_id: str = ""
    analyst_corrections: list[dict[str, Any]] = Field(default_factory=list)
    response_decision: str = ""  # approve / reject

    # Phase 5: Metrics
    run_metrics: RunMetrics = Field(default_factory=RunMetrics)
    model_version: str = "v1.0"

    # Error tracking
    last_error: str = ""
    pipeline_phase: str = "ingest"


class RetrainState(BaseModel):
    """Retrain sub-graph state."""
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
```

## Node Specifications

### SARIngestNode

| Field | Value |
|-------|-------|
| **Class** | `SARIngestNode(NodeBase)` |
| **Reads** | `state.tile_queue`, `state.tiles_failed`, `state.failure_threshold` |
| **Writes** | `current_tile`, `tile_queue`, `current_tile_id`, `pipeline_phase` |
| **External deps** | Postgres (tile metadata table), filesystem (tile paths) |
| **Error handling** | If tile file missing, increment `tiles_failed`; if `tiles_failed >= failure_threshold`, set `last_error` and halt |

Pops next tile ID from `tile_queue`, reads metadata from Postgres `sar_tiles` table, validates file exists on disk, populates `current_tile` sub-model.

### YOLOInferenceNode

| Field | Value |
|-------|-------|
| **Class** | `YOLOInferenceNode(NodeBase)` |
| **Reads** | `state.current_tile`, `state.model_version` |
| **Writes** | `raw_detections`, `pipeline_phase` |
| **External deps** | ONNX model via ModelRegistry, `onnxruntime` |
| **Error handling** | ModelRegistry hash mismatch raises `IncompatibleModelHashError`; tile read failure skips tile |

**Why custom node, not MLNode**: MLNode's `_predict()` returns raw output. YOLO OBB requires: (1) tile into 640x640 patches, (2) per-patch ONNX inference, (3) OBB post-processing (decode rotated boxes), (4) coordinate transform from patch-local pixels to geo-coords using tile affine transform from `rasterio`. This pipeline is YOLO-specific and doesn't fit MLNode's `input_field -> predict -> output_field` contract.

```python
async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
    tile = state.current_tile
    # 1. Load GeoTIFF via rasterio, extract affine transform
    # 2. Tile into 640x640 patches with 10% overlap
    # 3. Two-step ONNX session acquisition:
    #    entry = await registry.load_alias("sdw-detector", "production")  # -> ModelEntry
    #    session = loaders.get_onnx_session(model_id=entry.model_id, version=entry.version, file_uri=entry.file_uri)
    # 4. Run inference per patch (offloaded to thread via asyncio.to_thread)
    # 5. Decode OBB outputs: [x_center, y_center, w, h, angle, conf, class]
    # 6. Transform patch-local pixel coords to geo-coords via affine
    # 7. Build Detection objects with geo_lat, geo_lon, obb_corners, confidence
    return {"raw_detections": detections, "pipeline_phase": "detection"}
```

### NMSDeduplicationNode

| Field | Value |
|-------|-------|
| **Class** | `NMSDeduplicationNode(NodeBase)` |
| **Reads** | `state.raw_detections` |
| **Writes** | `detections` (partial — pre-land-mask) |
| **External deps** | None (pure geometry) |
| **Error handling** | Empty detections list passes through unchanged |

Cross-tile NMS using rotated IoU. Configurable IoU threshold (default 0.5). Operates on geo-coordinates, not pixel coordinates, so overlapping tile boundary detections are correctly deduplicated.

### LandMaskFilterNode

| Field | Value |
|-------|-------|
| **Class** | `LandMaskFilterNode(NodeBase)` |
| **Reads** | `state.detections` (from NMS) |
| **Writes** | `detections`, `detection_count` |
| **External deps** | Pre-loaded coastline polygons from PostGIS `coastlines` table |
| **Error handling** | If PostGIS query fails, log warning and skip filter (do not discard detections) |

PostGIS `ST_Contains` query: for each detection centroid, check if point falls within any land polygon. Remove matches. Uses connection pool from bootstrap-provisioned PostGIS.

### AISCorrelationNode

| Field | Value |
|-------|-------|
| **Class** | `AISCorrelationNode(NodeBase)` |
| **Reads** | `state.detections`, `state.current_tile`, `state.ais_match_radius_m`, `state.ais_query_time_window_min` |
| **Writes** | `detections` (enriched with AIS fields) |
| **External deps** | Postgres `ais_positions` table via Nautilus `BrokerNode` |
| **Error handling** | If broker request fails, mark all detections as `dark_vessel=True` (conservative) |

**Predicted-position matching** (AC-4.3): For each AIS report within the time window, compute expected position at SAR acquisition time: `predicted_lat = lat + speed_kn * cos(heading) * time_delta_hours / 60`, `predicted_lon = lon + speed_kn * sin(heading) * time_delta_hours / 60`. Then apply spatial radius to predicted position.

The node queries Nautilus Postgres adapter via `BrokerNode` with intent: `"AIS positions within bbox={tile_bounds} time_window=±{minutes}m"`. Returns list of `(mmsi, lat, lon, speed, heading, ts, ship_name, flag_state, vessel_type)`. Matches are joined to detections by minimum predicted-position distance. Unmatched detections get `dark_vessel=True`.

### GeoContextNode (NodeBase, uses DSPy internally)

| Field | Value |
|-------|-------|
| **Class** | `GeoContextNode(NodeBase)` — custom `execute()` does PostGIS queries first, then calls a DSPy `ChainOfThought` module internally |
| **Reads** | `state.detections` |
| **Writes** | `detections` (enriched with `eez_name`, `distance_to_port_nm`, `distance_to_coast_nm`, `fishing_zone`, `geo_summary`) |
| **External deps** | PostGIS (`eez_boundaries`, `ports`, `coastlines`), LLM via DSPy |
| **Error handling** | If LLM unavailable, populate structured fields from PostGIS only; `geo_summary` = templated fallback |

**Why NodeBase, not DSPyNode**: Harbor's `DSPyNode` only projects state fields to DSPy signature inputs and projects outputs back — it cannot interleave deterministic PostGIS queries before the DSPy call. `GeoContextNode` needs a custom `execute()` that runs PostGIS first, then calls the DSPy module.

Two-step process within `execute()`:
1. **PostGIS queries** (deterministic): `ST_Contains` for EEZ, `ST_Distance` for port/coast distances. Populates structured geo fields on each detection.
2. **LLM synthesis** (DSPy): Instantiates a `ChainOfThought` module internally. Takes structured geo fields + detection metadata, produces 2-3 sentence situational summary per AC-5.4.

DSPy signature:
```python
class GeoContextSignature(dspy.Signature):
    """Synthesize a situational summary for a maritime vessel detection."""
    detection_lat: float = dspy.InputField()
    detection_lon: float = dspy.InputField()
    dark_vessel: bool = dspy.InputField()
    eez_name: str = dspy.InputField()
    distance_to_port_nm: float = dspy.InputField()
    nearest_port_name: str = dspy.InputField()
    distance_to_coast_nm: float = dspy.InputField()
    ais_status: str = dspy.InputField()
    geo_summary: str = dspy.OutputField(desc="2-3 sentence maritime intel summary")
```

### RiskScoringNode

| Field | Value |
|-------|-------|
| **Class** | `RiskScoringNode(NodeBase)` |
| **Reads** | `state.detections`, `state.low_conf_threshold` |
| **Writes** | `detections` (with `risk_score`, `risk_level`), `has_low_confidence_detections`, `low_conf_count` |
| **External deps** | None (pure computation) |
| **Error handling** | N/A — deterministic |

**Scoring formula** (AC-6.1, AC-6.4 — weights configurable via state fields):

```python
score = 0
if det.dark_vessel:                  score += state.risk_weight_dark_vessel
if det.eez_name in SENSITIVE_EEZS:   score += state.risk_weight_sensitive_eez
if det.distance_to_port_nm > 50:     score += state.risk_weight_far_from_port
if det.vessel_length_m > 100:        score += state.risk_weight_large_vessel
score += int(det.confidence * state.risk_weight_confidence_max)
score = min(score, 100)
```

`SENSITIVE_EEZS` = `{"Iranian", "North Korean", "Syrian", "Venezuelan"}` — configurable via env.

Weights default to `40/20/10/10/20` in `SdwState`. Override by passing custom values in initial state (`POST /v1/runs state={"risk_weight_dark_vessel": 50, ...}`) or via env vars (`RISK_WEIGHT_DARK_VESSEL=50` read by bootstrap into default state).

Risk levels per AC-6.2: Critical 80-100, High 60-79, Medium 40-59, Low 0-39.

Also sets `has_low_confidence_detections = any(d.confidence < low_conf_threshold for d in detections)` for FR-17 active learning routing.

### ReportingNode (NodeBase, uses DSPy internally)

| Field | Value |
|-------|-------|
| **Class** | `ReportingNode(NodeBase)` — custom `execute()` formats structured report sections, then calls DSPy `ChainOfThought` for narrative synthesis |
| **Reads** | `state.detections` (each with all enrichment fields) |
| **Writes** | `detections` (with `report_text` populated) |
| **External deps** | LLM via DSPy |
| **Error handling** | LLM failure → templated fallback report with structured fields only |

**Why NodeBase, not DSPyNode**: Report output requires structured section formatting (Detection Summary, Imagery Reference, AIS Correlation Result, Geo-Context, Risk Assessment, Recommended Actions) that the node assembles deterministically before calling DSPy for narrative prose. `DSPyNode`'s state-field projection can't handle this multi-step formatting.

Generates per-detection intel report per AC-7.1. `execute()` assembles structured section data from detection fields, then calls a `ChainOfThought` module internally for narrative synthesis. Fallback: templated report with structured fields only (no LLM).

### EmitSARChipsNode (WriteArtifactNode wrapper)

| Field | Value |
|-------|-------|
| **Class** | `EmitSARChipsNode(NodeBase)` |
| **Reads** | `state.detections`, `state.current_tile` |
| **Writes** | `detections` (with `chip_artifact_ref` populated) |
| **External deps** | Filesystem via `FilesystemArtifactStore`, `rasterio` |
| **Error handling** | If chip extraction fails for one detection, log and continue |

For each detection, crop a 128x128 pixel region around the detection centroid from the source GeoTIFF, save as PNG via `rasterio`/`Pillow`, persist via `FilesystemArtifactStore.put()`, store `ArtifactRef` path in `chip_artifact_ref`.

### AnalystReviewNode (InterruptNode)

| Field | Value |
|-------|-------|
| **Class** | `AnalystReviewNode(NodeBase)` wrapping `InterruptNode` behavior |
| **Reads** | `state.detections`, `state.current_tile_id` |
| **Writes** | `analyst_corrections`, `response_decision` |
| **External deps** | None (HITL wait) |

Raises `_HitInterrupt` with a prompt listing detection count and tile ID. Run enters `awaiting-input` state. Streamlit UI calls `POST /v1/runs/{id}/respond` with analyst decisions per detection. On resume, `analyst_corrections` populated with `[{detection_id, decision, override_risk_level}]`, and corrections written to Postgres `corrections` table.

### MetricsCollectorNode

| Field | Value |
|-------|-------|
| **Class** | `MetricsCollectorNode(NodeBase)` |
| **Reads** | `state.detections`, `state.analyst_corrections`, `state.run_started_at`, `state.model_version` |
| **Writes** | `run_metrics` |
| **External deps** | Postgres `run_metrics` table |

Computes `RunMetrics` from current state: counts detections, dark vessels, AIS matches, false positives (from corrections), processing time. Writes to Postgres for dashboard queries.

### RetrainTriggerNode

| Field | Value |
|-------|-------|
| **Class** | `RetrainTriggerNode(NodeBase)` |
| **Reads** | `state.analyst_corrections` |
| **Writes** | (none — signals only) |

Checks if enough corrections accumulated for retrain (threshold: 10+). If so, and if triggered manually or by cron, dispatches the retrain sub-graph via `SubGraphNode`. Otherwise, proceeds to `action_done`.

### Retrain Sub-Graph Nodes

**RetrainCollectNode**: Queries `corrections` table from Postgres. Merges with original xView3 training labels. Writes merged dataset to `data/retrain/` directory.

**RetrainTrainNode**: Shells out to `scripts/train_detector.py --data data/retrain/ --epochs 10`. Captures new model path and mAP metrics. Registers new version in ModelRegistry.

**ChampionChallengerNode**: Loads champion (`production` alias) and challenger (just-trained) from ModelRegistry. Runs both on holdout set. Sets `challenger_wins = challenger_map50 > champion_map50`. If wins, calls `registry.alias(model_id="sdw-detector", alias="production", version=new_version)`.

**RetrainMetricsNode**: Writes `ModelMetrics` to Postgres `model_metrics` table. Includes both champion and challenger scores for dashboard comparison.

## Nautilus Configuration

```yaml
# nautilus.yaml
sources:
  - id: ais_buffer
    type: postgres
    description: "AIS position buffer (populated by ais_ingest daemon)"
    classification: unclassified
    data_types: [ais-position, vessel-identity]
    allowed_purposes: [surveillance, correlation]
    connection: "${POSTGRES_DSN}"
    table: ais_positions

  - id: geo_data
    type: postgres
    description: "Pre-loaded geographic data (EEZ, ports, coastlines)"
    classification: unclassified
    data_types: [eez-boundary, port-location, coastline]
    allowed_purposes: [enrichment, filtering]
    connection: "${POSTGRES_DSN}"
    table: eez_boundaries

  - id: detection_store
    type: postgres
    description: "Detection results and run metrics"
    classification: unclassified
    data_types: [detection, metrics, correction]
    allowed_purposes: [storage, analysis, retrain]
    connection: "${POSTGRES_DSN}"
    table: detections

agents:
  sdw-pipeline:
    id: sdw-pipeline
    clearance: unclassified
    compartments: []
    default_purpose: surveillance

rules:
  user_rules_dirs: []

analysis:
```

## Database Schema

PostGIS tables provisioned by `bootstrap.py`:

```sql
-- Extension
CREATE EXTENSION IF NOT EXISTS postgis;

-- SAR tile metadata
CREATE TABLE IF NOT EXISTS sar_tiles (
    tile_id       TEXT PRIMARY KEY,
    scene_id      TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    acquired_at   TIMESTAMPTZ NOT NULL,
    bounds        GEOMETRY(POLYGON, 4326) NOT NULL,
    patch_count   INTEGER DEFAULT 0,
    ingested_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_sar_tiles_bounds ON sar_tiles USING GIST(bounds);

-- AIS position buffer (fed by ais_ingest daemon)
CREATE TABLE IF NOT EXISTS ais_positions (
    id            BIGSERIAL PRIMARY KEY,
    mmsi          TEXT NOT NULL,
    lat           DOUBLE PRECISION NOT NULL,
    lon           DOUBLE PRECISION NOT NULL,
    speed_kn      DOUBLE PRECISION,
    heading       DOUBLE PRECISION,
    ts            TIMESTAMPTZ NOT NULL,
    ship_name     TEXT,
    flag_state    TEXT,
    vessel_type   TEXT,
    geom          GEOMETRY(POINT, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED
);
CREATE INDEX idx_ais_positions_geom ON ais_positions USING GIST(geom);
CREATE INDEX idx_ais_positions_ts ON ais_positions(ts);

-- EEZ boundaries (loaded from Marine Regions shapefile at bootstrap)
CREATE TABLE IF NOT EXISTS eez_boundaries (
    gid           SERIAL PRIMARY KEY,
    mrgid         INTEGER,
    geoname       TEXT NOT NULL,
    sovereign     TEXT,
    geom          GEOMETRY(MULTIPOLYGON, 4326) NOT NULL
);
CREATE INDEX idx_eez_geom ON eez_boundaries USING GIST(geom);

-- Port locations (loaded from World Port Index at bootstrap)
CREATE TABLE IF NOT EXISTS ports (
    id            SERIAL PRIMARY KEY,
    port_name     TEXT NOT NULL,
    country       TEXT,
    lat           DOUBLE PRECISION NOT NULL,
    lon           DOUBLE PRECISION NOT NULL,
    geom          GEOMETRY(POINT, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED
);
CREATE INDEX idx_ports_geom ON ports USING GIST(geom);

-- Coastline polygons (loaded from Natural Earth at bootstrap)
CREATE TABLE IF NOT EXISTS coastlines (
    gid           SERIAL PRIMARY KEY,
    geom          GEOMETRY(MULTIPOLYGON, 4326) NOT NULL
);
CREATE INDEX idx_coastlines_geom ON coastlines USING GIST(geom);

-- Detection results
CREATE TABLE IF NOT EXISTS detections (
    detection_id  TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL,
    tile_id       TEXT NOT NULL,
    geo_lat       DOUBLE PRECISION NOT NULL,
    geo_lon       DOUBLE PRECISION NOT NULL,
    confidence    DOUBLE PRECISION NOT NULL,
    dark_vessel   BOOLEAN DEFAULT FALSE,
    ais_mmsi      TEXT,
    risk_score    INTEGER DEFAULT 0,
    risk_level    TEXT DEFAULT 'low',
    analyst_decision TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    geom          GEOMETRY(POINT, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(geo_lon, geo_lat), 4326)) STORED
);
CREATE INDEX idx_detections_geom ON detections USING GIST(geom);

-- Analyst corrections (consumed by retrain)
CREATE TABLE IF NOT EXISTS corrections (
    id            SERIAL PRIMARY KEY,
    detection_id  TEXT REFERENCES detections(detection_id),
    run_id        TEXT NOT NULL,
    decision      TEXT NOT NULL,  -- confirm, reject, flag
    override_risk TEXT,
    corrected_at  TIMESTAMPTZ DEFAULT NOW(),
    consumed      BOOLEAN DEFAULT FALSE  -- set true after retrain consumes
);

-- Run-level metrics
CREATE TABLE IF NOT EXISTS run_metrics (
    id                SERIAL PRIMARY KEY,
    run_id            TEXT NOT NULL,
    tiles_processed   INTEGER DEFAULT 0,
    total_detections  INTEGER DEFAULT 0,
    dark_vessels      INTEGER DEFAULT 0,
    ais_matched       INTEGER DEFAULT 0,
    false_positives   INTEGER DEFAULT 0,
    processing_secs   DOUBLE PRECISION DEFAULT 0,
    model_version     TEXT,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

-- Model evaluation metrics (per retrain cycle)
CREATE TABLE IF NOT EXISTS model_metrics (
    id                SERIAL PRIMARY KEY,
    version           TEXT NOT NULL,
    map50             DOUBLE PRECISION,
    map50_95          DOUBLE PRECISION,
    precision_val     DOUBLE PRECISION,
    recall_val        DOUBLE PRECISION,
    training_samples  INTEGER,
    holdout_samples   INTEGER,
    promoted          BOOLEAN DEFAULT FALSE,
    trained_at        TIMESTAMPTZ DEFAULT NOW()
);
```

## Docker Compose Design

```yaml
# docker-compose.yml
name: sdw-demo

services:
  postgis:
    image: postgis/postgis:16-3.4
    container_name: sdw-postgis
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-harbor}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-harbor}
      POSTGRES_DB: ${POSTGRES_DB:-sdw}
    ports:
      - "${POSTGRES_PORT:-5441}:5432"
    volumes:
      - postgis-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7-alpine
    container_name: sdw-redis
    ports:
      - "${REDIS_PORT:-6391}:6379"
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  llm-shim:
    build:
      context: ./mocks/llm-shim
      dockerfile: Dockerfile
    container_name: sdw-llm
    ports:
      - "${LLM_PORT:-41001}:41001"
    environment:
      LLM_MODEL_NAME: ${LLM_MODEL_NAME:-sdw-shim}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:41001/health"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  postgis-data:
  redis-data:
```

Three services only. No pgvector (v2), no Neo4j (v2). PostGIS replaces plain postgres for spatial queries. llm-shim provides domain-realistic canned responses for offline mode.

## AIS Ingest Daemon

`ais_ingest.py`: Standalone asyncio daemon.

```python
# Pseudocode — key logic
async def main():
    mode = os.environ.get("AIS_MODE", "live")  # live | mock
    if mode == "mock":
        await load_fixture_ais()
        return

    api_key = os.environ["AIS_STREAM_API_KEY"]
    bbox = json.loads(os.environ.get("AIS_BBOX", '[[24.0, 56.0], [27.0, 57.0]]'))

    async with websockets.connect("wss://stream.aisstream.io/v0/stream") as ws:
        await ws.send(json.dumps({
            "Apikey": api_key,
            "BoundingBoxes": [bbox],
            "FilterMessageTypes": ["PositionReport"]
        }))
        pool = await asyncpg.create_pool(os.environ["POSTGRES_DSN"])
        async for msg in ws:
            data = json.loads(msg)
            meta = data.get("MetaData", {})
            pos = data.get("Message", {}).get("PositionReport", {})
            await pool.execute("""
                INSERT INTO ais_positions (mmsi, lat, lon, speed_kn, heading, ts, ship_name)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            """, meta["MMSI"], pos["Latitude"], pos["Longitude"],
                pos.get("Sog", 0), pos.get("TrueHeading", 0),
                datetime.utcnow(), meta.get("ShipName", ""))
```

**Mock fallback**: `load_fixture_ais()` reads `fixtures/ais_positions.json` and bulk-inserts into the same table. Fixture contains ~100 positions in the Strait of Hormuz AOI with realistic MMSI/names/speeds.

## Streamlit UI Design

`ui/app.py` — single Streamlit app with tab-based navigation.

**Tab 1: Live Map** (AC-8.1)
- Folium map centered on Strait of Hormuz
- SAR tile footprint (blue polygon)
- Detection markers: red (Critical), orange (High), yellow (Medium), green (Low)
- AIS tracks as polylines (blue for matched, gray for unmatched)
- Click marker → popup with detection detail

**Tab 2: Detection Review** (AC-8.2, AC-8.3)
- Table sorted by risk score descending (AC-8.6)
- For each detection: SAR chip image, confidence bar, OBB overlay, AIS status, geo-summary, risk badge, draft report
- Action buttons: Confirm Vessel / Reject (FP) / Flag for Review / Override Risk
- Submit → `POST /v1/runs/{run_id}/respond` with corrections payload

**Tab 3: Metrics Dashboard** (AC-10.1, AC-10.2)
- Plotly charts: mAP over model versions (line), dark vessel count per run (bar), tiles/hour (gauge), FP rate trend (line)
- Before/after comparison card when retrained model exists
- Data sourced from `run_metrics` and `model_metrics` Postgres tables

**Tab 4: Pipeline Status** (AC-11.2)
- WebSocket connection to `GET /v1/runs/{id}/stream`
- Shows current node, time per node, progress bar
- JSONL audit log viewer

**Integration**:
```python
import streamlit as st
import requests

HARBOR_URL = os.environ.get("HARBOR_URL", "http://localhost:9001")

# Trigger new pipeline run
def trigger_run(tile_ids: list[str]):
    resp = requests.post(f"{HARBOR_URL}/v1/runs", json={
        "graph_id": "graph:sdw-pipeline",
        "state": {"tile_queue": tile_ids}
    })
    return resp.json()["run_id"]

# Submit analyst review
def submit_review(run_id: str, corrections: list[dict]):
    requests.post(f"{HARBOR_URL}/v1/runs/{run_id}/respond", json={
        "decision": "approve",
        "corrections": corrections
    })
```

## LLM Mock Server

`mocks/llm-shim/`: OpenAI-compatible HTTP server returning domain-realistic canned responses.

**Dockerfile**: Lightweight FastAPI app.

**Endpoint**: `POST /v1/chat/completions` — standard OpenAI chat API shape.

**Response routing**: Match on system prompt keywords.
- `"geo_context"` or `"situational summary"` → returns maritime geo-context template:
  > "Vessel detected 14nm inside Iranian EEZ, 52nm from Bandar Abbas port. Located outside established IOTC fishing zones in the Strait of Hormuz traffic separation scheme. No AIS correlation detected — assessed as potential dark vessel operating in a high-sensitivity maritime zone. Recommend priority ISR tasking."
- `"intel report"` or `"reporting"` → returns structured intel report template with detection summary, imagery reference, AIS correlation, geo-context, risk assessment, recommended actions sections.
- Default → returns generic acknowledgment.

**Health endpoint**: `GET /health` → `{"status": "ok"}`.

## Scripts

### `scripts/prepare_dataset.py`

**Inputs**: xView3 scene directory (GeoTIFF + CSV labels), output directory
**Outputs**: `data/tiles/` (640x640 PNG patches), `data/labels/` (YOLO OBB `.txt` files)

Key steps:
1. Load scene GeoTIFF via `rasterio`
2. Tile into 640x640 patches with 10% stride overlap
3. For each xView3 CSV label within patch bounds:
   - Convert point annotation to OBB: synthesize rotated rectangle from `vessel_length_m` and assumed 1:4 width-to-length ratio, oriented along predominant SAR return axis (estimated from local gradient)
   - Normalize OBB corners to [0,1] patch coordinates
   - Write YOLO OBB format: `class x1 y1 x2 y2 x3 y3 x4 y4`
4. Generate `data.yaml` config file for Ultralytics:
   ```yaml
   train: data/tiles/train
   val: data/tiles/val
   names:
     0: vessel
   ```

### `scripts/train_detector.py`

**Inputs**: `--data data/data.yaml`, `--epochs 50`, `--model yolo11s-obb.pt`
**Outputs**: `runs/detect/train/weights/best.pt` → exported `best.onnx`

Key steps:
1. `from ultralytics import YOLO`
2. `model = YOLO("yolo11s-obb.pt")` (pre-trained on DOTAv1)
3. `model.train(data="data/data.yaml", epochs=epochs, imgsz=640, batch=16)`
4. `model.export(format="onnx")` → `best.onnx`
5. Compute SHA-256 of `best.onnx`
6. Register via ModelRegistry:
   ```python
   registry = ModelRegistry("data/model_registry.db")
   await registry.bootstrap()
   await registry.register(
       model_id="sdw-detector", version=version,
       runtime="onnx", file_uri=f"file://{onnx_path}",
       content_hash=sha256_hex, framework="ultralytics-yolo11"
   )
   await registry.alias(model_id="sdw-detector", alias="production", version=version)
   ```

## File Structure

```
demos/sentinel_dark_watch/
├── __init__.py
├── .env.example
├── .env                          # gitignored
├── docker-compose.yml
├── bootstrap.py                  # idempotent provisioning
├── nautilus.yaml                 # Nautilus broker config
├── capabilities.py               # engine-side capability profile
├── serve_sdw.py                  # harbor serve wrapper
├── ais_ingest.py                 # AIS WebSocket daemon
├── graph/
│   ├── __init__.py
│   ├── harbor.yaml               # main pipeline IR
│   ├── retrain.yaml              # retrain sub-graph IR
│   ├── state.py                  # SdwState, RetrainState
│   └── nodes.py                  # all node implementations
├── fixtures/
│   ├── ais_positions.json        # mock AIS data (Strait of Hormuz)
│   ├── llm_responses.json        # canned LLM responses for shim
│   └── test_tile.tif             # single small GeoTIFF for CI
├── mocks/
│   └── llm-shim/
│       ├── Dockerfile
│       ├── server.py
│       └── responses.py
├── scripts/
│   ├── prepare_dataset.py        # xView3 → YOLO OBB format
│   └── train_detector.py         # fine-tune + export + register
├── ui/
│   └── app.py                    # Streamlit analyst dashboard
├── data/                         # gitignored except LFS-tracked test tiles
│   ├── tiles/                    # prepared training tiles
│   ├── labels/                   # YOLO OBB annotations
│   ├── data.yaml                 # Ultralytics dataset config
│   ├── model_registry.db         # SQLite model registry
│   └── geo/                      # EEZ shapefile, port CSV, coastline
├── Justfile                      # task runner
└── tests/
    ├── test_nodes.py             # unit tests for all nodes
    ├── test_pipeline.py          # integration test (full graph run)
    └── conftest.py               # fixtures
```

## Justfile / Makefile

```just
# Justfile
set dotenv-load

# --- Primary commands ---
demo:                             # US-1: one-command demo launch
    docker compose -f demos/sentinel_dark_watch/docker-compose.yml up -d
    uv run --no-project python -m demos.sentinel_dark_watch.bootstrap
    uv run --no-project python -m demos.sentinel_dark_watch.ais_ingest &
    uv run --no-project python -m demos.sentinel_dark_watch.serve_sdw --port 9001 &
    sleep 3
    uv run --no-project streamlit run demos/sentinel_dark_watch/ui/app.py --server.port 8501

demo-offline:                     # US-1: no-network mode
    AIS_MODE=mock LLM_BASE_URL=http://localhost:41001/v1 just demo

teardown:                         # clean stop
    docker compose -f demos/sentinel_dark_watch/docker-compose.yml down -v
    pkill -f "serve_sdw" || true
    pkill -f "ais_ingest" || true
    pkill -f "streamlit" || true

# --- ML commands ---
prepare-data:                     # download + tile xView3 subset
    uv run --no-project python -m demos.sentinel_dark_watch.scripts.prepare_dataset

train:                            # fine-tune YOLO + register
    uv run --no-project python -m demos.sentinel_dark_watch.scripts.train_detector

retrain:                          # manual retrain trigger (AC-9.4)
    curl -X POST http://localhost:9001/v1/runs \
        -H "Content-Type: application/json" \
        -d '{"graph_id": "graph:sdw-retrain", "state": {}}'

# --- Dev commands ---
bootstrap:
    uv run --no-project python -m demos.sentinel_dark_watch.bootstrap

serve:
    uv run --no-project python -m demos.sentinel_dark_watch.serve_sdw --port 9001

ui:
    uv run --no-project streamlit run demos/sentinel_dark_watch/ui/app.py --server.port 8501

test:
    uv run --no-project pytest demos/sentinel_dark_watch/tests/ -v
```

## Error Handling

| Error Scenario | Handling Strategy | User Impact |
|----------------|-------------------|-------------|
| Tile file missing | Increment `tiles_failed`; skip tile; halt if `>= failure_threshold` | Log message; pipeline continues on remaining tiles |
| ONNX model hash mismatch | `IncompatibleModelHashError` raised by ModelRegistry | Run fails loud; operator must re-register model |
| AIS WebSocket disconnect | `ais_ingest.py` reconnects with exponential backoff (3 retries) | Brief gap in AIS data; correlation still works on buffered positions |
| AIS broker request fails | All detections marked `dark_vessel=True` (conservative) | Over-flagging; analyst corrects in HITL |
| PostGIS down | Land-mask/EEZ queries fail; skip filter, log warning | Detections may include land FPs; analyst corrects |
| LLM unavailable | DSPy nodes fall back to templated responses | `geo_summary` and `report_text` are formulaic but functional |
| Retrain produces worse model | Champion/challenger gate rejects; `production` alias unchanged | Dashboard shows challenger metrics but no promotion |
| Streamlit WebSocket drop | Auto-reconnect on next page interaction | Brief stale data; refreshes on reconnect |

## Edge Cases

- **Zero detections on tile**: NMS/LandMask/AIS nodes pass through empty lists. Metrics collector records `total_detections=0`. No HITL gate triggered (nothing to review).
- **All detections on land**: Land-mask removes all. Same as zero-detection path.
- **AIS buffer empty (daemon not running)**: All detections marked dark. Offline mode pre-loads fixture data.
- **Overlapping tile boundaries**: NMS deduplication handles via cross-tile rotated IoU.
- **Very large scenes (29400x24400)**: Tiled at prepare-dataset time. Only 640x640 patches enter inference.
- **xView3 point labels with no vessel_length_m**: Default to 20m OBB during label synthesis. Conservative but functional.

## Test Strategy

### Unit Tests
- **YOLOInferenceNode**: Mock ONNX session; verify Detection objects created with correct geo-coords from known tile affine.
- **NMSDeduplicationNode**: Two overlapping detections → one output. Non-overlapping → both preserved.
- **LandMaskFilterNode**: Detection on land (known lat/lon) removed. Detection at sea preserved.
- **AISCorrelationNode**: Known AIS position near detection → matched. No AIS → dark_vessel=True.
- **RiskScoringNode**: Dark vessel in sensitive EEZ → Critical. AIS-matched near port → Low.
- **GeoContextNode**: Mock DSPy module; verify PostGIS fields populated and summary generated.

### Integration Tests
- **Full pipeline (mock)**: Load single test tile, run graph with mock LLM + fixture AIS. Verify: detections produced, AIS correlation attempted, risk scores assigned, HITL gate hit.
- **Retrain cycle**: Pre-seed corrections table, trigger retrain sub-graph, verify new model registered in ModelRegistry.
- **Bootstrap idempotency**: Run bootstrap twice; second run is no-op (no SQL errors, no duplicate rows).

### E2E Tests
- **Demo narrative** (Playwright or manual): `make demo` → pipeline runs → map shows detections → analyst reviews → `make retrain` → metrics dashboard shows improvement.

## Technical Decisions

| Decision | Options Considered | Choice | Rationale |
|----------|-------------------|--------|-----------|
| YOLO inference node | `MLNode(runtime="onnx")` vs custom `YOLOInferenceNode(NodeBase)` | Custom node | MLNode's `predict()` can't handle tiling, OBB decode, or geo-coord transform. Custom node wraps ONNX session directly. |
| AIS integration | Direct WebSocket in graph vs buffer daemon | Buffer daemon + Postgres | Nautilus is request/response only. Buffer pattern matches Nautilus postgres adapter. Graph stays reactive. |
| Sub-graph for retrain | Single monolithic graph vs separate retrain sub-graph | Separate `retrain.yaml` | Retrain is independently triggerable (cron or manual). Different state model. SubGraphNode connects when triggered from main pipeline. |
| Spatial database | Plain Postgres + in-memory shapely vs PostGIS | PostGIS | `ST_Contains`, `ST_Distance`, GIST indexes needed for performant spatial queries on EEZ boundaries and AIS positions. |
| UI framework | Gradio vs Streamlit | Streamlit | Folium map integration via `streamlit-folium`. More control over layout. Decoupled from harbor serve (REST/WS). |
| Risk scoring | ML-based vs rule-based | Rule-based | Explainable for investors. No second model to train. Weights configurable. |
| Tile storage | S3 vs local filesystem + Git LFS | Local + Git LFS | Demo runs offline. Small test set (~2-3 tiles) fits in LFS. Full dataset downloaded by `prepare_dataset.py`. |
| Model registry DB | Shared Postgres vs SQLite | SQLite (Harbor default) | ModelRegistry is SQLite-backed by design. No reason to override for a demo. |
| Coastline data | GSHHG vs Natural Earth | Natural Earth | Smaller download, shapefile format, easily loaded by geopandas. Sufficient resolution for land-mask filtering. |

## Performance Considerations

- **ONNX inference**: Harbor's `get_onnx_session()` caches sessions by `(model_id, version)`. Thread-safe CPU EP. Expect <100ms/patch GPU, <2s/patch CPU.
- **Tiling**: 640x640 patches from a 29400x24400 scene yields ~2000 patches. At <2s/patch CPU = ~67 min. GPU cuts to ~3.5 min. Demo uses pre-tiled small scenes (10-20 patches each).
- **PostGIS queries**: GIST indexes on geometry columns. EEZ point-in-polygon: <10ms per detection. Port distance: <10ms.
- **AIS correlation**: Index on `ais_positions(ts)` + GIST on `geom`. Windowed query should be <50ms.

## Security Considerations

- **Capability profile**: `serve_sdw.py` pins capabilities. `tools:broker_request` required for Nautilus access.
- **No auth on Streamlit**: Demo only. Not exposed to network. Runs on localhost:8501.
- **AIS API key**: In `.env` (gitignored). `.env.example` has placeholder.
- **LLM API key**: In `.env`. Default `localhost:41001` requires no auth.
- **No TLS**: All services on localhost. Demo scope only.

## Existing Patterns to Follow

Based on CVE-rem codebase analysis:

1. **`state_class:` in harbor.yaml** → `"demos.sentinel_dark_watch.graph.state:SdwState"` (colon-separated module:class)
2. **Node `kind:` format** → `"demos.sentinel_dark_watch.graph.nodes:ClassName"` (Python import path)
3. **`bootstrap.py` pattern** → `_wait_tcp()` + `_wait_http_health()` for Docker health, then SQL DDL, then seed data. `_load_env()` from `.env` at module top.
4. **Run via uv** → `uv run --no-project python -m demos.sentinel_dark_watch.bootstrap`
5. **`serve_*.py` pattern** → Argparse wrapper that imports `harbor.serve.api.create_app`, pins capabilities, starts uvicorn.
6. **Fathom rules syntax** → `?n <- (node-id (id <node_id>)) (state (<field> <value>))` with `then: [{ kind: goto, target: <next> }]`
7. **HITL gates** → `kind: interrupt` with `timeout: null`, `on_timeout: "noop"`, followed by `branch_resp_*` passthrough for response routing
8. **`.env.example`** → All env vars with sensible defaults for Docker compose. DSN format: `postgresql://user:pass@localhost:port/db`

## Dependencies

New entries for `pyproject.toml` `[sdw]` extra:

```toml
[project.optional-dependencies]
sdw = [
    # ultralytics 8.3+ includes YOLO11 model architecture despite 8.x version numbering
    "ultralytics>=8.3",
    "torch>=2.0",
    "torchvision>=0.15",
    "rasterio>=1.3",
    "geopandas>=0.14",
    "shapely>=2.0",
    "streamlit>=1.30",
    "streamlit-folium>=0.20",
    "folium>=0.15",
    "websockets>=12.0",
    "Pillow>=10.0",
    "asyncpg>=0.29",
    "plotly>=5.18",
]
```

`onnxruntime` already in `[ml]` extra. `asyncpg` for AIS ingest daemon (direct Postgres writes, not through SQLAlchemy).

## Implementation Steps

1. Rename `demos/sentinel-dark-watch/` → `demos/sentinel_dark_watch/`. Create directory structure + `__init__.py` files.
2. Create `docker-compose.yml` (PostGIS + Redis + llm-shim). Create `.env.example`.
3. Create `graph/state.py` with `SdwState` and `RetrainState`.
4. Create `graph/harbor.yaml` (main pipeline IR) and `graph/retrain.yaml` (retrain sub-graph IR).
5. Create `bootstrap.py` — wait for Docker health, provision PostGIS schema (all tables), pre-load EEZ/port/coastline data, pre-stage fixture AIS data.
6. Create `graph/nodes.py` — implement all 12+ node classes. Start with PassthroughNode + SARIngestNode + YOLOInferenceNode.
7. Create `nautilus.yaml` — configure postgres source for AIS buffer + geo data + detections.
8. Create `ais_ingest.py` — AIS WebSocket daemon with mock fallback.
9. Create `mocks/llm-shim/` — OpenAI-compatible server with maritime canned responses.
10. Create `serve_sdw.py` — harbor serve wrapper with capability profile.
11. Create `capabilities.py` — SDW capability profile.
12. Create `scripts/prepare_dataset.py` — xView3 tiling + label conversion.
13. Create `scripts/train_detector.py` — YOLO fine-tune + ONNX export + ModelRegistry registration.
14. Create `ui/app.py` — Streamlit dashboard (map, review, metrics, status tabs).
15. Create `fixtures/` — mock AIS JSON, canned LLM responses, small test tile.
16. Create `Justfile` with demo/demo-offline/teardown/train/retrain/serve/ui commands.
17. Add `[sdw]` extra to monorepo `pyproject.toml`.
18. Create `tests/` — unit tests for nodes, integration test for pipeline, conftest fixtures.
