-- Sentinel Dark Watch — DDL
-- Executed by bootstrap.py via Path(__file__).parent / "schema.sql"

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS sar_tiles (
    tile_id       TEXT PRIMARY KEY,
    scene_id      TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    acquired_at   TIMESTAMPTZ NOT NULL,
    bounds        GEOMETRY(POLYGON, 4326) NOT NULL,
    patch_count   INTEGER DEFAULT 0,
    ingested_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sar_tiles_bounds ON sar_tiles USING GIST(bounds);

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
CREATE INDEX IF NOT EXISTS idx_ais_positions_geom ON ais_positions USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_ais_positions_ts ON ais_positions(ts);

CREATE TABLE IF NOT EXISTS eez_boundaries (
    gid           SERIAL PRIMARY KEY,
    mrgid         INTEGER,
    geoname       TEXT NOT NULL,
    sovereign     TEXT,
    geom          GEOMETRY(MULTIPOLYGON, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eez_geom ON eez_boundaries USING GIST(geom);

CREATE TABLE IF NOT EXISTS ports (
    id            SERIAL PRIMARY KEY,
    port_name     TEXT NOT NULL,
    country       TEXT,
    lat           DOUBLE PRECISION NOT NULL,
    lon           DOUBLE PRECISION NOT NULL,
    geom          GEOMETRY(POINT, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED
);
CREATE INDEX IF NOT EXISTS idx_ports_geom ON ports USING GIST(geom);

CREATE TABLE IF NOT EXISTS coastlines (
    gid           SERIAL PRIMARY KEY,
    geom          GEOMETRY(MULTIPOLYGON, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_coastlines_geom ON coastlines USING GIST(geom);

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
CREATE INDEX IF NOT EXISTS idx_detections_geom ON detections USING GIST(geom);

CREATE TABLE IF NOT EXISTS corrections (
    id            SERIAL PRIMARY KEY,
    detection_id  TEXT REFERENCES detections(detection_id),
    run_id        TEXT NOT NULL,
    decision      TEXT NOT NULL,
    override_risk TEXT,
    corrected_at  TIMESTAMPTZ DEFAULT NOW(),
    consumed      BOOLEAN DEFAULT FALSE
);

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
