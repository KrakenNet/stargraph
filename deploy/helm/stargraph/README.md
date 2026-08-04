# stargraph Helm chart

Minimal chart running `stargraph serve` as a **single-replica StatefulSet**
with a PVC for embedded state (SQLite checkpointer + JSONL audit log) and a
ClusterIP Service.

```bash
helm install stargraph deploy/helm/stargraph \
  --set image.repository=<your-registry>/stargraph \
  --set image.tag=0.5.2
```

Probes hit `GET /health`; `GET /metrics` serves Prometheus text exposition.

## Single-replica is the supported topology

`replicas: 1` is pinned in the template, not exposed as a value. This is
deliberate, not a missing feature: Stargraph's audit chain is a single
fsync'd hash-chained JSONL writer, and replay determinism requires one
process per state volume (the single-process invariant, locked Decision #5 —
see `docs/guides/air-gap-deployment.md` §4). Two replicas writing one chain
would corrupt it; two replicas with separate chains are two deployments.

**Horizontal scaling is explicitly out of scope.** There is no worker pool,
no HPA, and no distributed executor. If you need more capacity, install
additional independent releases (each gets its own PVC, checkpointer, and
audit chain) and partition work upstream.

## Values

| Key | Default | Description |
|---|---|---|
| `image.repository` | `stargraph` | Image repository (build from the repo-root `Dockerfile`). |
| `image.tag` | `""` (chart appVersion) | Image tag. |
| `image.pullPolicy` | `IfNotPresent` | Pull policy. |
| `profile` | `oss-default` | `stargraph serve --profile` value (`oss-default` \| `cleared`). |
| `service.type` | `ClusterIP` | Service type. |
| `service.port` | `8000` | HTTP port. |
| `persistence.size` | `1Gi` | PVC size for `/data`. |
| `persistence.storageClassName` | `""` | StorageClass (empty = cluster default). Must be POSIX-local-backed storage — the checkpointer refuses NFS/SMB. |
| `resources` | `{}` | Container resources. |
| `extraEnv` | `[]` | Extra env vars for the container. |
