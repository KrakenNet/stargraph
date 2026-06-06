# STRIDE Threat Model — Stargraph v1

**Status**: v1 (cleared-mode + OSS-default).
**Spec ref**: `stargraph-serve-and-bosun` §12.1 (FR-61, FR-62, AC-8.1, AC-8.2).
**Locked decisions**: #2 (cf-respond source prefix), #5 (single-process invariant).

This document enumerates the 36 STRIDE cells across 6 attack surfaces.
Each cell is a **mitigation** (with a code reference), an **open gap**
(with the planned post-1.0 fix), or **n/a — by construction**.

The most prominent v1 gap is the **Fathom-pack hot-reload absence**
(see `Tampering × Plugin loader` and `Elevation × Plugin loader`): a
malicious pack mutation requires a serve restart to take effect. The
v1 boundary is "bring your own clean operator workflow"; post-1.0
work plans an SBOM-gated reload path.

## Attack surfaces

| # | Surface | Description |
|---|---------|-------------|
| 1 | HTTP API | FastAPI routes mounted under `/v1/*`. Auth = profile-driven. |
| 2 | WebSocket stream | `/v1/runs/{id}/stream` (SSE-equivalent over WS). |
| 3 | Plugin loader | Entry-point discovery (`stargraph.plugins`) + Bosun pack loader. |
| 4 | IR loader | YAML graph documents authored by humans or LLM-generated. |
| 5 | State + Checkpointer | SQLite checkpointer, run history, audit-sink coupling. |
| 6 | Replay engine | cf-fork mutation overlay + diff renderer. |

## STRIDE Matrix (6×6 = 36 cells)

### Spoofing

| Surface | Mitigation / Gap |
|---------|------------------|
| HTTP API | mTLS in cleared profile (`stargraph.serve.auth:MtlsProvider`); BearerJWT (JWKS-pinned) in OSS-default. Bypass-provider rejected at startup when `profile.allow_anonymous=False`. |
| WebSocket | Auth header carried in upgrade request; same provider chain as HTTP. Anonymous WS connections refused under cleared (`stargraph.serve.api:websocket_endpoint`). |
| Plugin loader | Pack signing alg-strict: only Ed25519 accepted; `none`/HS256/RS256 rejected at load (`stargraph.bosun.signing:verify`). Static pubkey allow-list + TOFU first-pin (FR-21). |
| IR loader | n/a — by construction. The IR loader has no notion of identity; it consumes already-parsed YAML. Author identity is upstream (git, signing). |
| State + Checkpointer | n/a — by construction. The checkpointer is identity-blind; it persists what the engine commits. Spoofing is upstream (the actor that emitted the fact). |
| Replay engine | cf-respond facts forced to `source="cf:<actor>"` per locked Decision #2. The cf-mutation cannot impersonate a real user (`stargraph.serve.respond:cf_respond`). |

### Tampering

| Surface | Mitigation / Gap |
|---------|------------------|
| HTTP API | TLS terminates at the edge (Envoy/nginx in cleared, FastAPI direct in OSS). Body validation via Pydantic 422. Rate-limiter prevents tampering-via-amplification (`stargraph.serve.ratelimit`). |
| WebSocket | WS frames are read-only on the server side (events flow out, never in). Slow-consumer disconnects with 1011 prevent backpressure tampering (`stargraph.serve.broadcast:_emit_with_timeout`). |
| Plugin loader | **GAP**: Fathom-pack hot-reload absent (AC-8.3). Tampering with a pack on disk requires a serve restart to take effect — the running process is immune to in-place pack mutation. Post-1.0: SBOM-gated reload with re-verification of signatures + capability deltas. |
| IR loader | YAML safe-load only (`yaml.safe_load`); no `!!python/object` exec. Schema validation via Pydantic before any handler dispatches. Cap-grant audit for IR-declared capabilities. |
| State + Checkpointer | Checkpoint rows immutable post-commit (append-only schema with `step` PK). Audit-sink fsync on every write (`stargraph.audit.jsonl:write`). JSONL signing key (Ed25519) for tamper-evident logs. |
| Replay engine | cf-fork copies parent rows by reference; does not mutate parent state. Mutation overlay validated before fork-step seek (`stargraph.replay.counterfactual:apply_mutation`). |

### Repudiation

