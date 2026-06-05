---
spec: demo-sentinel-dark-watch-1
phase: research
created: 2026-05-26
---

# Research: demo-sentinel-dark-watch-1

## Executive Summary

Sentinel Dark Watch is a **maritime SAR surveillance pipeline**: Sentinel-1 SAR tiles + AIS vessel tracking in, dark vessel alerts + analyst-reviewed intel reports out. Harbor provides the graph engine (nodes, rules, checkpoints, Fathom governance, `harbor serve`). Nautilus brokers external data (AIS, geo-context, S3 tiles). The ML model (YOLO OBB for SAR vessel detection, trained on xView3) needs **custom nodes** -- Harbor's built-in `MLNode` only supports sklearn/xgboost/onnx, but YOLO can be exported to ONNX for inference. Training uses Ultralytics + PyTorch directly, outside the graph engine.

## Harbor Framework Analysis

### Graph Engine

- **`Graph`** (definition.py): IR-validated, hashable. Constructed from `harbor.yaml` IR YAML. Supports `state_class: "module:ClassName"` for complex Pydantic states.
- **`GraphRun`** (run.py): Single-use execution handle. `await graph.start(state, checkpointer=...)` returns a run.
- **Loop** (loop.py): Walks IR nodes sequentially. Supports routing via Fathom rules (CLIPS-style `when`/`then`), parallel blocks, and cooperative cancel/pause.
- **Checkpointing**: SQLite (default) or Postgres. Per-step snapshots of state.
- **Events**: `TransitionEvent`, `ResultEvent`, `WaitingForInputEvent`. EventBus for streaming.

### Built-in Node Types

| Node | Purpose | Relevance |
|------|---------|-----------|
| `NodeBase` | ABC; `async execute(state, ctx) -> dict` | All custom nodes subclass this |
| `DSPyNode` | Wraps DSPy modules for LLM calls | Geo-context agent, reporting agent, AIS correlation agent |
| `MLNode` | sklearn/xgboost/onnx inference | **Cannot** handle YOLO/PyTorch directly; **can** handle ONNX-exported YOLO |
| `BrokerNode` | Nautilus broker requests | AIS stream, SAR tile metadata, geo-context lookups |
| `InterruptNode` | HITL durable wait | Analyst review gates |
| `SubGraphNode` | Child node sequence in parent run | Training sub-graph, sandbox validation |
| `WriteArtifactNode` | Emit ArtifactRef | Save detection chips, reports |
| `EchoNode` | Test fixture | N/A |

### ML Infrastructure

- **MLNode**: Supports `Runtime = Literal["sklearn", "xgboost", "onnx"]`. ONNX uses `InferenceSession` with CPU EP, cached per `(model_id, version)`.
- **ModelRegistry** (SQLite): `register() -> alias() -> load()` with SHA-256 hash verification. Supports champion/challenger via aliases (`production`, `staging`).
- **Key insight**: YOLO models can be **exported to ONNX** (`model.export(format="onnx")`), then loaded by `MLNode` for inference. Training must happen outside the graph (custom script or SubGraphNode wrapping a training script).
- **Alternative**: Write a custom `YOLONode(NodeBase)` that uses Ultralytics API directly. Simpler for OBB post-processing.

### Store Types

| Store | Backing | Use Case |
|-------|---------|----------|
| `SQLiteCheckpointer` | SQLite | Run state checkpoints |
| `FilesystemArtifactStore` | Disk | Detection chips, reports |
| `VectorStore` (lancedb/pgvector) | pgvector | Embedding search (not needed in v1) |
| `GraphStore` (ryugraph) | RyuGraph/Neo4j | Knowledge graphs (vessel patterns?) |
| `FactStore` | SQLite | Structured facts |
| `MemoryStore` | SQLite/Redis | Agent memory |

### Serving

