---
spec: demo-sentinel-dark-watch-1
phase: requirements
created: 2026-05-26
---

# Requirements: Sentinel Dark Watch

## Overview

Investor-grade demo of a self-improving maritime reconnaissance pipeline. Sentinel-1 SAR imagery flows through ML-based vessel detection, AIS correlation flags "dark" (transponder-off) vessels, LLM agents enrich with geo-context and draft intel reports, analysts review/correct via Streamlit UI, and the system retrains nightly on new labels. Built on Stargraph (graph engine) and Nautilus (data broker). One command (`make demo`) brings up the entire stack from cold start. The narrative: cold start with xView3 training data, detect dark vessels in the Strait of Hormuz, analyst corrects, model measurably improves, metrics dashboard proves it.

Primary audience: external stakeholders / investors. Polish, narrative arc, and visual impact matter.

## User Stories

### US-1: One-Command Demo Launch

**As an** investor watching a live demo
**I want** the entire pipeline to start from a single command
**So that** I see a polished, zero-friction experience.

**Acceptance Criteria:**
- [ ] AC-1.1: `make demo` (or `just run`) brings up Docker services, runs bootstrap, starts Stargraph serve, starts Streamlit UI, and opens browser — all from cold start
- [ ] AC-1.2: No manual env setup beyond copying `.env.example` to `.env` and adding API keys
- [ ] AC-1.3: Pipeline completes a full run (ingest → detect → correlate → enrich → report → HITL) within 5 minutes on a machine with GPU
- [ ] AC-1.4: `make demo-offline` works without network access (mock AIS, local LLM shim, pre-staged tiles)
- [ ] AC-1.5: `make teardown` cleanly removes all containers and volumes

### US-2: SAR Tile Ingestion

**As a** pipeline operator
**I want** Sentinel-1 SAR tiles loaded into the system
**So that** the detection pipeline has imagery to process.

**Acceptance Criteria:**
- [ ] AC-2.1: Bootstrap pre-stages 10-20 Sentinel-1 GRD scenes for the Strait of Hormuz AOI
- [ ] AC-2.2: Each scene is tiled into 640x640 patches with geo-referenced metadata preserved
- [ ] AC-2.3: Tiles stored on local filesystem with paths tracked in Postgres
- [ ] AC-2.4: A `SARIngestNode` reads tile paths from state and loads imagery for inference
- [ ] AC-2.5: Simulated "live" ingestion — tiles are fed sequentially with timestamps, creating a deterministic replay

### US-3: ML Vessel Detection

**As a** pipeline operator
**I want** an ML model to detect vessels in SAR tiles
**So that** vessel candidates are identified automatically.

**Acceptance Criteria:**
- [ ] AC-3.1: YOLO11-OBB model fine-tuned on xView3 subset (10-20 scenes, Strait of Hormuz region). If fewer than 10 scenes available for Strait of Hormuz, expand AOI to broader Persian Gulf / Arabian Sea region to reach minimum scene count.
- [ ] AC-3.2: Training script (`scripts/train_detector.py`) converts xView3 CSV labels to YOLO OBB format, tiles scenes, fine-tunes, exports to ONNX, registers in Stargraph ModelRegistry
- [ ] AC-3.3: `YOLOInferenceNode` runs ONNX inference on each tile, outputs detections with: geo-coords, confidence, OBB corners, vessel_length_estimate
- [ ] AC-3.4: NMS post-processing eliminates duplicate detections across overlapping tile boundaries
- [ ] AC-3.5: Land-mask filtering removes detections on land (using pre-loaded coastline data)
- [ ] AC-3.6: Model registered with SHA-256 hash and `production` alias in ModelRegistry

### US-4: AIS Correlation and Dark Vessel Flagging

**As an** analyst
**I want** SAR detections cross-referenced with AIS positions
**So that** vessels without transponders ("dark" vessels) are flagged.

