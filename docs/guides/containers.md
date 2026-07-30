# Containers — Docker, Compose, and Helm

**Audience**: Operators running `stargraph serve` in a containerized
environment.
**Companion doc**: [Air-gap deployment](air-gap-deployment.md) — the
wheelhouse recipe and the single-process invariant this page builds on.

Stargraph ships three deployment artifacts at the repo root / `deploy/`:

| Artifact | Path | What it does |
|---|---|---|
| Image | `Dockerfile` | Multi-stage build (uv-locked builder → slim runtime), non-root, runs `stargraph serve` |
| Compose | `compose.yaml` | Single service with a named volume for `/data` |
| Helm chart | `deploy/helm/stargraph/` | Single-replica StatefulSet + PVC + Service |

All three run **one serve process per state volume**. That is the supported
topology — the audit chain is a single fsync'd hash-chained writer and replay
determinism requires it (see the air-gap guide §4). There is no worker pool
and no HPA; scale by running additional independent instances.

## Build the image

```bash
docker build -t stargraph .
```

The builder stage resolves dependencies from the committed `uv.lock`
(`uv sync --frozen --no-dev --no-editable`) and the runtime stage is
`python:3.13-slim` with a non-root `stargraph` user (uid 1000). State
(SQLite checkpointer + JSONL audit log) lives under `/data`.

**Air-gapped build**: stage a wheelhouse per the
[air-gap guide §1](air-gap-deployment.md), drop it at `./wheelhouse/` in the
build context, and build with `--network=none`. When `wheelhouse/*.whl` is
present the builder installs with `pip --no-index --find-links wheelhouse`
and never reaches the network.

## Run with Compose

```bash
docker compose up --build
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/metrics
```

State persists in the `stargraph-state` named volume.

## Install with Helm

```bash
helm install stargraph deploy/helm/stargraph \
  --set image.repository=<your-registry>/stargraph \
  --set image.tag=<tag>
```

The chart deploys a StatefulSet pinned to `replicas: 1` (deliberately not a
value — see the [chart README](https://github.com/KrakenNet/stargraph/tree/main/deploy/helm/stargraph)
for the rationale), a PVC for `/data`, and a ClusterIP Service. Liveness and
readiness probes hit `GET /health`. The PVC's StorageClass must be backed by
POSIX-local storage — the checkpointer refuses NFS/SMB at bootstrap.

## Ops endpoints

`stargraph serve` exposes two operational endpoints:

- `GET /health` — per-component readiness (store probe via a real
  run-history query, CLIPS engine construction, artifact store, plugin
  registry) plus an overall status; `200` when healthy, `503` when any
  probe errors. Ungated so probes need no credentials.
- `GET /metrics` — Prometheus text exposition (no client library):
  runs by status, run-duration summary, audit-chain height, and
  rule-transition count. Gated on the `metrics:read` capability —
  permissive under the OSS-default profile, default-deny under the
  cleared profile.

## What this does not give you

Honest limits, unchanged by containerization:

- No horizontal scaling, distributed executor, or worker pool.
- No OpenTelemetry export or tracing dashboard (metrics are
  Prometheus-scrape only).
- No managed runtime or one-command cloud deploy.