- `harbor serve` starts FastAPI app with WebSocket streaming, REST API (`POST /v1/runs`, `GET /v1/runs/{id}/stream`).
- CVE-rem wraps this in `serve_cve_rem.py` to add capability profile + watcher UI mount + per-run JSONL audit.
- Pattern: demo provides its own `serve_sdw.py` that mirrors `serve_cve_rem.py`.

## Nautilus Framework Analysis

### Architecture

Nautilus is a **data broker**: agents request data via natural-language intents, Nautilus routes to appropriate sources via rule-based analysis, enforces classification/scope constraints, and returns structured `AdapterResult`s with attestation.

### Adapter Types (8 total)

| Adapter | Type Key | Relevant? | Use Case |
|---------|----------|-----------|----------|
| `RestAdapter` | `rest` | **Yes** | AISStream.io WebSocket (wrapped), Marine Regions WFS, GFW API |
| `S3Adapter` | `s3` | **Yes** | Sentinel-1 GRD tiles on AWS Open Data (`s3://sentinel-s1-l1c/`) |
| `PostgresAdapter` | `postgres` | **Yes** | Detection results store, audit chain |
| `PgvectorAdapter` | `pgvector` | Maybe | Embedding store for vessel pattern matching |
| `Neo4jAdapter` | `neo4j` | Maybe | Vessel knowledge graph |
| `ElasticsearchAdapter` | `elasticsearch` | No | |
| `InfluxDBAdapter` | `influxdb` | Maybe | Time-series AIS positions |
| `ServiceNowAdapter` | `servicenow` | No | |

### Source Config Pattern

Each source in `nautilus.yaml` declares: `id`, `type`, `description`, `classification`, `data_types`, `allowed_purposes`, `connection`, `auth`. Env vars interpolated via `${VAR}`.

### Key Consideration

Nautilus adapters are **query/response**, not streaming. AISStream.io is a WebSocket stream. Options:
1. **Custom ingest node** that connects to AIS WebSocket directly (outside Nautilus).
2. **REST adapter** hitting AISStream.io's HTTP API (if available) for recent positions.
3. **Buffer pattern**: Custom daemon writes AIS messages to Postgres/Redis; Nautilus `postgres` adapter queries the buffer.

**Recommendation**: Option 3 (buffer pattern). Separate AIS ingest daemon writes to Postgres table. Nautilus queries it. Keeps the graph reactive, not streaming.

## CVE-Rem Demo Patterns (Structural Reference)

### Directory Layout

```
demos/cve_remediation/
  .env / .env.example          # env vars
  docker-compose.yml           # postgres, pgvector, redis, neo4j, mock-servicenow, llm-shim
  bootstrap.py                 # idempotent: wait health, provision schemas, seed data
  nautilus.yaml                # broker source configs
  capabilities.py              # engine-side capability profile
  serve_cve_rem.py             # harbor serve wrapper + watcher mount
  graph/
    harbor.yaml                # IR graph definition (nodes, rules)
    state.py                   # Pydantic BaseModel state class
    nodes.py                   # stub nodes
    real_nodes.py              # real node implementations
    rules/                     # Fathom rule packs
    phase0/ phase6/            # auxiliary graphs
    subgraphs/                 # sub-graph IR YAMLs
    triggered/                 # triggered graphs (drift_watch, etc.)
  fixtures/                    # test data
  mocks/                       # mock services (llm-shim, servicenow)
  tools/                       # custom tool definitions
  watcher/                     # run-watcher UI (React+Babel)
```

### Key Patterns to Follow

1. **`state_class`** in harbor.yaml: `state_class: "demos.sentinel_dark_watch.graph.state:SdwState"` — Pydantic BaseModel with StrEnum enums, flat top-level attrs, sub-models as values.
2. **Node `kind`** format: `"demos.sentinel_dark_watch.graph.nodes:NodeClassName"` — Python import path.
3. **bootstrap.py**: `_wait_tcp()` + `_wait_http_health()` for Docker health; then SQL schema provisioning; then seed data.
4. **Run via uv**: `uv run --no-project python -m demos.sentinel_dark_watch.bootstrap` — no separate pyproject.toml.
5. **Port allocation**: CVE-rem uses 5439/5440/6390/7687/8089/41001/9000. SDW should offset to avoid collision.