| Surface | Mitigation / Gap |
|---------|------------------|
| HTTP API | Every authenticated request emits a `request_audit` event with `(actor, capability, route, status)`. JSONL sink is fsync'd. Audit-sink mandatory under cleared (`stargraph.serve.profiles:ClearedProfile.audit_sink_required=True`). |
| WebSocket | WS connect/disconnect emits audit events. Per-frame events carry `actor` lineage. |
| Plugin loader | Pack-load events recorded with pack hash + signature verification result (`stargraph.bosun.loader`). |
| IR loader | IR-document hash recorded in run-history `graph_hash` field. Determinism guarantee (FR-93): same IR + state -> same graph_hash. |
| State + Checkpointer | Provenance bundle on every fact: `(origin, source, run_id, step, confidence, timestamp)` per `stargraph.fathom._provenance:ProvenanceBundle`. Lineage audit script (`scripts/lineage_audit.py`) gates CI. |
| Replay engine | cf-run-id minted as `cf-<uuid>`; parent linkage stored in `runs_history.parent_run_id`. cf-respond facts carry `source="cf:<actor>"` so they're never confused with parent-run respond facts. |

### Information disclosure

| Surface | Mitigation / Gap |
|---------|------------------|
| HTTP API | TLS for all external traffic. CORS denies cross-origin in cleared. PII-scrubbing in error envelopes (`stargraph.serve.api:_redact_error`). Profile-conditional default-deny on read routes (cleared). |
| WebSocket | Same TLS posture. WS frames carry only typed `Event` shapes — no internal exception traces leak. |
| Plugin loader | Pack contents are filesystem-readable (not over-the-wire). Pack-internal secrets are operator responsibility (POSIX file mode 0600 recommended; documented in air-gap guide). |
| IR loader | n/a — by construction. The IR document is a public contract; no secrets in IR. |
| State + Checkpointer | HITL audit hashes the response body, not its content (`stargraph.serve.respond:_compute_body_hash`). Checkpoint rows are stored as opaque bytes — never logged. SQLite file mode 0600 in cleared (documented in air-gap guide). |
| Replay engine | cf-fork is per-process; no cross-run leakage (`stargraph.replay.counterfactual` rate-limiter scoped per actor). cf-mutation values not echoed to logs unless `--log-level=debug`. |

### Denial of service

| Surface | Mitigation / Gap |
|---------|------------------|
| HTTP API | Per-actor rate-limiter (`stargraph.serve.ratelimit:TokenBucket`, design §5.5). Connection cap. Body-size cap (FastAPI default + custom dep). |
| WebSocket | Slow-consumer disconnects with WS code 1011 + 5s emit timeout (`stargraph.serve.broadcast`). Prevents single slow client from blocking broadcast. |
| Plugin loader | Pack-load is one-shot at startup — no DoS surface during request handling. Bosun rule-eval CPU bounded by CLIPS rule-fact count caps (per pack). |
| IR loader | YAML parse depth/size limits via `yaml.safe_load` defaults. IR document size capped pre-load. |
| State + Checkpointer | SQLite single-writer-lock guarantees no contention storm. Audit-sink rotates at 100 MiB to bound disk usage. **GAP**: no per-run wall-clock cap in v1 — runaway IR can hang a run indefinitely; operator kill via `stargraph cancel <run_id>`. Post-1.0: `--max-run-duration` profile knob. |
| Replay engine | cf-rate-limiter = 1 cf-fork per actor per minute (`stargraph.serve.api:_cf_rate_limiter`). Prevents cf-amplification DoS. |

### Elevation of privilege

| Surface | Mitigation / Gap |
|---------|------------------|
| HTTP API | Capability gate at route boundary (`stargraph.serve.api:require`). Cleared profile = default-deny on the 7 mutation routes (cancel/pause/respond/cf/artifacts r+w/broker). Capability-deny audit emission verified (task 3.22). |
| WebSocket | WS read-only — no privilege escalation surface. Capability check on connect (read-runs cap required). |
| Plugin loader | **GAP**: Fathom-pack hot-reload absent (AC-8.3) — a tampered pack with elevated capability declarations cannot take effect mid-run; serve restart required. Post-1.0: SBOM-gated reload with capability-delta review. Pack signing prevents on-disk tampering from succeeding without operator complicity. |
| IR loader | IR-declared capabilities checked against `Capabilities` instance pinned by profile. Cleared profile pins a stricter set. No `!!python/object` exec route. |
| State + Checkpointer | `--allow-side-effects` startup gate under cleared (task 2.37) — refuses to start unless operator explicitly opts in. Replay-mode side-effect blocker is independent of this gate. |
| Replay engine | Replay isolation: cf-runs do NOT trigger external side effects (`stargraph.replay.counterfactual:_replay_ctx`). HITL `respond` rate-limited per actor + scoped to the parent run's actors. |

