# HTTP API

Stargraph's HTTP surface lives under `/v1/*`, mounted on a FastAPI app
constructed by `stargraph.serve.api.create_app`.
Every route runs through the auth provider chain (profile-pinned), then the
route-level capability gate (`Depends(require(...))`), then the handler.
Authentication and capability decisions emit audit events on every request.

The route surface is intentionally small: 12 v1 routes plus a WebSocket
stream. The OpenAPI 3.1 spec is the canonical contract — see
[`reference/openapi.json`](../reference/openapi.json). This page is the
human-readable index; for parameter schemas and response models, follow
the OpenAPI link or render via Swagger UI / Redoc.

## Conventions

| Concern | Behavior | Source |
|---|---|---|
| Auth | Bearer token (or profile-pinned chain). 401 on miss. | `serve/auth.py` |
| Capability gate | `Depends(require("<cap>"))` per route; 403 on deny + audit event. | `serve/api.py:337` |
| Errors | FastAPI default envelope; `_redact_error` strips internals on 500. | `serve/api.py` |
| Rate limits | `X-RateLimit-*` + `Retry-After` headers when enabled. | `serve/ratelimit.py` |
| Pagination | `limit` (≤1000, default 100) + `offset`; keyset pagination is Phase 3. | `serve/history.py` |

## Routes

### Runs

| Method | Path | Capability | Notes |
|---|---|---|---|
| `POST` | `/v1/runs` | `runs:start` | Enqueue a run. Returns `202 Accepted` with `{run_id, status: "pending"}`. Body: `{graph_id, inputs?, trigger_source?}`. |
| `GET` | `/v1/runs` | `runs:read` | Paginated list. Filters: `status`, `since`, `until`, `trigger_source`, `limit`, `offset`. Backed by `RunHistory`. |
| `GET` | `/v1/runs/{run_id}` | `runs:read` | `RunSummary` for the run, or 404. POC reads in-memory `deps["runs"]`; Phase 2 reads from Checkpointer. |
| `POST` | `/v1/runs/{run_id}/cancel` | `runs:cancel` | Cooperative cancel. Routes through `lifecycle.cancel_run` → engine capability check + audit + bus signal. |
| `POST` | `/v1/runs/{run_id}/pause` | `runs:pause` | Cooperative pause. Same lifecycle path as cancel. |
| `POST` | `/v1/runs/{run_id}/resume` | `runs:resume` | Resume a paused run. Returns `202` with `status="pending"` once `GraphRun.resume` materializes a fresh run bound to the same checkpoint. |
| `POST` | `/v1/runs/{run_id}/respond` | `runs:respond` | Deliver a HITL response to an `awaiting-input` run. Body: `{response, actor}`. State-precondition checked before write. |
| `POST` | `/v1/runs/{run_id}/counterfactual` | `counterfactual:run` | Fork a counterfactual child at `body.step`. Mints `cf-<uuid>` child id; parent unchanged. |

### Artifacts

| Method | Path | Capability | Notes |
|---|---|---|---|
| `GET` | `/v1/runs/{run_id}/artifacts` | `artifacts:read` | List `ArtifactRef`s emitted by the run. Empty list when none. |
| `GET` | `/v1/artifacts/{artifact_id}` | `artifacts:read` | Raw bytes. `Content-Type` resolved from sidecar metadata when available, else `application/octet-stream`. |

### Discovery

| Method | Path | Capability | Notes |
|---|---|---|---|
| `GET` | `/v1/graphs` | `runs:read` | List registered graphs (`graph_id`, `graph_hash`, node count). Backed by `deps["graphs"]`. |
| `GET` | `/v1/registry/{kind}` | `runs:read` | Plugin-discovered registry entries. `kind` ∈ `{tools, skills, stores}`. |

### Streaming

| Path | Notes |
|---|---|
| `WS /v1/runs/{run_id}/stream` | Server-pushed event stream (audit + node lifecycle + result). `Last-Event-Id: <event_id>,<offset>,<seq>` resumes after reconnect. See [WebSocket stream](ws.md). |

## Capability matrix

| Capability | Routes |
|---|---|
| `runs:start` | `POST /v1/runs` |
| `runs:read` | `GET /v1/runs`, `GET /v1/runs/{id}`, `GET /v1/graphs`, `GET /v1/registry/{kind}` |
| `runs:cancel` | `POST /v1/runs/{id}/cancel` |
| `runs:pause` | `POST /v1/runs/{id}/pause` |
| `runs:resume` | `POST /v1/runs/{id}/resume` |
| `runs:respond` | `POST /v1/runs/{id}/respond` |
| `counterfactual:run` | `POST /v1/runs/{id}/counterfactual` |
| `artifacts:read` | `GET /v1/runs/{id}/artifacts`, `GET /v1/artifacts/{id}` |

Profiles map roles to capability sets — see [Profiles](profiles.md). The
gate emits a `capability_denied` audit event before returning 403, including
caller identity, capability, and route.

## Error envelope

FastAPI's default JSON shape on 4xx/5xx:

```json
{ "detail": "<message>" }
```

For `422 Unprocessable Entity` (request validation), Pydantic supplies a
location-tagged list. On unhandled exceptions the response goes through
`_redact_error`, which collapses the message to a stable string and emits
the full traceback to the audit log only.

## Rate limits

When `RATELIMIT_*` settings are configured, every route returns:

- `X-RateLimit-Limit` — window quota.
- `X-RateLimit-Remaining` — quota left in the current window.
- `X-RateLimit-Reset` — epoch seconds until window rolls.

429 responses include `Retry-After` (seconds). The implementation lives in
`stargraph.serve.ratelimit`; defaults are profile-driven.

## Generating clients

The Python and TypeScript clients are not shipped in v1. You can generate
either from `docs/reference/openapi.json` with the standard tools:

```bash
# python
openapi-python-client generate --path docs/reference/openapi.json

# typescript
npx openapi-typescript docs/reference/openapi.json -o client.ts
```

The OpenAPI spec is regenerated by `stargraph.serve.openapi` on each release;
see `tests/integration/serve/test_openapi_freshness.py` for the drift
canary.

## See also

- [WebSocket stream](ws.md) — `WS /v1/runs/{id}/stream` framing + resume.
- [Run history](runs.md) — `RunHistory` semantics behind `GET /v1/runs`.
- [Profiles](profiles.md) — capability set per profile.
- [HITL](hitl.md) — `awaiting-input` lifecycle for `respond`.
- [Artifacts](artifacts.md) — `ArtifactRef` + sidecar metadata.
- [`reference/v1-limits.md`](../reference/v1-limits.md) — stub-vs-real boundaries.