## Existing Demo Directory State

```
demos/sentinel-dark-watch/
  .env                         # AIS_STREAM_API_KEY=933205213e3365decec22f0b3ea6bb439e3d2684
  README.md                    # Spec document (maritime SAR pipeline description)
  sdw-graph.md                 # Empty file (1 line, no content)
```

The `.env` already has the AISStream.io API key. Everything else needs to be built from scratch.

**Note**: Directory uses hyphens (`sentinel-dark-watch`), but Python module must use underscores (`sentinel_dark_watch`). Follow CVE-rem pattern: `demos/cve_remediation/` (underscores in Python-facing directory name). Either rename the directory or use a symlink. CVE-rem uses underscores throughout.

## xView3 Dataset

### Overview

- **Source**: [iuu.xview.us](https://iuu.xview.us/)
- **Size**: ~1,000 Sentinel-1 scenes, 243,018 labeled maritime objects, 43.2M km^2
- **Image size**: Average 29,400 x 24,400 pixels per scene (huge — needs tiling)
- **Format**: GeoTIFF (VH_dB.tif, VV_dB.tif per scene) + ancillary rasters (bathymetry, wind speed/direction, land/ice masks)
- **Labels**: CSV files (train.csv, valid.csv)

### Label Schema (CSV columns)

| Column | Type | Description |
|--------|------|-------------|
| `detect_scene_row` | int | Pixel row in scene |
| `detect_scene_column` | int | Pixel column in scene |
| `detect_lat` | float | Latitude |
| `detect_lon` | float | Longitude |
| `is_vessel` | bool | True for vessels, False for non-vessels |
| `is_fishing` | bool | Fishing vessel classification |
| `vessel_length_m` | float | Vessel length in meters |
| `confidence` | str | "HIGH" or "MEDIUM" |
| `source` | str | Detection source (AIS-matched, manual, automated) |

### Subset Strategy for Demo

Full dataset is ~300 GB. For demo:
1. Download 10-20 scenes from a single region (e.g., South China Sea or Strait of Hormuz).
2. Tile each scene into 640x640 or 800x800 patches.
3. Convert CSV point labels to YOLO OBB format (oriented bounding box annotations).
4. This gives ~500-2000 training patches — enough for fine-tuning a pre-trained model.

### Reference Implementations

- [AllenAI sar_vessel_detect](https://github.com/allenai/sar_vessel_detect) — Faster-RCNN, PyTorch, xView3 1st/3rd place solutions
- [xView3 reference](https://github.com/DIUx-xView/xview3-reference) — Official reference implementation
- [SARFish on HuggingFace](https://huggingface.co/datasets/ConnorLuckettDSTG/SARFish) — Alternative SAR vessel dataset

## SAR Object Detection Models

### Approach: Ultralytics YOLO OBB

**Why YOLO OBB over Faster-RCNN:**
- Vessels in SAR are elongated, rotated. OBB fits them tighter than axis-aligned boxes.
- Ultralytics YOLO11/YOLO26 has built-in OBB support (`yolo11n-obb.pt`).
- One-line export to ONNX: `model.export(format="onnx")`.
- ONNX model can be loaded by Harbor's `MLNode` or a custom `YOLONode`.

**Training pipeline:**
1. Convert xView3 CSV labels to YOLO OBB format (4 corner points, normalized).
2. Fine-tune `yolo11s-obb.pt` (pre-trained on DOTAv1) on xView3 subset.
3. Export best checkpoint to ONNX.
4. Register in Harbor `ModelRegistry` with SHA-256 hash.

**Recent SAR-specific YOLO variants (2024-2025):**
- AC-YOLO (YOLO11-based): 30% fewer params, 1.2% higher AP on SSDD
- CV-YOLO: Anchor-free OBB for SAR, built on Ultralytics 8.1.30
- MC-ASFF-ShipYOLO: YOLO11 with multi-scale feature fusion

**Recommendation**: Start with stock `yolo11s-obb` fine-tuned on xView3. If performance is insufficient, try SAR-specific architectures.

### ONNX Export Path

```python
from ultralytics import YOLO
model = YOLO("best.pt")  # trained checkpoint
model.export(format="onnx")  # -> best.onnx
# Can be loaded by Harbor MLNode(runtime="onnx") or onnxruntime directly
```

**Caveat**: YOLO OBB output requires custom post-processing (NMS, coordinate transform). A custom `YOLOInferenceNode(NodeBase)` is cleaner than trying to shoehorn into `MLNode`'s simple `predict()` API.

## AISStream.io Integration

### WebSocket API

- **Endpoint**: `wss://stream.aisstream.io/v0/stream`
- **Auth**: API key in subscription message
- **Subscription message** (JSON):
  ```json
  {
    "Apikey": "<key>",
    "BoundingBoxes": [
      [[lat_min, lon_min], [lat_max, lon_max]]
    ],
    "FiltersShipMMSI": [],
    "FilterMessageTypes": ["PositionReport"]
  }
  ```
- **Response**: Streaming JSON messages with AIS position reports (MMSI, lat, lon, speed, heading, timestamp, ship name, etc.)

### Integration Pattern

Since Nautilus adapters are request/response (not streaming), use a **buffer daemon**:

1. `ais_ingest.py` — standalone asyncio daemon that connects to AIS WebSocket, writes positions to Postgres table `ais_positions` (MMSI, lat, lon, speed, heading, ts, ship_name).
2. Nautilus `postgres` source queries `ais_positions` table for positions within a bounding box and time window.
3. AIS correlation node in the graph queries via BrokerNode.

**Alternative (simpler v1)**: Skip real-time AIS streaming. Pre-load historical AIS data from NOAA or use a small fixture set. Correlate against detection results offline.

### API Key

Already provisioned: `AIS_STREAM_API_KEY=933205213e3365decec22f0b3ea6bb439e3d2684` in `.env`.

## Sentinel-1 Data Access

### AWS Open Data (Primary)

- **Bucket**: `s3://sentinel-s1-l1c/` (eu-central-1)
- **Access**: No AWS account needed (`--no-sign-request`)
- **Format**: GRD (Ground Range Detected) tiles, organized by date/mode/polarization
- **STAC endpoint**: Available for catalog queries
- **CLI**: `aws s3 ls --no-sign-request s3://sentinel-s1-l1c/GRD/`

### Copernicus Data Space (Alternative)

- **API**: OData + S3-compatible object storage
- **Python client**: `cdse-client` (modern replacement for `sentinelsat`)
- **Process API**: POST request with bounding box + time range, returns processed data

### Integration Pattern

For the demo, two modes:
1. **Bootstrap**: Download a small set of scenes for training data (scripted, one-time).
2. **Operational**: Query STAC/OData for new scenes over the AOI, download GRD tiles, feed into detection pipeline.

Nautilus `s3` adapter can list/fetch from `sentinel-s1-l1c` bucket. Custom ingest node handles the STAC query + tile download + preprocessing.

## Geo-Context Data Sources

### EEZ Boundaries (Marine Regions)

- **WFS endpoint**: `https://geo.vliz.be/geoserver/MarineRegions/wfs`
- **Layer**: `eez_boundaries` (or `eez` for full polygons)
- **Format**: GeoJSON, shapefile
- **Python**: `geopandas.read_file(wfs_url)` with OGC filter parameters
- **Strategy**: Download EEZ shapefile at bootstrap, load into PostGIS or in-memory GeoDataFrame. Point-in-polygon queries for detections.

### Global Fishing Watch

- **APIs**: Vessel API, Events API, 4Wings API (fishing effort heatmap, SAR detections)
- **Access**: Free for non-commercial use, requires API key registration
- **Relevant data**: Known fishing vessel identities, fishing effort patterns, SAR-detected vessel positions
- **Python client**: [gfw-api-python-client](https://github.com/GlobalFishingWatch/gfw-api-python-client)

### Port Data

- **OpenStreetMap**: `osmnx` library can extract port/harbor locations
- **World Port Index**: US NGA database, downloadable CSV
- **Strategy**: Pre-load port locations at bootstrap. Calculate distance-to-nearest-port for each detection.

### GSHHG Coastlines

- **Source**: NOAA/NGDC shoreline data
- **Purpose**: Filter land-based false positives from SAR detections
- **Format**: Shapefile
- **Strategy**: Load at bootstrap, use for land masking during preprocessing.

### Nautilus Integration

- `rest` adapter for Marine Regions WFS + GFW API queries
- `postgres` adapter for pre-loaded EEZ/port/coastline data in PostGIS

## ML Pipeline Design

### Training Flow (Outside Graph)

```
scripts/train_detector.py:
  1. Load xView3 subset (GeoTIFF tiles + CSV labels)
  2. Convert labels to YOLO OBB format
  3. Tile scenes into 640x640 patches
  4. Fine-tune yolo11s-obb on patches
  5. Export best.pt -> best.onnx
  6. Register in ModelRegistry (model_id, version, sha256)
  7. Set alias "production" -> latest version
```

### Inference Flow (Inside Graph)

```
graph node: YOLOInferenceNode(NodeBase)
  1. Read SAR tile path from state
  2. Tile into patches
  3. Run ONNX inference per patch (onnxruntime)
  4. Post-process: NMS, coordinate transform back to geo-coords
  5. Write detections list to state
```

### Champion/Challenger (Auto-Retrain)

Harbor `ModelRegistry` supports this natively:
1. Nightly cron trigger fires a training sub-graph.
2. New model evaluated on holdout set.
3. If metrics exceed champion: promote to `production` alias.
4. If not: keep current champion, log challenger metrics.

### Model Format Decision

**ONNX recommended** over raw PyTorch for inference:
- Harbor `MLNode` already handles ONNX sessions (cached, thread-safe)
- 43% faster inference than PyTorch (per Ultralytics docs)
- CPU-only deployment (no CUDA dependency in production)
- SHA-256 hash verification via ModelRegistry

## Streamlit HITL UI

### Design

Analyst review dashboard with:
1. **Map view** (Folium/pydeck): SAR tile footprint, detection markers (color-coded by risk), AIS tracks
2. **Detection detail panel**: SAR chip (cropped patch around detection), confidence score, AIS correlation status, geo-context summary
3. **Action buttons**: Confirm vessel / Reject (false positive) / Flag for review / Assign risk level
4. **Report view**: Generated intel report (editable before submission)

### Libraries

| Library | Purpose |
|---------|---------|
| `streamlit` | App framework |
| `streamlit-folium` | Map rendering (Leaflet.js backend) |
| `Pillow` / `rasterio` | SAR image display |
| `folium` | Map markers, layers, popups |
| `geopandas` | Geo data handling |

### Integration with Harbor

Streamlit app communicates with `harbor serve` API:
- `POST /v1/runs` to trigger pipeline for new SAR tile
- `GET /v1/runs/{id}/stream` WebSocket for live progress
- `POST /v1/runs/{id}/respond` to submit analyst decisions (InterruptNode resume)
- Custom API routes for detection queries, map data

## Infrastructure (Docker Compose)

### Services Needed

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| postgres | postgres:16-alpine | 5441 | Detection results, AIS buffer, audit chain, EEZ/port data |
| pgvector | pgvector/pgvector:pg16 | 5442 | Embedding store (vessel pattern matching, future) |
| redis | redis:7-alpine | 6391 | AIS position cache, rate limiting |
| llm-shim | custom (or omit) | 41001 | Mock LLM or point at real Ollama |

**Not needed initially**: Neo4j (vessel knowledge graph can wait for v2).

### Port Allocation (offset from CVE-rem)

| Service | CVE-rem | SDW |
|---------|---------|-----|
| postgres | 5439 | 5441 |
| pgvector | 5440 | 5442 |
| redis | 6390 | 6391 |
| harbor serve | 9000 | 9001 |
| llm | 41001 | 41001 (shared) |

## Package Management (uv)

### Existing Pattern

- Monorepo `pyproject.toml` declares all deps.
- Demo code runs as `uv run --no-project python -m demos.<module>`.
- No separate pyproject.toml per demo.
- Extra deps (ultralytics, rasterio, geopandas, streamlit) need to be declared somewhere.

### SDW-Specific Dependencies

| Package | Purpose | Size |
|---------|---------|------|
| `ultralytics` | YOLO training + export | ~50 MB |
| `torch` + `torchvision` | YOLO training backend | ~200 MB (CPU) |
| `rasterio` | GeoTIFF I/O | ~20 MB |
| `geopandas` | Geo data handling | ~15 MB |
| `shapely` | Geometry operations | ~10 MB |
| `streamlit` | HITL UI | ~30 MB |
| `streamlit-folium` | Map viz | ~1 MB |
| `folium` | Leaflet maps | ~5 MB |
| `websockets` | AIS WebSocket client | ~1 MB |
| `Pillow` | Image processing | ~10 MB |
| `onnxruntime` | ONNX inference | Already in `[ml]` extra |

**Approach**: Add an `[sdw]` optional dependency group to root `pyproject.toml`, or create a `demos/sentinel_dark_watch/requirements.txt` for uv to install into the demo's venv.

## Quality Commands

| Type | Command | Source |
|------|---------|--------|
| Lint | `make lint` / `uv run ruff check src/ tests/` | Makefile |
| TypeCheck | `make typecheck` / `uv run pyright` | Makefile |
| Unit Test | `make test` / `uv run pytest -m unit` | Makefile |
| All Tests | `make test-all` / `uv run pytest` | Makefile |
| Build | Not applicable (library, not built artifact) | - |

**Local CI**: `make lint && make typecheck && make test`

## Verification Tooling

| Tool | Command | Detected From |
|------|---------|---------------|
| Dev Server | `uv run --no-project python -m demos.sentinel_dark_watch.serve_sdw` | CVE-rem pattern |
| Browser Automation | `playwright` | devDependencies (cve-rem watcher tests) |
| E2E Config | `demos/cve_remediation/watcher/tests/playwright.config.ts` | project structure |
| Port | `9001` | convention (offset from CVE-rem 9000) |
| Docker | `docker-compose.yml` | demo directory |

**Project Type**: Web App (FastAPI backend + Streamlit HITL UI)
**Verification Strategy**: Start docker-compose, run bootstrap.py, start harbor serve, run Streamlit, verify via curl + Playwright

## Related Specs

| Spec | Relevance | mayNeedUpdate |
|------|-----------|---------------|
| cve-rem-node-ui | Low — different domain (CVE remediation watcher UI), but shares run-watcher patterns | No |
| harbor-engine | Medium — relies on graph engine, MLNode, SubGraphNode | No |
| harbor-serve-and-bosun | Medium — relies on harbor serve API, scheduler, triggers | No |
| harbor-knowledge | Low — may use stores (vector, fact) in future | No |

## Recommendations

### Implementation Approach

1. **Phase 1 (Foundation)**:
   - Create `demos/sentinel_dark_watch/` directory structure (underscore, Python-compatible)
   - `docker-compose.yml` with postgres + redis
   - `bootstrap.py` — provision schemas, download small xView3 subset, pre-load EEZ/port data
   - `state.py` — `SdwState` Pydantic model
   - `harbor.yaml` — graph IR definition

2. **Phase 2 (ML Pipeline)**:
   - `scripts/prepare_dataset.py` — download xView3 subset, tile, convert labels to YOLO OBB
   - `scripts/train_detector.py` — fine-tune YOLO11-OBB, export ONNX, register model
   - `graph/nodes.py` — `YOLOInferenceNode`, `AISCorrelationNode`, `GeoContextNode`, `ReportingNode`

3. **Phase 3 (Integration)**:
   - `nautilus.yaml` — sources for AIS buffer, SAR metadata, geo-context
   - `ais_ingest.py` — AIS WebSocket daemon (writes to Postgres)
   - `serve_sdw.py` — harbor serve wrapper
   - `capabilities.py` — engine-side capability profile

4. **Phase 4 (HITL UI)**:
   - `ui/app.py` — Streamlit analyst dashboard
   - Map view, detection review, report editing
   - Integration with harbor serve API (respond to InterruptNodes)

5. **Phase 5 (Self-Improvement)**:
   - Cron-triggered nightly retrain sub-graph
   - Champion/challenger evaluation gate
   - Active learning queue (low-confidence detections routed to analyst)

### Simplifications for v1

- Skip SAR tile download automation — pre-download a small set during bootstrap
- Focus on the **detection -> correlation -> risk scoring -> report** pipeline being real
- UI can be basic Streamlit with map + table
- AIS: live AISStream.io with mock fallback (decided)
- Auto-retrain: include in pipeline but nightly cron, not real-time

## Resolved Decisions

1. **xView3 download**: User registered at iuu.xview.us. Use real xView3 data, small subset.
2. **Training hardware**: Local GPU available. Fine-tune YOLO locally.
3. **AIS approach**: Live AISStream.io websocket + mock fallback for offline. API key in `.env`.
4. **Region of interest**: Strait of Hormuz.
5. **Directory rename**: Yes — rename to `demos/sentinel_dark_watch/` (underscores).
6. **LLM provider**: Ollama default (`localhost:41001`) + llm-shim mock for offline/CI.
7. **Streamlit port**: 8501 (default, no conflicts).

## Sources

- [xView3-SAR Dataset](https://iuu.xview.us/) — primary training data
- [xView3 NeurIPS Paper (2022)](https://proceedings.neurips.cc/paper_files/paper/2022/file/f4d4a021f9051a6c18183b059117e8b5-Paper-Datasets_and_Benchmarks.pdf)
- [AllenAI sar_vessel_detect](https://github.com/allenai/sar_vessel_detect) — reference Faster-RCNN model
- [Ultralytics YOLO OBB Docs](https://docs.ultralytics.com/tasks/obb) — oriented bounding box detection
- [Ultralytics ONNX Export](https://docs.ultralytics.com/integrations/onnx) — model export
- [AISStream.io Documentation](https://aisstream.io/documentation) — WebSocket API
- [Sentinel-1 on AWS](https://registry.opendata.aws/sentinel-1/) — S3 bucket access
- [Copernicus Data Space](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/S1GRD.html) — Sentinel-1 GRD
- [Marine Regions WFS](https://www.marineregions.org/webservices.php) — EEZ boundaries
- [Global Fishing Watch APIs](https://globalfishingwatch.org/our-apis/) — vessel tracking data
- [streamlit-folium](https://folium.streamlit.app/) — map visualization
- Harbor source: `/home/sean/leagues/harbor/src/harbor/nodes/` — node types
- Harbor source: `/home/sean/leagues/harbor/src/harbor/ml/` — ML infrastructure
- Harbor source: `/home/sean/leagues/harbor/src/harbor/graph/` — graph engine
- CVE-rem demo: `/home/sean/leagues/harbor/demos/cve_remediation/` — structural reference
- Nautilus source: `/home/sean/leagues/nautilus/nautilus/adapters/` — adapter types