## Trigger trust boundaries

The 36-cell matrix above frames `serve` as a single HTTP/WS surface.
Trigger ingress (cron, manual, webhook) deserves an explicit
articulation because each trigger type sits at a different point on
the trust axis:

| Trigger | Trust posture | Code reference |
|---------|---------------|----------------|
| **`webhook`** | **Untrusted by default**, HMAC-gated. Body validated, signature verified against the per-source secret before enqueue. Replay window enforced. | [`src/stargraph/triggers/webhook.py`](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/triggers/webhook.py) |
| **`cron`** | **Trusts nothing external** (good). Schedule is operator-authored at deploy time; trigger fires from the in-process scheduler with no caller identity to spoof. | [`src/stargraph/triggers/cron.py`](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/triggers/cron.py) |
| **`manual.enqueue`** | **Trusts the caller** (Python API). Anyone with import access can synthesize a run; HTTP-equivalent gating happens at `POST /v1/runs` (capability gate via profile). Use only inside trusted entry points. | [`src/stargraph/triggers/manual.py`](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/triggers/manual.py) |

The HTTP `POST /v1/runs` route shares the manual trigger's enqueue
path but is gated by the capability check before it lands there;
direct Python use of `manual.enqueue` bypasses that gate, so treat
import access to it as equivalent to the `runs:write` capability.

## Bosun pack signing — TOFU drift

Pack signature verification is **alg-strict EdDSA-only** (`alg:none`,
HS256, RS256 are rejected at load). The trust anchor is a static
pubkey allow-list plus a TOFU first-pin: the first time a new pack id
is seen, the operator-supplied pubkey is recorded; subsequent loads
must present a signature verifiable under that pinned key.

Drift cases the TOFU pin catches:

- **Pubkey rotation.** A new key needs an explicit allow-list update
  by the operator. A pack signed with an unpinned-but-valid Ed25519
  key is rejected; the loader does not auto-trust on first sight if
  an existing pin disagrees.
- **Pack id reuse with a different signer.** Same id, different
  pubkey = reject. Mitigates the "rename a pack to take over an
  existing trusted slot" attack.

What the pin does **not** cover:

- **Filesystem tampering before first pin.** TOFU implies the first
  load is the authoritative one. Air-gap operators should fingerprint
  the pubkey out-of-band (release-signing key from
  [`reference/signing.md`](../reference/signing.md)).
- **Compromised signing key.** Once the key is on the allow-list,
  anything signed by it loads. Rotation is operator-driven; there is
  no automatic revocation feed in v1.

Source: [`src/stargraph/bosun/signing.py`](https://github.com/KrakenNet/stargraph/blob/main/src/stargraph/bosun/signing.py),
plus the alg-strict guard documented in [how-to/bosun-pack.md](../how-to/bosun-pack.md).

## Documented gaps (post-1.0 work)

1. **Fathom-pack hot-reload** (AC-8.3) — present in 2 cells (Tampering × Plugin loader, Elevation × Plugin loader). Mitigation: serve restart for any pack mutation. Post-1.0: SBOM-gated reload.
2. **Per-run wall-clock cap** (DoS × State+Checkpointer) — operator-driven via `stargraph cancel`. Post-1.0: profile-level `--max-run-duration` knob.

## How to use this document

- Before each release: re-walk the matrix; update any cell where the
  code reference moved or the mitigation changed.
- For a new attack surface: add a row across all 6 STRIDE columns.
- For a new STRIDE category (e.g. supply-chain): add a column across
  all 6 surfaces.
- Sign-off rubric (`docs/security/sign-off.md`) check #1 = "all 36
  cells filled". The v1 release blocker is this matrix complete +
  every documented gap has a tracked post-1.0 issue.