**Acceptance Criteria:**
- [ ] AC-4.1: AIS ingest daemon (`ais_ingest.py`) connects to AISStream.io WebSocket, writes positions to Postgres `ais_positions` table (MMSI, lat, lon, speed, heading, timestamp, ship_name)
- [ ] AC-4.2: Mock fallback: when AIS WebSocket unavailable, loads fixture AIS data from JSON
- [ ] AC-4.3: `AISCorrelationNode` uses predicted-position matching: compute expected vessel position from last AIS report (position + speed × heading × time_delta), then apply spatial radius (configurable, default 500m) to the predicted position. Time window configurable, default ±30 min. This accounts for vessel movement (a vessel at 15 knots covers ~14km in 30 minutes).
- [ ] AC-4.4: Detections with no AIS match flagged as `dark_vessel=True`
- [ ] AC-4.5: Detections with AIS match enriched with vessel identity (MMSI, name, flag state, vessel type)
- [ ] AC-4.6: Nautilus `postgres` adapter used for AIS position queries (via BrokerNode)

### US-5: Geo-Context Enrichment

**As an** analyst
**I want** each detection enriched with geographic context
**So that** I can assess the vessel's situation at a glance.

**Acceptance Criteria:**
- [ ] AC-5.1: `GeoContextNode` (DSPyNode) determines: EEZ location, distance to nearest port, distance to nearest coastline, fishing zone status
- [ ] AC-5.2: EEZ boundaries pre-loaded from Marine Regions WFS at bootstrap into PostGIS
- [ ] AC-5.3: Port locations pre-loaded from World Port Index at bootstrap
- [ ] AC-5.4: LLM synthesizes a 2-3 sentence situational summary per detection. Summary must contain: EEZ name, distance to nearest port, and AIS correlation status. (e.g., "Vessel detected 12nm inside Iranian EEZ, 45nm from Bandar Abbas, outside established shipping lanes. No AIS correlation. Assessed high-risk.")
- [ ] AC-5.5: Offline mode uses LLM shim that returns plausible mock summaries

### US-6: Risk Scoring

**As an** analyst
**I want** each detection assigned a risk score
**So that** I can prioritize which vessels to review first.

**Acceptance Criteria:**
- [ ] AC-6.1: `RiskScoringNode` computes a 0-100 risk score based on: dark_vessel flag (heavy weight), EEZ sensitivity, proximity to shipping lanes, vessel size anomaly, detection confidence
- [ ] AC-6.2: Risk levels: Critical (80-100), High (60-79), Medium (40-59), Low (0-39)
- [ ] AC-6.3: Detections sorted by risk score in analyst queue
- [ ] AC-6.4: Scoring weights configurable via state or env vars

### US-7: Intel Report Generation

**As an** analyst
**I want** a draft intel report generated for each high-risk detection
**So that** I have a starting point for my assessment.

**Acceptance Criteria:**
- [ ] AC-7.1: `ReportingNode` (DSPyNode) generates an intel-style report per detection: detection summary, imagery chip reference, AIS correlation result, geo-context, risk assessment, recommended actions
- [ ] AC-7.2: Report includes a cropped SAR chip (imagery patch around detection) saved as artifact
- [ ] AC-7.3: Reports saved via `WriteArtifactNode` to filesystem artifact store
- [ ] AC-7.4: Reports are editable by analyst before final submission (in HITL UI)

### US-8: Analyst HITL Review

**As an** analyst
**I want** a Streamlit dashboard to review and correct detections
**So that** I can confirm true positives, reject false positives, and improve model training data.

**Acceptance Criteria:**
- [ ] AC-8.1: Streamlit app on port 8501 with map view (Folium) showing: SAR tile footprint, detection markers color-coded by risk level, AIS tracks
- [ ] AC-8.2: Detection detail panel shows: SAR chip, confidence, OBB overlay, AIS correlation status, geo-context summary, risk score, draft report
- [ ] AC-8.3: Action buttons: Confirm Vessel / Reject (False Positive) / Flag for Review / Override Risk Level
- [ ] AC-8.4: Analyst decisions submitted via `POST /v1/runs/{id}/respond` (InterruptNode resume)
- [ ] AC-8.5: Analyst-corrected labels written to a `corrections` table in Postgres for retrain consumption
- [ ] AC-8.6: Review queue prioritized by risk score (highest first)

