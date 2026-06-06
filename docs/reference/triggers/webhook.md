# WebhookTrigger

`stargraph.triggers.webhook.WebhookTrigger` is the HTTP-receive trigger plugin
(design §6.4). It mounts a FastAPI `POST` route per
[`WebhookSpec`](#webhookspec) and verifies inbound bodies against a
Stripe-style HMAC-SHA256 signature before enqueueing a run.

Source: `src/stargraph/triggers/webhook.py`.

## WebhookSpec

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `trigger_id` | `str` | required | Stable identifier (e.g. `"webhook:nvd-mirror"`). Goes into the idempotency key and the nonce LRU triple, so it must be unique across the deployment. |
| `path` | `str` | required | HTTP path the FastAPI route mounts (e.g. `"/v1/webhooks/github"`). Must start with `/`. |
| `current_secret` | `bytes` | required | Active HMAC-SHA256 key. Used for both signing (caller-side) and verification (this side). Stored as `bytes` to discourage accidental string concatenation. |
| `previous_secret` | `bytes \| None` | `None` | Optional rotation-grace key. **Valid for verification only.** |
| `graph_id` | `str` | required | Target graph to enqueue when verification succeeds. |
| `params_extractor` | `Callable[[bytes, dict], dict] \| None` | `None` | Optional `(raw_body, headers) -> params` mapper. Default = body parsed as JSON; raises 400 on malformed JSON. |
| `timestamp_window_seconds` | `int` | `300` | ±window for `X-Stargraph-Timestamp`. Set to 0 to disable (NOT recommended). |
| `nonce_lru_size` | `int` | `10000` | Per-trigger nonce LRU capacity. ~1h at typical webhook traffic. |
| `signature_header` | `str` | `"X-Stargraph-Signature"` | Header carrying the HMAC digest. |
| `timestamp_header` | `str` | `"X-Stargraph-Timestamp"` | Header carrying the timestamp. |

## Verification gauntlet

Five steps, in order (FR-9.1-9.5, NFR-11):

### 1. Read raw body + headers

`X-Stargraph-Timestamp` (Unix epoch seconds as ASCII int) and
`X-Stargraph-Signature` (lowercase hex digest) are pulled from the request.

| Outcome | Status | `detail` |
| --- | --- | --- |
| Either header missing | 401 | `missing_headers` |
| Timestamp not parseable as int | 401 | `invalid_timestamp` |

### 2. Timestamp window

```python
if abs(now - timestamp) > spec.timestamp_window_seconds:
    raise HTTPException(401, detail="timestamp_out_of_window")
```

Rejects replays of intercepted signatures from outside the 5-minute grace.

### 3. HMAC compare

Compute and compare with `hmac.compare_digest` (constant-time):

```python
signed_payload = f"{ts}.{raw_body.decode('utf-8')}"
expected = hmac.new(secret, signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()
```

Try `current_secret` first, fall back to `previous_secret` (rotation
grace; valid for verify only). Both fail → 401 + audit-emit
`BosunAuditEvent` (kind `webhook_signature_invalid`).

### 4. Nonce LRU

Track `(trigger_id, signature, timestamp)` triples. Same digest re-submitted
within the 5-min grace is by definition a replay (the HMAC binds
`(timestamp, body)`, so a duplicate `(sig, ts)` implies the same body).

| Outcome | Status | `detail` |
| --- | --- | --- |
| First sight | (continue) | — |
| Replay | 409 | `duplicate_nonce` |

`_NonceLRU` is an `OrderedDict`-backed LRU guarded by `asyncio.Lock` so
the test-and-set is atomic. Replays still get promoted to MRU so a flood
of replays does not evict legitimate recent entries.

### 5. Enqueue

```python
params = self._extract_params(spec, raw_body, headers)
idem = self.idempotency_key(spec.trigger_id, raw_body)
self._scheduler.enqueue(graph_id=spec.graph_id, params=params, idempotency_key=idem)
return {"accepted": True, "idempotency_key": idem}
```

## Idempotency key

```python
@staticmethod
def idempotency_key(trigger_id: str, raw_body: bytes) -> str:
    body_hash = hashlib.sha256(raw_body).hexdigest()
    payload = f"{trigger_id}|{body_hash}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

`sha256(trigger_id || body_hash)` per design §6.4. Two callers posting
the same body to the same trigger compute the same key; the Checkpointer
pending-row write dedupes downstream.

## Dual-secret rotation

Per Resolved Decision #8 (90-day rotation cadence):

| Secret | Sign | Verify |
| --- | --- | --- |
| `current_secret` | yes | yes |
| `previous_secret` | **no** | yes |

In-flight callers do not 401 on the seam during rotation. Rotation
procedure:

1. Stage the new key as `current_secret`; copy the old key to
   `previous_secret`.
2. Wait until the longest in-flight signing window has drained.
3. Drop `previous_secret` (set to `None`).

## Lifecycle

| Method | Behaviour |
| --- | --- |
| `init(deps)` | Stash `deps["scheduler"]`, optional `deps["audit_sink"]`, and parse `deps["webhook_specs"]`. Validates that every spec has a non-empty `current_secret`. Raises `StargraphRuntimeError` on missing required keys or empty spec list. |
| `start()` | No-op (sets `_running = True`). FastAPI dispatches inbound requests synchronously. |
| `stop()` | No-op (sets `_running = False`). Routes stop serving when the app shuts down. |
| `routes()` | Returns one `APIRouter` carrying one `POST` route per `WebhookSpec.path`, named `stargraph.triggers.webhook.<trigger_id>`. FastAPI is imported lazily so non-serve plugin hosts can still import the module. |

!!! note
    `Request` is imported at module-top (not under `TYPE_CHECKING`) because
    FastAPI's annotation resolver looks up handler-parameter annotations
    in the handler's `__globals__`. A typing-only import would leave
    `Request` unresolved at mount time and FastAPI would treat the
    parameter as a query field (HTTP 422 on every request).

## Audit emission

When `deps["audit_sink"]` is present in `init`, every bad-signature 401
emits a `BosunAuditEvent`:

```python
BosunAuditEvent(
    run_id=f"webhook:{spec.trigger_id}",
    step=0,
    ts=datetime.now(UTC),
    pack_id="stargraph.triggers.webhook",
    pack_version="1.0",
    fact={"kind": "webhook_signature_invalid", "timestamp": ts, "path": spec.path},
    provenance={
        "origin": "system",
        "source": "stargraph.triggers.webhook",
        "run_id": f"webhook:{spec.trigger_id}",
        "step": 0,
        "confidence": 1.0,
        "timestamp": <iso now>,
    },
)
```

If the sink raises, the failure is logged and swallowed — audit must not
500 the request. Missing sink is a silent no-op; the 401 still fires and
the event lives only in the structured request log.

## Error reference

| Status | `detail` | Meaning |
| --- | --- | --- |
| 401 | `missing_headers` | One of `X-Stargraph-Timestamp` / `X-Stargraph-Signature` absent. |
| 401 | `invalid_timestamp` | Timestamp header not a parseable int. |
| 401 | `timestamp_out_of_window` | Outside ±`timestamp_window_seconds`. |
| 401 | `invalid_signature` | HMAC mismatch on both current and previous secrets. Audit event emitted. |
| 409 | `duplicate_nonce` | `(trigger_id, signature, timestamp)` already seen in the LRU. |
| 400 | `invalid_body_json: …` | Default extractor saw malformed JSON. |
| 400 | `body_not_json_object` | Default extractor saw a JSON value that is not an object. |

## Helpers

```python
@staticmethod
def sign(secret: bytes, timestamp: int, raw_body: bytes) -> str: ...

@staticmethod
def verify(
    *,
    current_secret: bytes,
    previous_secret: bytes | None,
    timestamp: int,
    raw_body: bytes,
    signature: str,
) -> bool: ...
```

`sign` produces the lowercase hex HMAC-SHA256 of `f"{ts}.{body}"`.
`verify` runs the constant-time compare against `current` then
`previous`. Both are static so unit-style smoke tests and caller-side
signing helpers can use them without holding a `WebhookTrigger`
instance.

## Example

```python
from stargraph.triggers.webhook import WebhookSpec, WebhookTrigger

spec = WebhookSpec(
    trigger_id="webhook:nvd-mirror",
    path="/v1/webhooks/nvd",
    current_secret=b"<32+ random bytes>",
    previous_secret=None,
    graph_id="nvd_ingest",
    timestamp_window_seconds=300,
    nonce_lru_size=10000,
)

trigger = WebhookTrigger()
trigger.init({
    "scheduler": scheduler,
    "webhook_specs": [spec],
    "audit_sink": audit_sink,
})
routes = trigger.routes()
# mount `routes` on the FastAPI app
```

Caller-side signing (curl-like):

```python
import time
ts = int(time.time())
body = b'{"feed": "nvd"}'
sig = WebhookTrigger.sign(spec.current_secret, ts, body)
# POST body with headers:
#   X-Stargraph-Timestamp: {ts}
#   X-Stargraph-Signature: {sig}
```

## See also

- [Triggers index](index.md)
- [Manual trigger](manual.md)
- [Cron trigger](cron.md)
- [Serve: HTTP API](../../serve/api.md)
- [Security: threat model](../../security/threat-model.md) — the §3.11
  webhook-signature surface.
