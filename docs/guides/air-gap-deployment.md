# Air-Gap Deployment Guide — Stargraph v1

**Audience**: Cleared-mode operators deploying Stargraph to a network-isolated
environment.
**Spec ref**: `stargraph-serve-and-bosun` §12.3, §17 Decision #5 (FR-66, AC-4.3,
AC-4.4, NFR-6).
**Companion doc**: `docs/knowledge/air-gap.md` (knowledge-stack offline staging).

This guide covers the **end-to-end** air-gap workflow: bundling the
Python wheelhouse, pre-staging embedding-model weights, terminating
mTLS at the edge, the single-process invariant, the POSIX-local
filesystem requirement, and UTC timezone recommendations.

## Why air-gap?

Cleared deployments operate behind one-way diodes or fully isolated
LANs. Stargraph's design pins zero outbound dependencies at run time:

- **Zero outbound HTTP** at run time once the wheelhouse + embedding
  weights are staged. Audit, replay, and HITL all stay local.
- **Single-process invariant** (locked Decision #5) — no multi-worker
  fan-out that would require external coordination.
- **POSIX-local-only state** — the SQLite checkpointer + JSONL audit
  sink refuse to start on NFS/SMB/AFP (task 3.31).

## 1. Wheelhouse build

The wheelhouse is the offline pip-install bundle. Build it on a
network-attached host once; ship the directory across the air-gap as
part of your release artifact.

```bash
# On the build host (network-attached):
cd /path/to/stargraph
uv lock                                    # pin transitive versions
uv pip wheel \
    --only-binary=:all: \
    -r <(uv export) \
    -d ./wheelhouse/

# Optional: include stargraph itself as a wheel in the bundle.
uv build --wheel
cp dist/stargraph-*.whl ./wheelhouse/
```

The `--only-binary=:all:` flag refuses any sdists; sdist-only deps
must be vendored or replaced. The `uv export` substitution emits the
locked requirements; `uv pip wheel` materializes each as a `.whl`.

**Bundle layout**:

```
release-bundle/
├── wheelhouse/                 # all .whl files
├── models/
│   └── all-MiniLM-L6-v2/      # HF snapshot (see §2)
├── hf_manifest.sha256          # weights pinning manifest
├── stargraph.toml                 # operator config (mTLS paths, etc.)
└── README.md                   # operator runbook excerpt
```

**Install on the air-gapped host**:

```bash
uv pip install \
    --no-index \
    --find-links ./wheelhouse/ \
    stargraph
```

`--no-index` blocks PyPI lookups; `--find-links` points at the local
wheelhouse. Any missing transitive triggers a clear `No matching
distribution` error rather than a silent network reach-out.

## 2. Embedding-model weights pinning

Stargraph's knowledge stack uses `sentence-transformers/all-MiniLM-L6-v2`
by default (384-dim, symmetric). Pre-stage the weights on the build
host, compute SHA-256 hashes, ship as part of the bundle.

```bash
# On the build host:
huggingface-cli download \
    sentence-transformers/all-MiniLM-L6-v2 \
    --local-dir ./models/all-MiniLM-L6-v2

# Compute manifest:
cd models/all-MiniLM-L6-v2
find . -type f -exec sha256sum {} \; > ../../hf_manifest.sha256
```

**On the air-gapped host**:

```bash
export HF_HUB_OFFLINE=1
export HF_HOME=/srv/stargraph/models   # contains all-MiniLM-L6-v2/

# Verify manifest before serve start:
cd $HF_HOME
sha256sum -c /path/to/hf_manifest.sha256
```

`MiniLMEmbedder` honors `HF_HUB_OFFLINE=1` and refuses any download
attempt; a missing weight file raises a clear error. Cross-ref:
`docs/knowledge/air-gap.md` covers the `MiniLMEmbedder` resolution
order in detail.

## 3. mTLS termination

Two recommended topologies; pick by deployment posture.

### 3a. Edge proxy (Envoy or nginx) — primary recommendation

Run Stargraph on `localhost:8000` (HTTP), terminate mTLS at the edge.
The proxy validates client certs against a pinned CA bundle and
forwards the verified subject into a header (`X-Client-Cert-Subject`)
that `stargraph.serve.auth:MtlsProvider` consumes.

**Envoy config sketch**:

```yaml
listeners:
- address: { socket_address: { address: 0.0.0.0, port: 443 } }
  filter_chains:
  - transport_socket:
      name: envoy.transport_sockets.tls
      typed_config:
        "@type": type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.DownstreamTlsContext
        require_client_certificate: true
        common_tls_context:
          tls_certificates:
          - certificate_chain: { filename: /etc/envoy/server.crt }
            private_key: { filename: /etc/envoy/server.key }
          validation_context:
            trusted_ca: { filename: /etc/envoy/client-ca.crt }
    filters:
    - name: envoy.filters.network.http_connection_manager
      typed_config:
        route_config:
          virtual_hosts:
          - routes:
            - route: { cluster: stargraph_local }
              match: { prefix: "/" }
        http_filters:
        - name: envoy.filters.http.router
```

Why Envoy/nginx first: TLS handshake is the most-attacked surface;
running it in a battle-hardened proxy (with a CVE response cycle
measured in hours) is a stronger posture than FastAPI direct.

**nginx alternative** (one location block):

```nginx
server {
    listen 443 ssl;
    ssl_certificate     /etc/nginx/server.crt;
    ssl_certificate_key /etc/nginx/server.key;
    ssl_client_certificate /etc/nginx/client-ca.crt;
    ssl_verify_client on;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header X-Client-Cert-Subject $ssl_client_s_dn;
    }
}
```

### 3b. FastAPI direct (ultra-locked-down)

For deployments where introducing a proxy is itself a compliance
issue (e.g. SCIF with frozen package list), FastAPI handles mTLS
directly via uvicorn's `--ssl-*` flags. The cryptography backend is
the same `cryptography` lib already pinned by Stargraph.

```bash
stargraph serve \
    --profile cleared \
    --port 443 \
    --tls-cert /etc/stargraph/server.crt \
    --tls-key /etc/stargraph/server.key \
    --tls-ca-cert /etc/stargraph/client-ca.crt \
    --tls-require-client-cert
```

`stargraph.serve.auth:MtlsProvider` reads the verified peer cert from
the ASGI scope (`scope["transport"].get_extra_info("peercert")`).

Code refs:
- `stargraph.serve.auth:MtlsProvider` — both topologies converge here.
- `stargraph.serve.lifecycle:_validate_tls_paths` — startup-time path
  validation (file exists, mode 0600, owner check).

## 4. Single-process invariant

Stargraph v1 is **single-process per deployment** (locked Decision #5).
No multi-worker uvicorn (`--workers N` refused under cleared), no
ProcessPoolExecutor for graph evaluation, no thread pool with shared
state across workers.

**Rationale**:

- **Replay determinism**: cf-fork results must be reproducible across
  restarts. Multi-worker introduces non-deterministic step interleaving.
- **Audit cohesion**: a single JSONL audit sink with a single fsync'd
  fd is the source of truth. Multi-worker would require sink
  consolidation; v1 ships zero such infrastructure.
- **Capacity model**: scale via multiple Stargraph *instances* (each
  with its own checkpointer + audit sink), not multiple workers per
  instance. Use a load balancer or message-queue dispatcher upstream.

The CLI enforces this: `stargraph serve --profile cleared --workers 4`
exits with code 2 and the message `cleared profile rejects --workers
> 1 (locked Decision #5)`.

## 5. POSIX-local-only filesystems

Stargraph refuses to start when the configured state directory lives on
NFS, SMB, AFP, or any other network filesystem (task 3.31, FR-31).
Lookup is via `statfs(2)` magic-number table (`stargraph.serve.lifecycle
:_check_local_fs`).

**Rationale**:

- **Lock semantics**: SQLite's WAL mode requires `fcntl(2)` POSIX
  locks. NFS lock servers are notoriously unreliable; on AFP they
  are advisory-only.
- **fsync durability**: NFS clients buffer writes; fsync may return
  before the server flushes. Audit-sink durability is foundational.
- **mmap behavior**: SQLite uses `MAP_SHARED`; NFS implementations
  diverge wildly on cross-client mmap visibility.

**Migration path**: stage state on local SSD, replicate via
filesystem-level snapshots (LVM, ZFS) or a backup rsync into network
storage. Never run Stargraph *on* the network mount.

## 6. UTC timezone recommendation

Stargraph's audit, provenance, and cf-hash subsystems use UTC for every
recorded timestamp (`datetime.now(UTC)` everywhere). Running the
serve process in a non-UTC timezone is **allowed** but adds skew
complexity:

- The hash chain is timezone-independent (UTC always).
- Operator log lines emitted by uvicorn / Python logging carry the
  process timezone.
- Mixing operator logs (local TZ) and audit JSONL (UTC) makes
  forensics painful.

**Recommendation**: set `TZ=UTC` in the systemd unit (or container
env), keep wallclock NTP-synced, and use a local-TZ converter at
log-aggregation time if operators want local timestamps.

```ini
# /etc/systemd/system/stargraph.service
[Service]
Environment=TZ=UTC
Environment=STARGRAPH_PROFILE=cleared
Environment=HF_HUB_OFFLINE=1
Environment=HF_HOME=/srv/stargraph/models
ExecStart=/srv/stargraph/.venv/bin/stargraph serve \
    --profile cleared \
    --port 443 \
    --tls-cert /etc/stargraph/server.crt \
    --tls-key /etc/stargraph/server.key \
    --tls-ca-cert /etc/stargraph/client-ca.crt
```

## 7. Worked example: zero to serve

Assuming a fresh air-gapped host with the release bundle staged at
`/srv/stargraph/`:

```bash
# 1. Verify weights manifest.
cd /srv/stargraph/models
sha256sum -c /srv/stargraph/hf_manifest.sha256

# 2. Create venv + install Stargraph offline.
cd /srv/stargraph
python3 -m venv .venv
source .venv/bin/activate
pip install --no-index --find-links ./wheelhouse/ stargraph

# 3. Verify install (no network reach-out).
STARGRAPH_PROFILE=cleared stargraph --help

# 4. Create state directory on local FS (refuse network mounts).
install -d -m 0700 -o stargraph -g stargraph /srv/stargraph/state

# 5. Configure mTLS paths in stargraph.toml.
cat > /etc/stargraph/stargraph.toml <<'TOML'
[serve.cleared]
auth_provider = "mtls"
tls_cert = "/etc/stargraph/server.crt"
tls_key = "/etc/stargraph/server.key"
tls_ca_cert = "/etc/stargraph/client-ca.crt"
state_dir = "/srv/stargraph/state"
audit_path = "/srv/stargraph/state/stargraph.audit.jsonl"
TOML

# 6. Start serve.
TZ=UTC HF_HUB_OFFLINE=1 HF_HOME=/srv/stargraph/models \
    stargraph serve --profile cleared --port 443 \
        --tls-cert /etc/stargraph/server.crt \
        --tls-key /etc/stargraph/server.key \
        --tls-ca-cert /etc/stargraph/client-ca.crt \
        --tls-require-client-cert
```

The first request (`curl --cert client.pem --cacert server-ca.pem
https://stargraph.local:443/v1/health`) should return `{"status":"ok"}`
with no outbound network observable on `tcpdump -i any not port 443`.

## 8. Failure modes + diagnostics

| Symptom | Likely cause | Fix |
|---|---|---|
| `stargraph serve` exits with `nfs/smb refused at bootstrap` | state_dir on a network mount | Move state_dir to local SSD |
| `MiniLMEmbedder` raises `LocalEntryNotFoundError` | weights not staged | Re-extract `models/` and verify manifest |
| `cleared profile rejects --workers > 1` | multi-worker attempt | Use a single worker (Decision #5) |
| `pack signature verification failed` | pack signed with wrong key or alg | Check `stargraph.toml` allow-list; alg must be Ed25519 |
| TLS handshake fails with `unknown ca` | client cert not in client-ca.crt | Re-issue client cert from the pinned CA |

## 9. Cross-references

- `docs/knowledge/air-gap.md` — knowledge-stack offline staging.
- `docs/security/threat-model.md` — STRIDE matrix; cleared-mode
  mitigations.
- `docs/security/sign-off.md` — pre-release rubric (covers NFS
  refusal, --allow-side-effects gate, mTLS posture).
- `docs/reference/cli.md` — full `stargraph serve` flag reference.