### US-9: Nightly Auto-Retrain

**As a** pipeline operator
**I want** the model to retrain nightly on new analyst-corrected labels
**So that** detection accuracy improves over time.

**Acceptance Criteria:**
- [ ] AC-9.1: Cron-triggered sub-graph runs nightly retrain: collects corrections from Postgres, merges with original training set, fine-tunes YOLO, exports ONNX, registers as new version
- [ ] AC-9.2: Champion/challenger gate: new model evaluated on holdout set. Promoted to `production` alias only if mAP exceeds current champion
- [ ] AC-9.3: Retrain history logged (version, mAP, training samples count, timestamp)
- [ ] AC-9.4: For demo purposes: retrain can be triggered manually via `make retrain` to show improvement without waiting for cron

### US-10: Metrics Dashboard

**As an** investor watching the demo
**I want** a metrics dashboard showing pipeline performance over time
**So that** I can see the system actually improving.

**Acceptance Criteria:**
- [ ] AC-10.1: Streamlit page (or tab) showing: detection mAP over model versions, dark vessel catch rate per run, tiles processed per hour, average analyst review time, false positive rate trend
- [ ] AC-10.2: Before/after comparison: initial model vs. retrained model on same holdout set
- [ ] AC-10.3: Metrics stored in Postgres, queried for dashboard rendering
- [ ] AC-10.4: Charts render without error; axes labeled; data points correspond to stored metrics (Plotly or similar)

### US-11: Pipeline Observability

**As a** demo presenter
**I want** to see the pipeline executing in real time
**So that** I can narrate what's happening at each stage.

**Acceptance Criteria:**
- [ ] AC-11.1: Stargraph serve WebSocket streams node transitions in real time
- [ ] AC-11.2: Streamlit sidebar or dedicated panel shows current pipeline stage with progress indicator
- [ ] AC-11.3: Per-run JSONL audit log written (mirrors CVE-rem pattern)
- [ ] AC-11.4: Node execution durations visible for throughput narrative

## Functional Requirements

| ID | Requirement | Priority | Acceptance Criteria |
|----|-------------|----------|---------------------|
| FR-1 | Graph definition (`stargraph.yaml`) with nodes: SARIngest, YOLOInference, AISCorrelation, GeoContext, RiskScoring, Reporting, AnalystReview (InterruptNode), RetrainTrigger | High | Graph loads and validates via `stargraph verify-graph` |
| FR-2 | `SdwState` Pydantic model tracks: current_tile, detections list, ais_matches, enrichments, risk_scores, analyst_decisions, run_metrics | High | State serializes/deserializes through checkpointing round-trip |
| FR-3 | `YOLOInferenceNode` runs ONNX model from ModelRegistry, outputs OBB detections with geo-coords | High | Detections match expected count (±10%) on known test tile |
| FR-4 | AIS buffer daemon writes WebSocket messages to Postgres; falls back to fixture data when offline | High | `ais_positions` table populated within 30s of daemon start; fixture mode works without network |
| FR-5 | `AISCorrelationNode` joins detections with AIS positions by spatial/temporal proximity | High | Known AIS-matched vessels correlate; known dark vessels flagged |
| FR-6 | `GeoContextNode` (DSPyNode) enriches detections with EEZ, port distance, situational summary | High | Detections in Iranian EEZ correctly attributed; port distances within 5% of ground truth |
| FR-7 | `RiskScoringNode` computes 0-100 score; dark_vessel heavily weighted | Medium | Dark vessel in sensitive EEZ scores Critical; AIS-matched vessel near port scores Low |
| FR-8 | `ReportingNode` (DSPyNode) generates intel reports with imagery chips | Medium | Report contains all required sections; chip file exists on disk |
| FR-9 | InterruptNode pauses run for analyst review; resumes on `POST /respond` | High | Run pauses at review gate; resumes with analyst decision in state |
| FR-10 | Retrain sub-graph merges corrections with training data, fine-tunes YOLO, evaluates on holdout, conditionally promotes | Medium | New model registered; promoted only if mAP improves |
| FR-11 | `nautilus.yaml` configures sources: postgres (AIS buffer, detections), rest (Marine Regions WFS fallback) | High | Nautilus broker resolves queries to correct adapters |
| FR-12 | `bootstrap.py` provisions: Postgres schemas, pre-staged SAR tiles, EEZ/port geo-data, fixture AIS data | High | Idempotent; second run is a no-op |
| FR-13 | `docker-compose.yml` with postgis/postgis:16-3.4 (5441), redis:7 (6391), llm-shim (41001) | High | All services healthy within 30s of `docker compose up` |
| FR-14 | `serve_sdw.py` wraps `stargraph serve` on port 9001 with capability profile + JSONL audit | High | `POST /v1/runs` triggers pipeline; WebSocket streams events |
| FR-15 | `scripts/prepare_dataset.py` downloads xView3 subset, tiles scenes, converts labels to YOLO OBB format | High | Output: `data/tiles/` with images + `data/labels/` with YOLO OBB annotations |
| FR-16 | `scripts/train_detector.py` fine-tunes YOLO11-OBB, exports ONNX, registers model | High | `production` alias points to trained model in ModelRegistry |
| FR-17 | Fathom rules route: low-confidence detections to analyst queue (active learning); high-confidence + low-risk detections to auto-accept | High | Low-conf detection hits InterruptNode; high-conf/low-risk bypasses it |
| FR-18 | NMS post-processing deduplicates detections across overlapping tile boundaries (IoU threshold configurable) | High | Duplicate detections on tile boundaries reduced to single detection; no missed detections from over-aggressive NMS |
| FR-19 | Land-mask filtering removes on-land detections using pre-loaded coastline polygons (Natural Earth or GSHHG) | High | Zero detections with centroids on land; coastline data loaded at bootstrap |

