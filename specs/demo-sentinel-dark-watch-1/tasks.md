---
spec: demo-sentinel-dark-watch-1
phase: tasks
created: 2026-05-26
total_tasks: 83
---

# Tasks: Sentinel Dark Watch

## Phase 1: Make It Work (POC)

### Foundation (directory + infra + state)

- [x] 1.1 Create directory structure and `__init__.py` files
  - **Do**:
    1. Create `demos/sentinel_dark_watch/` with subdirectories: `graph/`, `fixtures/`, `mocks/llm-shim/`, `scripts/`, `ui/`, `tests/`, `data/`
    2. Add `__init__.py` to `demos/sentinel_dark_watch/` and `demos/sentinel_dark_watch/graph/`
    3. Add `.gitkeep` to `data/` (gitignored bulk content)
  - **Files**: demos/sentinel_dark_watch/__init__.py, demos/sentinel_dark_watch/graph/__init__.py
  - **Done when**: `python -c "import demos.sentinel_dark_watch"` succeeds
  - **Verify**: `uv run --no-project python -c "import demos.sentinel_dark_watch; print('OK')"`
  - **Commit**: `feat(sdw): scaffold directory structure`
  - _Requirements: FR-12, FR-13_
  - _Design: File Structure_

- [x] 1.2 Create `.env.example` and copy existing `.env`
  - **Do**:
    1. Create `.env.example` with all env vars and sensible defaults (POSTGRES_USER=harbor, POSTGRES_PASSWORD=harbor, POSTGRES_DB=sdw, POSTGRES_PORT=5441, REDIS_PORT=6391, LLM_PORT=41001, POSTGRES_DSN, AIS_STREAM_API_KEY placeholder, AIS_MODE=mock, LLM_BASE_URL=http://localhost:41001/v1)
    2. Move/update existing `.env` from `demos/sentinel-dark-watch/.env` into new location preserving API key
  - **Files**: demos/sentinel_dark_watch/.env.example, demos/sentinel_dark_watch/.env
  - **Done when**: `.env.example` lists all required vars; `.env` has the real AIS key
  - **Verify**: `grep -q AIS_STREAM_API_KEY demos/sentinel_dark_watch/.env.example && grep -q POSTGRES_DSN demos/sentinel_dark_watch/.env.example && echo PASS`
  - **Commit**: `feat(sdw): add .env.example with all config vars`
  - _Requirements: AC-1.2_

- [x] 1.3 Create `docker-compose.yml` (PostGIS + Redis + llm-shim)
  - **Do**:
    1. Create `demos/sentinel_dark_watch/docker-compose.yml` with three services: postgis (postgis/postgis:16-3.4 on port 5441), redis (redis:7-alpine on port 6391), llm-shim (custom build from `./mocks/llm-shim` on port 41001)
    2. Include healthchecks for all services per design spec
    3. Define `postgis-data` and `redis-data` volumes
  - **Files**: demos/sentinel_dark_watch/docker-compose.yml
  - **Done when**: `docker compose config` validates without error
  - **Verify**: `docker compose -f demos/sentinel_dark_watch/docker-compose.yml config --quiet && echo PASS`
  - **Commit**: `feat(sdw): docker-compose with PostGIS, Redis, llm-shim`
  - _Requirements: FR-13, AC-1.1_
  - _Design: Docker Compose Design_

- [x] 1.4 [VERIFY] Quality checkpoint: import check
  - **Do**: Verify module imports and docker-compose validates
  - **Verify**: `uv run --no-project python -c "import demos.sentinel_dark_watch" && docker compose -f demos/sentinel_dark_watch/docker-compose.yml config --quiet && echo PASS`
  - **Done when**: Both commands exit 0
  - **Commit**: `chore(sdw): pass quality checkpoint` (if fixes needed)

- [x] 1.5 Create state models (`graph/state.py`)
  - **Do**:
    1. Create `demos/sentinel_dark_watch/graph/state.py` with all classes from design: `RiskLevel`, `AnalystDecision` (StrEnum), `Detection`, `TileMetadata`, `RunMetrics`, `ModelMetrics` (BaseModel), `SdwState`, `RetrainState` (BaseModel)
    2. All fields with defaults per design spec
  - **Files**: demos/sentinel_dark_watch/graph/state.py
  - **Done when**: `from demos.sentinel_dark_watch.graph.state import SdwState, RetrainState` succeeds; `SdwState()` instantiates with defaults
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.state import SdwState, RetrainState; s = SdwState(); print(s.model_version, s.failure_threshold); r = RetrainState(); print('OK')"`
  - **Commit**: `feat(sdw): SdwState and RetrainState Pydantic models`
  - _Requirements: FR-2_
  - _Design: State Model_

- [x] 1.6 [P] Create main pipeline graph IR (`graph/harbor.yaml`)
  - **Do**:
    1. Create `demos/sentinel_dark_watch/graph/harbor.yaml` with all 14 nodes, state_class, tools, governance mounts, and all rules exactly per design spec
    2. Include conditional rules for FR-17 active learning routing
  - **Files**: demos/sentinel_dark_watch/graph/harbor.yaml
  - **Done when**: YAML parses correctly; all node IDs, rules, and state_class present
  - **Verify**: `uv run --no-project python -c "import yaml; d = yaml.safe_load(open('demos/sentinel_dark_watch/graph/harbor.yaml')); assert len(d['nodes']) >= 14; assert d['state_class'].endswith(':SdwState'); print('OK')"`
  - **Commit**: `feat(sdw): main pipeline harbor.yaml IR with 14 nodes`
  - _Requirements: FR-1, FR-17_
  - _Design: Main Pipeline Graph_

- [x] 1.7 [P] Create retrain sub-graph IR (`graph/retrain.yaml`)
  - **Do**:
    1. Create `demos/sentinel_dark_watch/graph/retrain.yaml` with 5 nodes: collect_corrections, retrain_model, champion_challenger, retrain_metrics, retrain_done
    2. Include state_class pointing to RetrainState, all rules per design
  - **Files**: demos/sentinel_dark_watch/graph/retrain.yaml
  - **Done when**: YAML parses; 5 nodes and 6 rules present
  - **Verify**: `uv run --no-project python -c "import yaml; d = yaml.safe_load(open('demos/sentinel_dark_watch/graph/retrain.yaml')); assert len(d['nodes']) == 5; print('OK')"`
  - **Commit**: `feat(sdw): retrain sub-graph retrain.yaml IR`
  - _Requirements: FR-10, AC-9.1_
  - _Design: Retrain Sub-Graph_

- [ ] 1.8 [VERIFY] Quality checkpoint: state + graph IR
  - **Do**: Verify state models instantiate and graph YAMLs parse
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.state import SdwState, RetrainState; import yaml; yaml.safe_load(open('demos/sentinel_dark_watch/graph/harbor.yaml')); yaml.safe_load(open('demos/sentinel_dark_watch/graph/retrain.yaml')); print('ALL OK')"`
  - **Done when**: All imports and parses succeed
  - **Commit**: `chore(sdw): pass quality checkpoint` (if fixes needed)

### LLM Mock + Fixtures

- [ ] 1.9 [P] Create LLM shim mock server
  - **Do**:
    1. Create `demos/sentinel_dark_watch/mocks/llm-shim/app.py` — FastAPI OpenAI-compatible server. Routes: `POST /v1/chat/completions` (keyword-match on "geo_context"/"situational summary" → maritime geo template; "intel report"/"reporting" → structured report; default → generic ack), `GET /health` → `{"status": "ok"}`
    2. Create `demos/sentinel_dark_watch/mocks/llm-shim/Dockerfile` — python:3.12-slim, pip install fastapi uvicorn pydantic, EXPOSE 41001, healthcheck
  - **Files**: demos/sentinel_dark_watch/mocks/llm-shim/app.py, demos/sentinel_dark_watch/mocks/llm-shim/Dockerfile
  - **Done when**: `python app.py` starts; `/health` returns 200; `/v1/chat/completions` returns canned maritime response
  - **Verify**: `grep -q "geo_context\|situational summary" demos/sentinel_dark_watch/mocks/llm-shim/app.py && grep -q "41001" demos/sentinel_dark_watch/mocks/llm-shim/Dockerfile && echo PASS`
  - **Commit**: `feat(sdw): LLM shim mock server with maritime canned responses`
  - _Requirements: AC-5.5, NFR-5_
  - _Design: LLM Mock Server_

- [ ] 1.10 [P] Create fixture data files
  - **Do**:
    1. Create `demos/sentinel_dark_watch/fixtures/ais_positions.json` — ~100 AIS positions in Strait of Hormuz AOI (lat ~25-27, lon ~55-57) with realistic MMSI, ship_name, speed_kn, heading, timestamps. Mix of identified vessels and gaps (no AIS coverage) for dark vessel correlation
    2. Create `demos/sentinel_dark_watch/fixtures/llm_responses.json` — keyed by prompt type (geo_context, reporting) with realistic maritime intel text
  - **Files**: demos/sentinel_dark_watch/fixtures/ais_positions.json, demos/sentinel_dark_watch/fixtures/llm_responses.json
  - **Done when**: Both JSON files parse; AIS has 50+ entries; LLM responses has geo_context and reporting keys
  - **Verify**: `uv run --no-project python -c "import json; ais = json.load(open('demos/sentinel_dark_watch/fixtures/ais_positions.json')); print(f'{len(ais)} AIS positions'); llm = json.load(open('demos/sentinel_dark_watch/fixtures/llm_responses.json')); assert 'geo_context' in llm; print('OK')"`
  - **Commit**: `feat(sdw): fixture AIS positions and LLM response data`
  - _Requirements: AC-4.2, NFR-5_
  - _Design: AIS Ingest Daemon (mock fallback)_

- [ ] 1.11 [VERIFY] Quality checkpoint: fixtures + mock
  - **Do**: Verify fixture JSON and LLM shim structure
  - **Verify**: `uv run --no-project python -c "import json; json.load(open('demos/sentinel_dark_watch/fixtures/ais_positions.json')); json.load(open('demos/sentinel_dark_watch/fixtures/llm_responses.json')); print('OK')" && grep -q '/v1/chat/completions' demos/sentinel_dark_watch/mocks/llm-shim/app.py && echo PASS`
  - **Done when**: All checks pass
  - **Commit**: `chore(sdw): pass quality checkpoint` (if fixes needed)

### Bootstrap

- [ ] 1.12 Create `bootstrap.py` — wait for Docker health + provision schemas
  - **Do**:
    1. Create `demos/sentinel_dark_watch/bootstrap.py` with `_load_env()`, `_wait_tcp()`, `_wait_http_health()` helpers (mirroring CVE-rem pattern)
    2. Phase 1: Wait for PostGIS (5441), Redis (6391), llm-shim (41001)
    3. Phase 2: Run all DDL from design (sar_tiles, ais_positions, eez_boundaries, ports, coastlines, detections, corrections, run_metrics, model_metrics tables + PostGIS extension + GIST indexes)
    4. Phase 3: Seed fixture AIS data from `fixtures/ais_positions.json` into `ais_positions` table
    5. Phase 4: Seed minimal EEZ/port/coastline fixtures (2-3 EEZ polygons for Strait of Hormuz, 5-10 ports near AOI, simplified coastline polygon). Use INSERT with WKT geometry.
    6. Make idempotent: `CREATE TABLE IF NOT EXISTS`, `INSERT ... ON CONFLICT DO NOTHING`
  - **Files**: demos/sentinel_dark_watch/bootstrap.py
  - **Done when**: `python -m demos.sentinel_dark_watch.bootstrap` runs against live Docker and exits 0; second run is also 0
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.bootstrap import main; print('import OK')"`
  - **Commit**: `feat(sdw): bootstrap.py with PostGIS schema provisioning and fixture seeding`
  - _Requirements: FR-12, AC-2.3, AC-5.2, AC-5.3_
  - _Design: Database Schema, bootstrap.py_

- [ ] 1.13 [VERIFY] Quality checkpoint: bootstrap importable
  - **Do**: Verify bootstrap module imports cleanly
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch import bootstrap; print('OK')"`
  - **Done when**: Import succeeds
  - **Commit**: `chore(sdw): pass quality checkpoint` (if fixes needed)

### Node Implementations (main pipeline)

- [ ] 1.14 Create PassthroughNode + base imports in `graph/nodes.py`
  - **Do**:
    1. Create `demos/sentinel_dark_watch/graph/nodes.py` with imports for `NodeBase`, `BaseModel`, `ExecutionContext`
    2. Implement `PassthroughNode(NodeBase)` — `execute()` returns empty dict (used for branch_resp_review and action_done)
  - **Files**: demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: `from demos.sentinel_dark_watch.graph.nodes import PassthroughNode` succeeds
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import PassthroughNode; print('OK')"`
  - **Commit**: `feat(sdw): PassthroughNode base in graph/nodes.py`
  - _Requirements: FR-1_

- [ ] 1.15 Implement SARIngestNode
  - **Do**:
    1. Add `SARIngestNode(NodeBase)` to `graph/nodes.py`
    2. `execute()`: Pop next tile_id from `state.tile_queue`, query PostGIS `sar_tiles` table for metadata, validate file exists, populate `current_tile` TileMetadata, set `current_tile_id` and `pipeline_phase="ingest"`
    3. If tile missing: increment `tiles_failed`; if >= `failure_threshold`, set `last_error` and return error signal
  - **Files**: demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: `from demos.sentinel_dark_watch.graph.nodes import SARIngestNode` succeeds
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import SARIngestNode; print('OK')"`
  - **Commit**: `feat(sdw): SARIngestNode with tile queue + failure threshold`
  - _Requirements: FR-3, AC-2.4, AC-2.5_
  - _Design: SARIngestNode_

- [ ] 1.16 Implement YOLOInferenceNode
  - **Do**:
    1. Add `YOLOInferenceNode(NodeBase)` to `graph/nodes.py`
    2. `execute()`: Load GeoTIFF via rasterio, tile into 640x640 patches, acquire ONNX session from ModelRegistry (`sdw-detector`/`production`), run inference per patch via `asyncio.to_thread`, decode OBB outputs, transform pixel coords to geo-coords via affine, build Detection objects
    3. POC: accept `ImportError` for rasterio/onnxruntime gracefully (log warning, return empty detections for now if deps missing)
  - **Files**: demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: Class defined and importable; handles missing deps gracefully
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import YOLOInferenceNode; print('OK')"`
  - **Commit**: `feat(sdw): YOLOInferenceNode with ONNX inference + geo-coord transform`
  - _Requirements: FR-3, AC-3.3_
  - _Design: YOLOInferenceNode_

- [ ] 1.17 [VERIFY] Quality checkpoint: ingest + inference nodes
  - **Do**: Verify all nodes so far import
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import PassthroughNode, SARIngestNode, YOLOInferenceNode; print('OK')"`
  - **Done when**: All imports succeed
  - **Commit**: `chore(sdw): pass quality checkpoint` (if fixes needed)

- [ ] 1.18 Implement NMSDeduplicationNode
  - **Do**:
    1. Add `NMSDeduplicationNode(NodeBase)` to `graph/nodes.py`
    2. `execute()`: Cross-tile NMS using rotated IoU on geo-coordinates. Configurable IoU threshold (default 0.5). Empty detections pass through unchanged.
    3. Pure geometry — no external deps
  - **Files**: demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: Class importable
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import NMSDeduplicationNode; print('OK')"`
  - **Commit**: `feat(sdw): NMSDeduplicationNode with rotated IoU`
  - _Requirements: FR-18, AC-3.4_
  - _Design: NMSDeduplicationNode_

- [ ] 1.19 Implement LandMaskFilterNode
  - **Do**:
    1. Add `LandMaskFilterNode(NodeBase)` to `graph/nodes.py`
    2. `execute()`: For each detection centroid, query PostGIS `coastlines` table with `ST_Contains`. Remove detections on land. Update `detection_count`. If PostGIS fails, log warning and skip filter.
  - **Files**: demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: Class importable
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import LandMaskFilterNode; print('OK')"`
  - **Commit**: `feat(sdw): LandMaskFilterNode with PostGIS ST_Contains`
  - _Requirements: FR-19, AC-3.5_
  - _Design: LandMaskFilterNode_

- [ ] 1.20 Implement AISCorrelationNode
  - **Do**:
    1. Add `AISCorrelationNode(NodeBase)` to `graph/nodes.py`
    2. `execute()`: Query `ais_positions` via Postgres (direct or BrokerNode). Predicted-position matching per AC-4.3: compute expected position from last AIS report. Apply spatial radius (`ais_match_radius_m`). Match detections. Unmatched → `dark_vessel=True`. Matched → enrich with MMSI, name, flag_state, vessel_type.
    3. If query fails, mark all as `dark_vessel=True` (conservative)
  - **Files**: demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: Class importable
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import AISCorrelationNode; print('OK')"`
  - **Commit**: `feat(sdw): AISCorrelationNode with predicted-position matching`
  - _Requirements: FR-5, AC-4.3, AC-4.4, AC-4.5_
  - _Design: AISCorrelationNode_

- [ ] 1.21 [VERIFY] Quality checkpoint: detection pipeline nodes
  - **Do**: Verify all Phase 1-2 nodes import
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import NMSDeduplicationNode, LandMaskFilterNode, AISCorrelationNode; print('OK')"`
  - **Done when**: All imports succeed
  - **Commit**: `chore(sdw): pass quality checkpoint` (if fixes needed)

- [ ] 1.22 Implement GeoContextNode
  - **Do**:
    1. Add `GeoContextNode(NodeBase)` to `graph/nodes.py`
    2. `execute()`: Step 1 — PostGIS queries: `ST_Contains` for EEZ lookup, `ST_Distance` for port/coast distances. Populate `eez_name`, `distance_to_port_nm`, `distance_to_coast_nm`, `fishing_zone`. Step 2 — DSPy ChainOfThought call internally (import dspy, create `GeoContextSignature`, call module). LLM failure → templated fallback.
  - **Files**: demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: Class importable; falls back gracefully if DSPy not available
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import GeoContextNode; print('OK')"`
  - **Commit**: `feat(sdw): GeoContextNode with PostGIS + DSPy synthesis`
  - _Requirements: FR-6, AC-5.1, AC-5.4_
  - _Design: GeoContextNode_

- [ ] 1.23 Implement RiskScoringNode
  - **Do**:
    1. Add `RiskScoringNode(NodeBase)` to `graph/nodes.py`
    2. `execute()`: Apply scoring formula from design (dark_vessel + sensitive_eez + far_from_port + large_vessel + confidence). Read weights from state fields. Classify risk levels: Critical 80-100, High 60-79, Medium 40-59, Low 0-39.
    3. Set `has_low_confidence_detections` for FR-17 active learning routing
  - **Files**: demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: Class importable; scoring formula matches design
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import RiskScoringNode; print('OK')"`
  - **Commit**: `feat(sdw): RiskScoringNode with configurable weights + risk levels`
  - _Requirements: FR-7, AC-6.1, AC-6.2, AC-6.4_
  - _Design: RiskScoringNode_

- [ ] 1.24 Implement ReportingNode
  - **Do**:
    1. Add `ReportingNode(NodeBase)` to `graph/nodes.py`
    2. `execute()`: Assemble structured report sections (Detection Summary, Imagery Reference, AIS Correlation, Geo-Context, Risk Assessment, Recommended Actions) from detection fields. Then call DSPy ChainOfThought for narrative synthesis. LLM failure → templated fallback.
  - **Files**: demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: Class importable
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import ReportingNode; print('OK')"`
  - **Commit**: `feat(sdw): ReportingNode with DSPy narrative + templated fallback`
  - _Requirements: FR-8, AC-7.1_
  - _Design: ReportingNode_

- [ ] 1.25 [VERIFY] Quality checkpoint: enrichment + scoring + reporting nodes
  - **Do**: Verify all enrichment pipeline nodes import
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import GeoContextNode, RiskScoringNode, ReportingNode; print('OK')"`
  - **Done when**: All imports succeed
  - **Commit**: `chore(sdw): pass quality checkpoint` (if fixes needed)

- [ ] 1.26 Implement EmitSARChipsNode
  - **Do**:
    1. Add `EmitSARChipsNode(NodeBase)` to `graph/nodes.py`
    2. `execute()`: For each detection, crop 128x128 region around centroid from source GeoTIFF, save as PNG via rasterio/Pillow, persist via `FilesystemArtifactStore.put()`, store ref in `chip_artifact_ref`. On failure, log and continue.
  - **Files**: demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: Class importable
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import EmitSARChipsNode; print('OK')"`
  - **Commit**: `feat(sdw): EmitSARChipsNode for SAR chip artifact extraction`
  - _Requirements: FR-8, AC-7.2, AC-7.3_
  - _Design: EmitSARChipsNode_

- [ ] 1.27 Implement AnalystReviewNode
  - **Do**:
    1. Add `AnalystReviewNode(NodeBase)` to `graph/nodes.py`
    2. `execute()`: Raise `_HitInterrupt` with prompt listing detection count and tile ID. On resume (after `POST /respond`), populate `analyst_corrections` and `response_decision`. Write corrections to Postgres `corrections` table.
  - **Files**: demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: Class importable
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import AnalystReviewNode; print('OK')"`
  - **Commit**: `feat(sdw): AnalystReviewNode HITL interrupt gate`
  - _Requirements: FR-9, AC-8.4, AC-8.5_
  - _Design: AnalystReviewNode_

- [ ] 1.28 Implement MetricsCollectorNode
  - **Do**:
    1. Add `MetricsCollectorNode(NodeBase)` to `graph/nodes.py`
    2. `execute()`: Compute RunMetrics from state (detection counts, dark vessels, AIS matches, FP from corrections, processing time). Write to Postgres `run_metrics` table.
  - **Files**: demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: Class importable
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import MetricsCollectorNode; print('OK')"`
  - **Commit**: `feat(sdw): MetricsCollectorNode run-level metrics`
  - _Requirements: AC-10.3_
  - _Design: MetricsCollectorNode_

- [ ] 1.29 Implement RetrainTriggerNode
  - **Do**:
    1. Add `RetrainTriggerNode(NodeBase)` to `graph/nodes.py`
    2. `execute()`: Check if corrections_count >= 10 threshold. If so and triggered, dispatch retrain sub-graph. Otherwise proceed to action_done.
  - **Files**: demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: Class importable
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import RetrainTriggerNode; print('OK')"`
  - **Commit**: `feat(sdw): RetrainTriggerNode correction-threshold gate`
  - _Requirements: FR-10, AC-9.4_
  - _Design: RetrainTriggerNode_

- [ ] 1.30 [VERIFY] Quality checkpoint: all main pipeline nodes
  - **Do**: Verify all 12 main pipeline node classes import
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import PassthroughNode, SARIngestNode, YOLOInferenceNode, NMSDeduplicationNode, LandMaskFilterNode, AISCorrelationNode, GeoContextNode, RiskScoringNode, ReportingNode, EmitSARChipsNode, AnalystReviewNode, MetricsCollectorNode, RetrainTriggerNode; print('ALL 13 nodes OK')"`
  - **Done when**: All 13 node classes import
  - **Commit**: `chore(sdw): pass quality checkpoint` (if fixes needed)

### Retrain Sub-Graph Nodes

- [ ] 1.31 [P] Implement RetrainCollectNode
  - **Do**:
    1. Add `RetrainCollectNode(NodeBase)` to `graph/nodes.py`
    2. `execute()`: Query `corrections` table for unconsumed corrections (`consumed=false`). Count and merge with original training labels concept. Set `corrections_count`, `merged_training_samples`.
  - **Files**: demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: Class importable
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import RetrainCollectNode; print('OK')"`
  - **Commit**: `feat(sdw): RetrainCollectNode corrections collector`
  - _Requirements: FR-10, AC-9.1_

- [ ] 1.32 [P] Implement RetrainTrainNode
  - **Do**:
    1. Add `RetrainTrainNode(NodeBase)` to `graph/nodes.py`
    2. `execute()`: Shell out to `scripts/train_detector.py` with merged data. Capture new model path + mAP. Register in ModelRegistry.
  - **Files**: demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: Class importable
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import RetrainTrainNode; print('OK')"`
  - **Commit**: `feat(sdw): RetrainTrainNode model fine-tuning`
  - _Requirements: FR-10, AC-9.1_

- [ ] 1.33 [P] Implement ChampionChallengerNode + RetrainMetricsNode
  - **Do**:
    1. Add `ChampionChallengerNode(NodeBase)`: Load champion + challenger from ModelRegistry, compare mAP on holdout, set `challenger_wins`, promote if wins
    2. Add `RetrainMetricsNode(NodeBase)`: Write ModelMetrics to Postgres `model_metrics` table
  - **Files**: demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: Both classes importable
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import ChampionChallengerNode, RetrainMetricsNode; print('OK')"`
  - **Commit**: `feat(sdw): ChampionChallengerNode + RetrainMetricsNode`
  - _Requirements: FR-10, AC-9.2, AC-9.3_
  - _Design: Retrain Sub-Graph Nodes_

- [ ] 1.34 [VERIFY] Quality checkpoint: all nodes complete
  - **Do**: Verify all node classes import (17 total: 13 main + 4 retrain)
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import RetrainCollectNode, RetrainTrainNode, ChampionChallengerNode, RetrainMetricsNode; print('ALL retrain nodes OK')"`
  - **Done when**: All imports succeed
  - **Commit**: `chore(sdw): pass quality checkpoint` (if fixes needed)

### Integration Layer (nautilus, ais, serve, capabilities, scripts, UI)

- [ ] 1.35 Create `nautilus.yaml`
  - **Do**:
    1. Create `demos/sentinel_dark_watch/nautilus.yaml` per design spec — 3 sources: `ais_buffer` (postgres, ais_positions), `geo_data` (postgres, eez_boundaries), `detection_store` (postgres, detections). Agent `sdw-pipeline` with `clearance: unclassified`.
  - **Files**: demos/sentinel_dark_watch/nautilus.yaml
  - **Done when**: YAML parses; 3 sources declared
  - **Verify**: `uv run --no-project python -c "import yaml; d = yaml.safe_load(open('demos/sentinel_dark_watch/nautilus.yaml')); assert len(d['sources']) == 3; print('OK')"`
  - **Commit**: `feat(sdw): nautilus.yaml broker config for AIS, geo, detections`
  - _Requirements: FR-11, AC-4.6_
  - _Design: Nautilus Configuration_

- [ ] 1.36 Create `ais_ingest.py` daemon
  - **Do**:
    1. Create `demos/sentinel_dark_watch/ais_ingest.py` — standalone asyncio daemon
    2. Mock mode: load `fixtures/ais_positions.json`, bulk-insert into `ais_positions` table, exit
    3. Live mode: connect to `wss://stream.aisstream.io/v0/stream` with API key from env, parse PositionReport messages, insert to Postgres. Reconnect with exponential backoff.
    4. `__main__` block: read `AIS_MODE` env var (default "mock")
  - **Files**: demos/sentinel_dark_watch/ais_ingest.py
  - **Done when**: `python -m demos.sentinel_dark_watch.ais_ingest --help` or import succeeds
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch import ais_ingest; print('OK')"`
  - **Commit**: `feat(sdw): AIS ingest daemon with live WebSocket + mock fallback`
  - _Requirements: FR-4, AC-4.1, AC-4.2_
  - _Design: AIS Ingest Daemon_

- [ ] 1.37 [VERIFY] Quality checkpoint: nautilus + ais_ingest
  - **Do**: Verify nautilus config and ais_ingest import
  - **Verify**: `uv run --no-project python -c "import yaml; yaml.safe_load(open('demos/sentinel_dark_watch/nautilus.yaml')); from demos.sentinel_dark_watch import ais_ingest; print('OK')"`
  - **Done when**: Both pass
  - **Commit**: `chore(sdw): pass quality checkpoint` (if fixes needed)

- [ ] 1.38 Create `capabilities.py`
  - **Do**:
    1. Create `demos/sentinel_dark_watch/capabilities.py` with `build_sdw_capabilities()` function
    2. Follow CVE-rem pattern: enumerate tool permissions (nautilus.broker_request), build `Capabilities(default_deny=True)` with granted set
  - **Files**: demos/sentinel_dark_watch/capabilities.py
  - **Done when**: `from demos.sentinel_dark_watch.capabilities import build_sdw_capabilities` succeeds
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.capabilities import build_sdw_capabilities; print('OK')"`
  - **Commit**: `feat(sdw): engine-side capability profile`
  - _Requirements: FR-14_
  - _Design: capabilities.py_

- [ ] 1.39 Create `serve_sdw.py`
  - **Do**:
    1. Create `demos/sentinel_dark_watch/serve_sdw.py` — harbor serve wrapper per CVE-rem pattern
    2. Argparse: `--host`, `--port` (default 9001), `--graph` (repeatable, default harbor.yaml + retrain.yaml)
    3. Load .env, pin capabilities via `build_sdw_capabilities()`, import `create_app`, start uvicorn
    4. Register nightly retrain schedule via APScheduler (02:00 UTC)
    5. `__main__` block
  - **Files**: demos/sentinel_dark_watch/serve_sdw.py
  - **Done when**: `python -m demos.sentinel_dark_watch.serve_sdw --help` shows usage
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch import serve_sdw; print('OK')"`
  - **Commit**: `feat(sdw): serve_sdw.py harbor serve wrapper with retrain scheduler`
  - _Requirements: FR-14, AC-9.1_
  - _Design: serve_sdw.py, Nightly Retrain Cron_

- [ ] 1.40 [VERIFY] Quality checkpoint: serve + capabilities
  - **Do**: Verify both modules import
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.capabilities import build_sdw_capabilities; from demos.sentinel_dark_watch import serve_sdw; print('OK')"`
  - **Done when**: Imports succeed
  - **Commit**: `chore(sdw): pass quality checkpoint` (if fixes needed)

- [ ] 1.41 [P] Create `scripts/prepare_dataset.py`
  - **Do**:
    1. Create `demos/sentinel_dark_watch/scripts/__init__.py`
    2. Create `demos/sentinel_dark_watch/scripts/prepare_dataset.py` — load xView3 scenes via rasterio, tile into 640x640 patches with 10% overlap, convert CSV point labels to YOLO OBB format (synthesize OBB from vessel_length_m + 1:4 w:l ratio), generate `data.yaml` config
    3. POC: accept ImportError for rasterio gracefully
  - **Files**: demos/sentinel_dark_watch/scripts/__init__.py, demos/sentinel_dark_watch/scripts/prepare_dataset.py
  - **Done when**: Script importable
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.scripts import prepare_dataset; print('OK')"`
  - **Commit**: `feat(sdw): prepare_dataset.py xView3 tiling + OBB label conversion`
  - _Requirements: FR-15, AC-2.1, AC-2.2, AC-3.1, AC-3.2_
  - _Design: scripts/prepare_dataset.py_

- [ ] 1.42 [P] Create `scripts/train_detector.py`
  - **Do**:
    1. Create `demos/sentinel_dark_watch/scripts/train_detector.py` — fine-tune YOLO11-OBB on prepared data, export to ONNX, compute SHA-256, register in ModelRegistry with `production` alias
    2. Argparse: `--data`, `--epochs` (default 50), `--model` (default yolo11s-obb.pt)
    3. POC: accept ImportError for ultralytics gracefully
  - **Files**: demos/sentinel_dark_watch/scripts/train_detector.py
  - **Done when**: Script importable
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.scripts import train_detector; print('OK')"`
  - **Commit**: `feat(sdw): train_detector.py YOLO fine-tune + ONNX export + ModelRegistry`
  - _Requirements: FR-16, AC-3.2, AC-3.6_
  - _Design: scripts/train_detector.py_

- [ ] 1.43 [VERIFY] Quality checkpoint: scripts
  - **Do**: Verify both scripts import
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.scripts import prepare_dataset, train_detector; print('OK')"`
  - **Done when**: Imports succeed
  - **Commit**: `chore(sdw): pass quality checkpoint` (if fixes needed)

### Streamlit UI

- [ ] 1.44 Create Streamlit `ui/app.py` — tab structure + map view
  - **Do**:
    1. Create `demos/sentinel_dark_watch/ui/app.py` with Streamlit tab layout: "Live Map", "Detection Review", "Metrics Dashboard", "Pipeline Status"
    2. Tab 1 (Live Map): Folium map centered on Strait of Hormuz (lat ~26.5, lon ~56.2). Query detections from Harbor API, render markers color-coded by risk level (red=Critical, orange=High, yellow=Medium, green=Low). Show AIS tracks as polylines.
    3. `HARBOR_URL` from env (default `http://localhost:9001`)
    4. Helper functions: `trigger_run(tile_ids)` → `POST /v1/runs`, `get_detections(run_id)` → fetch from state
  - **Files**: demos/sentinel_dark_watch/ui/app.py
  - **Done when**: `streamlit run ui/app.py --server.headless true` starts without error (or import at least succeeds)
  - **Verify**: `uv run --no-project python -c "import ast; ast.parse(open('demos/sentinel_dark_watch/ui/app.py').read()); print('SYNTAX OK')"`
  - **Commit**: `feat(sdw): Streamlit app.py with map view + tab structure`
  - _Requirements: AC-8.1_
  - _Design: Streamlit UI Design_

- [ ] 1.45 Add Detection Review tab to Streamlit
  - **Do**:
    1. Extend `ui/app.py` Tab 2: Detection Review — table sorted by risk score descending (AC-6.3), SAR chip image, confidence bar, AIS status, geo-summary, risk badge, draft report
    2. Editable report text area per detection — analyst can edit draft report before submission (AC-7.4)
    3. Action buttons: Confirm Vessel / Reject (FP) / Flag for Review / Override Risk
    4. Submit → `POST /v1/runs/{run_id}/respond` with corrections payload (includes edited reports)
  - **Files**: demos/sentinel_dark_watch/ui/app.py
  - **Done when**: Review tab code added; `submit_review()` function defined
  - **Verify**: `uv run --no-project python -c "import ast; ast.parse(open('demos/sentinel_dark_watch/ui/app.py').read()); print('SYNTAX OK')"`
  - **Commit**: `feat(sdw): Detection Review tab with HITL actions`
  - _Requirements: AC-6.3, AC-7.4, AC-8.2, AC-8.3, AC-8.4, AC-8.6_
  - _Design: Streamlit UI Design (Tab 2)_

- [ ] 1.46 Add Metrics Dashboard + Pipeline Status tabs to Streamlit
  - **Do**:
    1. Tab 3 (Metrics): Plotly charts — mAP over model versions (line), dark vessel count per run (bar), FP rate trend (line). Before/after comparison card. Data from `run_metrics` + `model_metrics` tables via Postgres.
    2. Tab 4 (Pipeline Status): WebSocket connection to `GET /v1/runs/{id}/stream`, show current node + progress bar + JSONL audit viewer
  - **Files**: demos/sentinel_dark_watch/ui/app.py
  - **Done when**: All 4 tabs have content; syntax valid
  - **Verify**: `uv run --no-project python -c "import ast; ast.parse(open('demos/sentinel_dark_watch/ui/app.py').read()); print('SYNTAX OK')"`
  - **Commit**: `feat(sdw): Metrics Dashboard + Pipeline Status tabs`
  - _Requirements: AC-10.1, AC-10.2, AC-10.4, AC-11.1, AC-11.2, AC-11.3, AC-11.4_
  - _Design: Streamlit UI Design (Tabs 3-4)_

- [ ] 1.47 [VERIFY] Quality checkpoint: UI syntax + full module imports
  - **Do**: Verify all demo modules import and UI syntax is valid
  - **Verify**: `uv run --no-project python -c "import ast; ast.parse(open('demos/sentinel_dark_watch/ui/app.py').read()); from demos.sentinel_dark_watch.graph.nodes import PassthroughNode; from demos.sentinel_dark_watch.graph.state import SdwState; print('OK')"`
  - **Done when**: All checks pass
  - **Commit**: `chore(sdw): pass quality checkpoint` (if fixes needed)

### Justfile + pyproject.toml + .gitignore

- [ ] 1.48 [P] Create `Justfile`
  - **Do**:
    1. Create `demos/sentinel_dark_watch/Justfile` with all commands from design: `demo`, `demo-offline`, `teardown`, `prepare-data`, `train`, `retrain`, `bootstrap`, `serve`, `ui`, `test`
    2. Set `set dotenv-load` at top
  - **Files**: demos/sentinel_dark_watch/Justfile
  - **Done when**: `just --list` in demo dir shows all commands
  - **Verify**: `grep -q "demo:" demos/sentinel_dark_watch/Justfile && grep -q "teardown:" demos/sentinel_dark_watch/Justfile && echo PASS`
  - **Commit**: `feat(sdw): Justfile with demo/teardown/train/retrain commands`
  - _Requirements: AC-1.1, AC-1.4, AC-1.5, AC-9.4_
  - _Design: Justfile_

- [ ] 1.49 [P] Add `[sdw]` extra to monorepo `pyproject.toml`
  - **Do**:
    1. Add `sdw` optional dependency group to `/home/sean/leagues/harbor/pyproject.toml` under `[project.optional-dependencies]`
    2. Include: ultralytics>=8.3, torch>=2.0, torchvision>=0.15, rasterio>=1.3, geopandas>=0.14, shapely>=2.0, streamlit>=1.30, streamlit-folium>=0.20, folium>=0.15, websockets>=12.0, Pillow>=10.0, asyncpg>=0.29, plotly>=5.18
    3. Add inline comment about ultralytics version numbering
  - **Files**: pyproject.toml
  - **Done when**: `uv pip compile --extra sdw` or `grep sdw pyproject.toml` shows the extra
  - **Verify**: `grep -A 15 'sdw = \[' pyproject.toml | head -20 && echo PASS`
  - **Commit**: `feat(sdw): add [sdw] optional dependency group`
  - _Requirements: NFR-1_
  - _Design: Dependencies_

- [ ] 1.50 Update `.gitignore` for SDW
  - **Do**:
    1. Add entries to root `.gitignore`: `demos/sentinel_dark_watch/.env`, `demos/sentinel_dark_watch/data/`, `demos/sentinel_dark_watch/data/**`, `!demos/sentinel_dark_watch/data/.gitkeep`
  - **Files**: .gitignore
  - **Done when**: `.env` and `data/` are gitignored
  - **Verify**: `grep -q "sentinel_dark_watch" .gitignore && echo PASS`
  - **Commit**: `chore(sdw): gitignore .env and data directory`

- [ ] 1.51 [VERIFY] Quality checkpoint: full module structure
  - **Do**: Verify complete module can be imported and all key files exist
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.state import SdwState, RetrainState; from demos.sentinel_dark_watch.graph.nodes import PassthroughNode, SARIngestNode; from demos.sentinel_dark_watch import bootstrap, ais_ingest, serve_sdw, capabilities; print('ALL MODULES OK')" && test -f demos/sentinel_dark_watch/Justfile && test -f demos/sentinel_dark_watch/docker-compose.yml && echo PASS`
  - **Done when**: All imports and file checks succeed
  - **Commit**: `chore(sdw): pass quality checkpoint` (if fixes needed)

### POC Checkpoint

- [ ] 1.52 POC Checkpoint: end-to-end pipeline verification
  - **Do**:
    1. Start Docker services: `docker compose -f demos/sentinel_dark_watch/docker-compose.yml up -d`
    2. Wait for health: `curl -sf http://localhost:41001/health`
    3. Run bootstrap: `uv run --no-project python -m demos.sentinel_dark_watch.bootstrap`
    4. Run AIS ingest (mock): `AIS_MODE=mock uv run --no-project python -m demos.sentinel_dark_watch.ais_ingest`
    5. Verify PostGIS tables exist and have data: query ais_positions count
    6. Verify all node classes instantiate with default state
    7. Tear down: `docker compose -f demos/sentinel_dark_watch/docker-compose.yml down -v`
  - **Done when**: Docker up, bootstrap runs, tables created, fixture data loaded, nodes instantiate, teardown clean
  - **Verify**: `docker compose -f demos/sentinel_dark_watch/docker-compose.yml up -d --wait && uv run --no-project python -m demos.sentinel_dark_watch.bootstrap && uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import SARIngestNode, YOLOInferenceNode, AISCorrelationNode, GeoContextNode, RiskScoringNode, ReportingNode, AnalystReviewNode, MetricsCollectorNode; print('POC PASS')" && docker compose -f demos/sentinel_dark_watch/docker-compose.yml down -v`
  - **Commit**: `feat(sdw): complete POC — full pipeline structure validated`
  - _Requirements: AC-1.1, FR-1, FR-2, FR-12, FR-13_

## Phase 2: Refactoring

- [ ] 2.1 Extract DB connection helpers into shared module
  - **Do**:
    1. Create `demos/sentinel_dark_watch/db.py` with `get_pg_pool()`, `get_pg_dsn()` helpers using asyncpg
    2. Refactor nodes that do direct Postgres queries to use shared pool helper
    3. Add connection pooling (min=2, max=10)
  - **Files**: demos/sentinel_dark_watch/db.py, demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: All Postgres-accessing nodes use shared pool; no inline DSN parsing
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.db import get_pg_dsn; print('OK')"`
  - **Commit**: `refactor(sdw): extract shared DB connection helpers`
  - _Design: Error Handling_

- [ ] 2.2 Add error handling to all nodes
  - **Do**:
    1. Add try/except with logging to every node's `execute()`
    2. SARIngestNode: handle missing tile file, increment tiles_failed
    3. YOLOInferenceNode: handle ONNX session errors, model hash mismatch
    4. AISCorrelationNode: handle broker failure → conservative dark_vessel=True
    5. GeoContextNode/ReportingNode: handle LLM unavailable → templated fallback
    6. Set `last_error` on state for all failure paths
  - **Files**: demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: Every node has error handling; no unhandled exceptions escape `execute()`
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.nodes import SARIngestNode; print('OK')"`
  - **Commit**: `refactor(sdw): add error handling to all node execute() methods`
  - _Design: Error Handling table_

- [ ] 2.3 [VERIFY] Quality checkpoint: refactoring
  - **Do**: Verify module imports after refactoring
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.db import get_pg_dsn; from demos.sentinel_dark_watch.graph.nodes import SARIngestNode, YOLOInferenceNode, AISCorrelationNode, GeoContextNode, RiskScoringNode; print('OK')"`
  - **Done when**: All imports succeed
  - **Commit**: `chore(sdw): pass quality checkpoint` (if fixes needed)

- [ ] 2.4 Make scoring weights and thresholds configurable via env vars
  - **Do**:
    1. In `bootstrap.py` or a new `config.py`: read `RISK_WEIGHT_DARK_VESSEL`, `RISK_WEIGHT_SENSITIVE_EEZ`, etc. from env
    2. Pass through to initial state when creating runs
    3. Add `SENSITIVE_EEZS` as env-configurable comma-separated list
    4. Ensure `failure_threshold`, `low_conf_threshold`, `ais_match_radius_m` are env-overridable
  - **Files**: demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: Env vars override state defaults
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.state import SdwState; s = SdwState(risk_weight_dark_vessel=50); assert s.risk_weight_dark_vessel == 50; print('OK')"`
  - **Commit**: `refactor(sdw): configurable scoring weights and thresholds via env`
  - _Requirements: AC-6.4_

- [ ] 2.5 Clean up bootstrap.py — extract DDL into separate SQL file
  - **Do**:
    1. Create `demos/sentinel_dark_watch/schema.sql` with all DDL from bootstrap
    2. Refactor `bootstrap.py` to read and execute SQL file
    3. Keep fixture seeding logic in bootstrap.py
  - **Files**: demos/sentinel_dark_watch/schema.sql, demos/sentinel_dark_watch/bootstrap.py
  - **Done when**: DDL is in .sql file; bootstrap reads it
  - **Verify**: `test -f demos/sentinel_dark_watch/schema.sql && uv run --no-project python -c "from demos.sentinel_dark_watch import bootstrap; print('OK')" && echo PASS`
  - **Commit**: `refactor(sdw): extract DDL into schema.sql`
  - _Design: Database Schema_

- [ ] 2.6 Extract DSPy signatures into dedicated module
  - **Do**:
    1. Create `demos/sentinel_dark_watch/graph/signatures.py` with `GeoContextSignature` and `ReportingSignature` classes
    2. Refactor `GeoContextNode` and `ReportingNode` to import from signatures module
    3. Move LLM fallback templates to a `FALLBACK_TEMPLATES` dict in signatures module
  - **Files**: demos/sentinel_dark_watch/graph/signatures.py, demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: DSPy signatures in dedicated module; nodes import from it
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.signatures import GeoContextSignature; print('OK')"`
  - **Commit**: `refactor(sdw): extract DSPy signatures into dedicated module`
  - _Design: GeoContextNode, ReportingNode_

- [ ] 2.7 Consolidate PostGIS query patterns across nodes
  - **Do**:
    1. Create `demos/sentinel_dark_watch/geo.py` with shared spatial query helpers: `point_in_eez()`, `nearest_port()`, `point_on_land()`, `predicted_ais_position()`
    2. Refactor `LandMaskFilterNode`, `AISCorrelationNode`, `GeoContextNode` to use shared helpers
  - **Files**: demos/sentinel_dark_watch/geo.py, demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: All PostGIS queries go through shared helpers; no inline SQL in nodes
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.geo import point_in_eez, nearest_port; print('OK')"`
  - **Commit**: `refactor(sdw): consolidate PostGIS queries into geo.py`
  - _Design: LandMaskFilterNode, AISCorrelationNode, GeoContextNode_

- [ ] 2.8 Add JSONL audit log writing to serve_sdw.py
  - **Do**:
    1. Wire audit event handler in `serve_sdw.py` that writes per-run JSONL files to `data/audit/`
    2. Each line: `{"ts": ..., "run_id": ..., "node_id": ..., "event": ..., "duration_ms": ...}`
    3. Directory created at bootstrap if not exists
  - **Files**: demos/sentinel_dark_watch/serve_sdw.py, demos/sentinel_dark_watch/bootstrap.py
  - **Done when**: JSONL audit files written during graph execution
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.serve_sdw import *; print('OK')"`
  - **Commit**: `feat(sdw): JSONL audit log per run`
  - _Requirements: AC-11.3_
  - _Design: Pipeline Observability_

- [ ] 2.9 Add performance timing to pipeline for AC-1.3 tracking
  - **Do**:
    1. Add `perf_marks` dict to `SdwState` tracking per-node wall-clock time
    2. Emit timing in MetricsCollectorNode → `run_metrics.processing_secs`
    3. Log total pipeline time at action_done
  - **Files**: demos/sentinel_dark_watch/graph/state.py, demos/sentinel_dark_watch/graph/nodes.py
  - **Done when**: Pipeline timing tracked end-to-end; processing_secs populated
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.state import SdwState; s = SdwState(); assert hasattr(s, 'run_started_at'); print('OK')"`
  - **Commit**: `feat(sdw): pipeline performance timing for AC-1.3`
  - _Requirements: AC-1.3, NFR-1_

- [ ] 2.10 [VERIFY] Quality checkpoint: post-refactoring full import
  - **Do**: Verify full module after all refactoring
  - **Verify**: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.state import SdwState; from demos.sentinel_dark_watch.graph.nodes import SARIngestNode, RiskScoringNode; from demos.sentinel_dark_watch.graph.signatures import GeoContextSignature; from demos.sentinel_dark_watch.geo import point_in_eez; from demos.sentinel_dark_watch import bootstrap, serve_sdw, capabilities, ais_ingest; print('REFACTOR OK')"`
  - **Done when**: All modules import cleanly
  - **Commit**: `chore(sdw): pass quality checkpoint` (if fixes needed)

## Phase 3: Testing

- [ ] 3.1 Create test conftest with fixtures
  - **Do**:
    1. Create `demos/sentinel_dark_watch/tests/__init__.py`
    2. Create `demos/sentinel_dark_watch/tests/conftest.py` with fixtures: `sample_state()` (SdwState with realistic defaults), `sample_detections()` (list of 5 Detection objects — mix of dark/matched/land), `sample_tile_metadata()`, `mock_pg_pool()` (asyncpg pool mock), `mock_onnx_session()`
  - **Files**: demos/sentinel_dark_watch/tests/__init__.py, demos/sentinel_dark_watch/tests/conftest.py
  - **Done when**: `pytest --collect-only demos/sentinel_dark_watch/tests/conftest.py` shows fixtures
  - **Verify**: `uv run --no-project pytest --collect-only demos/sentinel_dark_watch/tests/conftest.py 2>&1 | grep -q "conftest" && echo PASS`
  - **Commit**: `test(sdw): conftest with sample state, detection, and mock fixtures`
  - _Design: Test Strategy_

- [ ] 3.2 Unit tests for RiskScoringNode
  - **Do**:
    1. Create `demos/sentinel_dark_watch/tests/test_nodes.py`
    2. Tests: dark vessel in sensitive EEZ → Critical (80+); AIS-matched near port → Low (<40); configurable weights change score; empty detections → pass-through; low-conf threshold sets `has_low_confidence_detections`
  - **Files**: demos/sentinel_dark_watch/tests/test_nodes.py
  - **Done when**: 4+ test cases pass
  - **Verify**: `uv run --no-project pytest demos/sentinel_dark_watch/tests/test_nodes.py -v -k "risk" --tb=short`
  - **Commit**: `test(sdw): unit tests for RiskScoringNode scoring + levels`
  - _Requirements: AC-6.1, AC-6.2, AC-6.4_
  - _Design: Test Strategy (Unit Tests)_

- [ ] 3.3 Unit tests for NMSDeduplicationNode
  - **Do**:
    1. Add tests to `test_nodes.py`: two overlapping detections (IoU > 0.5) → one output; non-overlapping → both preserved; empty list → empty list
  - **Files**: demos/sentinel_dark_watch/tests/test_nodes.py
  - **Done when**: 3+ NMS test cases pass
  - **Verify**: `uv run --no-project pytest demos/sentinel_dark_watch/tests/test_nodes.py -v -k "nms" --tb=short`
  - **Commit**: `test(sdw): unit tests for NMSDeduplicationNode`
  - _Requirements: FR-18, AC-3.4_

- [ ] 3.4 [VERIFY] Quality checkpoint: test runner works
  - **Do**: Run all existing tests
  - **Verify**: `uv run --no-project pytest demos/sentinel_dark_watch/tests/ -v --tb=short`
  - **Done when**: All tests pass
  - **Commit**: `chore(sdw): pass quality checkpoint` (if fixes needed)

- [ ] 3.5 Unit tests for AISCorrelationNode
  - **Do**:
    1. Add tests: known AIS position near detection → matched (dark_vessel=False, MMSI set); no AIS → dark_vessel=True; broker failure → conservative marking; predicted-position matching accuracy
  - **Files**: demos/sentinel_dark_watch/tests/test_nodes.py
  - **Done when**: 3+ AIS test cases pass
  - **Verify**: `uv run --no-project pytest demos/sentinel_dark_watch/tests/test_nodes.py -v -k "ais" --tb=short`
  - **Commit**: `test(sdw): unit tests for AISCorrelationNode`
  - _Requirements: FR-5, AC-4.3, AC-4.4_

- [ ] 3.6 Unit tests for SARIngestNode + LandMaskFilterNode
  - **Do**:
    1. SARIngestNode tests: valid tile → populates current_tile; missing tile → increment tiles_failed; failure_threshold breach → last_error set
    2. LandMaskFilterNode tests: detection on land → removed; detection at sea → preserved; PostGIS failure → skip filter
  - **Files**: demos/sentinel_dark_watch/tests/test_nodes.py
  - **Done when**: 4+ test cases pass
  - **Verify**: `uv run --no-project pytest demos/sentinel_dark_watch/tests/test_nodes.py -v -k "ingest or land" --tb=short`
  - **Commit**: `test(sdw): unit tests for SARIngestNode + LandMaskFilterNode`
  - _Requirements: AC-2.4, FR-19_

- [ ] 3.7 Unit tests for GeoContextNode + ReportingNode
  - **Do**:
    1. GeoContextNode: mock PostGIS + mock DSPy; verify EEZ/port fields populated; LLM fallback produces templated summary
    2. ReportingNode: mock DSPy; verify report_text has required sections; LLM fallback produces structured report
  - **Files**: demos/sentinel_dark_watch/tests/test_nodes.py
  - **Done when**: 4+ test cases pass
  - **Verify**: `uv run --no-project pytest demos/sentinel_dark_watch/tests/test_nodes.py -v -k "geo or report" --tb=short`
  - **Commit**: `test(sdw): unit tests for GeoContextNode + ReportingNode`
  - _Requirements: FR-6, FR-8, AC-5.4, AC-7.1_

- [ ] 3.8 [VERIFY] Quality checkpoint: all unit tests
  - **Do**: Run full test suite
  - **Verify**: `uv run --no-project pytest demos/sentinel_dark_watch/tests/ -v --tb=short`
  - **Done when**: All tests pass
  - **Commit**: `chore(sdw): pass quality checkpoint` (if fixes needed)

- [ ] 3.9 Integration test for full pipeline (mock mode)
  - **Do**:
    1. Create `demos/sentinel_dark_watch/tests/test_pipeline.py`
    2. Test: load SdwState with fixture tile queue, mock all external deps (Postgres, ONNX, DSPy), run nodes in sequence, verify: detections produced, AIS correlation attempted, risk scores assigned, metrics collected
    3. This tests the data flow through node chain, not infrastructure
  - **Files**: demos/sentinel_dark_watch/tests/test_pipeline.py
  - **Done when**: Pipeline integration test passes
  - **Verify**: `uv run --no-project pytest demos/sentinel_dark_watch/tests/test_pipeline.py -v --tb=short`
  - **Commit**: `test(sdw): integration test for full pipeline mock run`
  - _Design: Test Strategy (Integration Tests)_

- [ ] 3.10 Bootstrap idempotency test
  - **Do**:
    1. Add test to `test_pipeline.py` or new file: mock Postgres connection, call bootstrap DDL twice, assert no errors on second run (CREATE TABLE IF NOT EXISTS, INSERT ON CONFLICT DO NOTHING)
  - **Files**: demos/sentinel_dark_watch/tests/test_pipeline.py
  - **Done when**: Idempotency test passes
  - **Verify**: `uv run --no-project pytest demos/sentinel_dark_watch/tests/test_pipeline.py -v -k "idempotent" --tb=short`
  - **Commit**: `test(sdw): bootstrap idempotency test`
  - _Requirements: FR-12_

- [ ] 3.11 [VERIFY] Quality checkpoint: all tests green
  - **Do**: Run full test suite including integration
  - **Verify**: `uv run --no-project pytest demos/sentinel_dark_watch/tests/ -v --tb=short`
  - **Done when**: All tests pass (unit + integration)
  - **Commit**: `chore(sdw): pass quality checkpoint` (if fixes needed)

## Phase 4: Quality Gates

- [ ] V4 [VERIFY] Full local CI: lint + typecheck + tests
  - **Do**: Run complete local CI suite
  - **Verify**: `uv run ruff check demos/sentinel_dark_watch/ && uv run ruff format --check demos/sentinel_dark_watch/ && uv run --no-project pytest demos/sentinel_dark_watch/tests/ -v --tb=short && echo "LOCAL CI PASS"`
  - **Done when**: Lint clean, format clean, all tests pass
  - **Commit**: `chore(sdw): pass local CI` (if fixes needed)

- [ ] V5 [VERIFY] CI pipeline passes
  - **Do**: Push branch and verify CI
  - **Verify**: `git push -u origin fix/cve-rem-doctrine-extractor 2>/dev/null; gh pr checks 2>/dev/null || echo "CI check pending — verify after PR creation"`
  - **Done when**: CI pipeline passes (or PR not yet created)
  - **Commit**: None

- [ ] V6 [VERIFY] AC checklist
  - **Do**: Programmatically verify each acceptance criterion
    1. AC-1.1: `grep -q "demo:" demos/sentinel_dark_watch/Justfile` (one-command launch)
    2. AC-1.4: `grep -q "demo-offline:" demos/sentinel_dark_watch/Justfile` (offline mode)
    3. AC-1.5: `grep -q "teardown:" demos/sentinel_dark_watch/Justfile` (clean stop)
    4. FR-1: `uv run --no-project python -c "import yaml; d=yaml.safe_load(open('demos/sentinel_dark_watch/graph/harbor.yaml')); assert len(d['nodes'])>=14"` (graph with 14+ nodes)
    5. FR-2: `uv run --no-project python -c "from demos.sentinel_dark_watch.graph.state import SdwState; s=SdwState(); assert hasattr(s,'detections')"` (state model)
    6. AC-6.1/6.2: `grep -q "risk_score" demos/sentinel_dark_watch/graph/nodes.py` (risk scoring)
    7. AC-8.1: `grep -q "folium" demos/sentinel_dark_watch/ui/app.py` (map view)
    8. FR-10: `test -f demos/sentinel_dark_watch/graph/retrain.yaml` (retrain sub-graph)
    9. FR-13: `test -f demos/sentinel_dark_watch/docker-compose.yml` (docker compose)
  - **Verify**: `grep -q "demo:" demos/sentinel_dark_watch/Justfile && uv run --no-project python -c "import yaml; d=yaml.safe_load(open('demos/sentinel_dark_watch/graph/harbor.yaml')); assert len(d['nodes'])>=14; print('AC PASS')" && test -f demos/sentinel_dark_watch/graph/retrain.yaml && test -f demos/sentinel_dark_watch/docker-compose.yml && echo "ALL ACs VERIFIED"`
  - **Done when**: All acceptance criteria confirmed via automated checks
  - **Commit**: None

- [ ] VE1 [VERIFY] E2E startup: docker compose + bootstrap + harbor serve
  - **Do**:
    1. Start Docker: `docker compose -f demos/sentinel_dark_watch/docker-compose.yml up -d`
    2. Record compose PID context: `echo "sdw-docker" > /tmp/ve-pids.txt`
    3. Wait for PostGIS ready (60s timeout): `for i in $(seq 1 60); do pg_isready -h localhost -p 5441 -U harbor 2>/dev/null && break || sleep 1; done`
    4. Run bootstrap: `uv run --no-project python -m demos.sentinel_dark_watch.bootstrap`
    5. Run AIS ingest mock: `AIS_MODE=mock uv run --no-project python -m demos.sentinel_dark_watch.ais_ingest`
    6. Start harbor serve in background: `uv run --no-project python -m demos.sentinel_dark_watch.serve_sdw --port 9001 & echo $! >> /tmp/ve-pids.txt`
    7. Wait for serve ready: `for i in $(seq 1 60); do curl -sf http://localhost:9001/health && break || sleep 1; done`
  - **Verify**: `curl -sf http://localhost:9001/health && echo VE1_PASS`
  - **Done when**: Docker running, bootstrap complete, harbor serve responding on 9001
  - **Commit**: None

- [ ] VE2 [VERIFY] E2E check: pipeline health + streamlit loads
  - **Do**:
    1. Verify harbor serve health: `curl -sf http://localhost:9001/health`
    2. Start Streamlit in background: `uv run --no-project streamlit run demos/sentinel_dark_watch/ui/app.py --server.port 8501 --server.headless true & echo $! >> /tmp/ve-pids.txt`
    3. Wait for Streamlit: `for i in $(seq 1 30); do curl -sf http://localhost:8501/_stcore/health && break || sleep 1; done`
    4. Verify Streamlit responds: `curl -sf http://localhost:8501/_stcore/health`
  - **Verify**: `curl -sf http://localhost:9001/health && curl -sf http://localhost:8501/_stcore/health && echo VE2_PASS`
  - **Done when**: Both harbor serve and Streamlit responding
  - **Commit**: None

- [ ] VE3 [VERIFY] E2E cleanup: stop all services and free ports
  - **Do**:
    1. Kill PIDs: `cat /tmp/ve-pids.txt 2>/dev/null | grep -v sdw-docker | xargs -r kill 2>/dev/null; sleep 2; cat /tmp/ve-pids.txt 2>/dev/null | grep -v sdw-docker | xargs -r kill -9 2>/dev/null || true`
    2. Kill by port (serve): `lsof -ti :9001 | xargs -r kill 2>/dev/null || true`
    3. Kill by port (streamlit): `lsof -ti :8501 | xargs -r kill 2>/dev/null || true`
    4. Docker down: `docker compose -f demos/sentinel_dark_watch/docker-compose.yml down -v`
    5. Clean PID file: `rm -f /tmp/ve-pids.txt`
    6. Verify ports free: `! lsof -ti :9001 && ! lsof -ti :8501`
  - **Verify**: `! lsof -ti :9001 && ! lsof -ti :8501 && ! lsof -ti :5441 && echo VE3_PASS`
  - **Done when**: No processes on 9001, 8501, 5441; Docker containers removed
  - **Commit**: None

## Phase 5: PR Lifecycle

- [ ] 5.1 Create PR
  - **Do**:
    1. Verify on feature branch: `git branch --show-current`
    2. Stage all new SDW files: `git add demos/sentinel_dark_watch/ pyproject.toml .gitignore`
    3. Push branch: `git push -u origin $(git branch --show-current)`
    4. Create PR: `gh pr create --title "feat(sdw): Sentinel Dark Watch maritime SAR demo" --body "..."`
  - **Verify**: `gh pr view --json state -q '.state' | grep -q OPEN && echo PR_CREATED`
  - **Done when**: PR created and open
  - **Commit**: None (PR creation, not code change)

- [ ] 5.2 CI monitoring loop
  - **Do**:
    1. Check CI status: `gh pr checks`
    2. If failures: read failure details, fix locally, commit, push
    3. Re-check: `gh pr checks`
    4. Repeat until green (max 20 cycles)
  - **Verify**: `gh pr checks 2>&1 | grep -v "fail\|FAIL" | grep -q "pass\|PASS" || echo "CI pending"`
  - **Done when**: All CI checks green
  - **Commit**: `fix(sdw): address CI failures` (if fixes needed)

- [ ] 5.3 Review resolution loop
  - **Do**:
    1. Check for review comments: `gh pr view --json reviews`
    2. Address each comment with code fixes
    3. Push fixes, re-request review
    4. Repeat until approved or no unresolved comments
  - **Verify**: `gh pr view --json reviews -q '.reviews[-1].state' 2>/dev/null | grep -qE "APPROVED|COMMENTED" || echo "no reviews yet"`
  - **Done when**: No unresolved review comments
  - **Commit**: `fix(sdw): address review feedback` (if fixes needed)

- [ ] 5.4 Final validation
  - **Do**:
    1. Verify all Phase 1-4 tasks complete
    2. Verify CI green: `gh pr checks`
    3. Verify zero test regressions: `uv run --no-project pytest demos/sentinel_dark_watch/tests/ -v`
    4. Verify modularity: all nodes in separate classes, shared helpers extracted, no inline SQL
  - **Verify**: `uv run --no-project pytest demos/sentinel_dark_watch/tests/ -v --tb=short && echo "FINAL VALIDATION PASS"`
  - **Done when**: All completion criteria met — PR ready to merge
  - **Commit**: None

## Notes

- **POC shortcuts taken**: YOLO/rasterio/geopandas dependencies gated behind `try/except ImportError` in Phase 1 — nodes return empty results if geo deps missing. Real inference requires `[sdw]` extra installed with GPU.
- **Production TODOs (Phase 2)**: Extract DB pool, add error handling, make weights env-configurable.
- **Deferred to v2**: Neo4j vessel KG, vector embeddings, GFW API, agent eval loop, multi-user RBAC, Kubernetes deployment.
- **Test tile**: A minimal GeoTIFF fixture for CI is needed. POC uses `fixtures/test_tile.tif` (small synthetic or downloaded). If xView3 access is slow, mock the tile loading path.
- **LLM mock vs real**: Phase 1 uses llm-shim Docker container. Real Ollama (localhost:41001) used when available via `LLM_BASE_URL` env var.
- **Retrain sub-graph**: POC implements node stubs. Full YOLO retraining requires `[sdw]` extra with ultralytics + torch. Demo script can be triggered manually via `just retrain`.