## Non-Functional Requirements

| ID | Requirement | Metric | Target |
|----|-------------|--------|--------|
| NFR-1 | Full pipeline run (single tile, 50 detections) | Wall-clock time | < 60 seconds (GPU), < 5 min (CPU) |
| NFR-2 | YOLO inference per 640x640 patch | Latency | < 100ms (GPU), < 2s (CPU) |
| NFR-3 | Streamlit UI initial load | Time to interactive | < 3 seconds |
| NFR-4 | `make demo` cold start to first pipeline run | Total setup time | < 3 minutes (images cached) |
| NFR-5 | Offline mode (no network) | Functionality | Full pipeline runs with mock AIS + LLM shim + pre-staged tiles |
| NFR-6 | Deterministic replay | Reproducibility | Same tiles + same model version = identical detections |
| NFR-7 | Checkpoint recovery | Resilience | Pipeline resumes from last checkpoint after crash |
| NFR-8 | Demo visual polish | Presentation quality | No raw JSON objects rendered in UI; all data displayed via tables, charts, or formatted text. Map markers color-coded by risk level. |

## Glossary

- **SAR**: Synthetic Aperture Radar. Active radar imaging that works through clouds and at night. Sentinel-1 is ESA's SAR satellite constellation.
- **AIS**: Automatic Identification System. Transponder system on vessels that broadcasts identity, position, speed, heading. Dark vessels turn AIS off.
- **Dark vessel**: A vessel detected on SAR imagery that has no corresponding AIS transmission. Potentially engaged in illegal fishing, smuggling, or sanctions evasion.
- **OBB**: Oriented Bounding Box. A rotated rectangle that fits elongated objects (vessels) tighter than axis-aligned boxes.
- **EEZ**: Exclusive Economic Zone. Maritime zone extending 200 nautical miles from a nation's coastline where that nation has sovereign resource rights.
- **GRD**: Ground Range Detected. Sentinel-1 processing level with amplitude-only data projected to ground range.
- **xView3**: NeurIPS 2022 benchmark dataset for SAR-based maritime object detection. ~1,000 Sentinel-1 scenes with 243K labeled objects.
- **YOLO**: You Only Look Once. Real-time object detection architecture. YOLO11-OBB is the Ultralytics variant supporting oriented bounding boxes.
- **MMSI**: Maritime Mobile Service Identity. 9-digit number uniquely identifying a vessel's AIS transponder.
- **AOI**: Area of Interest. For this demo: Strait of Hormuz region.
- **Champion/challenger**: Model deployment pattern where a new model (challenger) must beat the current model (champion) on a holdout metric before promotion.
- **ModelRegistry**: Stargraph's model management component (SQLite-backed). Tracks versions, SHA-256 hashes, aliases.
- **InterruptNode**: Stargraph node type that pauses execution and waits for external input (analyst decision).
- **Fathom**: Stargraph's rule engine (CLIPS-style when/then rules). Used for routing logic.
- **mAP**: Mean Average Precision. Standard object detection accuracy metric.
- **Strait of Hormuz**: Narrow waterway between Iran and Oman. ~30% of seaborne oil passes through. High surveillance interest.

## Out of Scope

- Real-time Sentinel-1 acquisition scheduling or tasking
- Real-time Sentinel-1 scene discovery and download via STAC/Copernicus API (pre-staged tiles only)
- Production-grade security (auth, TLS, RBAC) — demo only
- Multi-region support (single AOI: Strait of Hormuz)
- Neo4j vessel knowledge graph (v2)
- Vector embedding search for vessel pattern matching (v2)
- Sentinel-2 optical confirmation imagery
- Global Fishing Watch API integration (pre-load geo data instead)
- Multi-user analyst workflow (single analyst assumed)
- Mobile or responsive UI (desktop Streamlit only)
- Agent eval loop / prompt auto-tuning via DSPy (v2 — focus retrain on ML model only)
- Regulatory compliance beyond disclaimers
- CI/CD pipeline for the demo itself
- Kubernetes / cloud deployment (local Docker only)

## Dependencies

| Dependency | Type | Status | Notes |
|------------|------|--------|-------|
| xView3-SAR dataset access | Data | User registered at iuu.xview.us | Need to download 10-20 Strait of Hormuz scenes |
| AISStream.io API key | Service | Provisioned in `.env` | Key: `933205...` |
| Local GPU (CUDA) | Hardware | Available | Required for training; inference can fall back to CPU |
| Ollama server | Service | Running at localhost:41001 | LLM for geo-context + reporting agents |
| Stargraph framework | Library | In monorepo | Graph engine, MLNode, ModelRegistry, InterruptNode, stores |
| Nautilus framework | Library | In monorepo | Data broker: postgres, rest, s3 adapters |
| PostGIS (postgis/postgis:16-3.4) | Infrastructure | Docker image | Required for spatial queries (ST_Contains, ST_Distance) on EEZ boundaries and AIS positions |
| Docker + Docker Compose | Infrastructure | Assumed installed | postgis, redis, llm-shim |
| uv package manager | Tooling | Assumed installed | `uv run --no-project` pattern |
| Python 3.11+ | Runtime | Assumed | Required by ultralytics + stargraph |
| Ultralytics YOLO11 | ML Library | pip/uv installable | Training + ONNX export |
| onnxruntime | ML Library | Already in `[ml]` extra | ONNX inference in Stargraph MLNode |
| Streamlit + streamlit-folium | UI Library | pip/uv installable | HITL analyst dashboard |
| rasterio + geopandas | Geo Library | pip/uv installable | GeoTIFF I/O + spatial queries |

## Success Criteria

1. **Cold-start narrative**: `make demo` on a fresh checkout → full pipeline run → dark vessels flagged → analyst reviews/corrects → `make retrain` triggers model update → metrics dashboard shows mAP improvement. Demo flow does NOT wait for nightly cron; presenter triggers retrain manually. All within a single live demo session (~20 minutes).
2. **Dark vessel detection**: At least 1 dark vessel correctly identified on the pre-staged Strait of Hormuz tiles that was not in the original AIS feed.
3. **Self-improvement proof**: Retrained model shows measurable mAP improvement (any positive delta) on holdout set after incorporating analyst corrections.
4. **Offline resilience**: `make demo-offline` completes the full pipeline without any network calls.
5. **Visual impact**: An investor watching the Streamlit dashboard sees a map with vessel detections, risk-colored markers, and a metrics chart showing improvement — without needing to understand the terminal.
