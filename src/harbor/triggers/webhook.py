# SPDX-License-Identifier: Apache-2.0
""":class:`WebhookTrigger` plugin -- HMAC-verified HTTP enqueue (design §6.4).

The HTTP-receive variant of the v1 trigger trio. Unlike :class:`ManualTrigger`
(task 2.9, explicit-caller path) and :class:`CronTrigger` (task 2.10, clock
poll), :class:`WebhookTrigger` mounts a FastAPI route per :class:`WebhookSpec`
and verifies inbound POST bodies against a Stripe-style HMAC-SHA256 signature
before enqueuing a run.

Verification gauntlet (design §6.4, FR-9.1-9.5, NFR-11), in order:

1. **Read raw body + headers**. ``X-Harbor-Timestamp`` (Unix epoch seconds as
   ASCII) and ``X-Harbor-Signature`` (lowercase hex digest) are pulled from
   the request. Missing headers → 401 + ``{"detail": "missing_headers"}``.
2. **Timestamp window** (±``timestamp_window_seconds``, default 300).
   Reject replays of intercepted signatures from outside the 5-min grace.
   401 + ``{"detail": "timestamp_out_of_window"}``.
3. **HMAC compare** (constant-time via :func:`hmac.compare_digest`).
   Compute ``expected = HMAC-SHA256(secret, f"{ts}.{raw_body.decode()}")``;
   try ``current_secret`` first, fall back to ``previous_secret`` (rotation
   grace; valid for verify, NOT for new signatures). Both fail → 401 +
   audit-emit :class:`BosunAuditEvent` with kind ``webhook_signature_invalid``.
4. **Nonce LRU** (size ``nonce_lru_size``, default 10000 ≈ 1h at typical
   traffic). Track ``(trigger_id, signature, timestamp)`` triples;
   duplicates → 409 + ``{"detail": "duplicate_nonce"}``.
5. **Enqueue**. Run :meth:`WebhookSpec.params_extractor` over the raw body
   + headers (default = body-as-JSON); call
   :meth:`Scheduler.enqueue` with ``idempotency_key =
   sha256(trigger_id || body_hash)``.

Lifecycle (matches the :class:`~harbor.triggers.Trigger` Protocol):

* :meth:`init` -- stash the :class:`Scheduler` + optional audit sink from
  ``deps``, parse the supplied :class:`WebhookSpec` list (validates that
  every spec has at least a ``current_secret``).
* :meth:`start` / :meth:`stop` -- no-op; the FastAPI route handlers serve
  inbound requests synchronously through the app.
* :meth:`routes` -- one ``POST`` route per :class:`WebhookSpec.path`.

Why a custom ``OrderedDict``-based LRU? :class:`functools.lru_cache` is
not async-safe (the function-decorator API does not let us insert keys
explicitly), and ``cachetools`` is not in the dependency closure. A 15-line
:class:`_NonceLRU` keeps the implementation tight and async-safe via
:class:`asyncio.Lock`.

References: design §6.4 (webhook trigger), §6.3 (trigger lifecycle); FR-5
(webhook trigger), FR-9.1-9.5 (HMAC + nonce + window + dual-secret + audit),
NFR-11 (signature verify), AC-12.1 (plugin discovery).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections import OrderedDict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

# ``Request`` is imported at runtime (NOT under TYPE_CHECKING) because
# FastAPI's annotation resolver looks up handler-parameter annotations in
# the handler function's ``__globals__`` -- which is this module. A
# typing-only import would leave ``Request`` unresolved at mount time and
# FastAPI would fall back to treating the parameter as a query field
# (HTTP 422 on every request). The ``noqa: TC002`` is therefore correct.
from fastapi import Request  # noqa: TC002

from harbor.errors import HarborRuntimeError
from harbor.ir import IRBase
from harbor.logging import get_logger
from harbor.runtime.events import BosunAuditEvent

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from harbor.serve.scheduler import Scheduler

__all__ = ["WebhookSpec", "WebhookTrigger"]

_logger = logging.getLogger(__name__)
_structlog = get_logger(__name__)

# Route is aliased to ``Any`` to keep this module import-light; FastAPI is
# imported lazily inside :meth:`WebhookTrigger.routes` so non-serve plugin
# hosts (e.g. unit tests that only validate HMAC) need not pull it in.
type _Route = Any

#: Default request header carrying the lowercase-hex HMAC-SHA256 digest.
_DEFAULT_SIGNATURE_HEADER = "x-harbor-signature"

#: Default request header carrying the Unix-epoch-seconds timestamp.
_DEFAULT_TIMESTAMP_HEADER = "x-harbor-timestamp"


class WebhookSpec(IRBase):
    """Single webhook-trigger configuration row (design §6.4).

    Attributes:
        trigger_id: Stable identifier for this webhook trigger instance
            (e.g. ``"webhook:nvd-mirror"``). Goes into the idempotency
            key and the nonce LRU triple, so it must be unique across
            the deployment.
        path: HTTP path the FastAPI route mounts (e.g.
            ``"/v1/webhooks/github"``). Must start with ``/``.
        current_secret: Active HMAC-SHA256 key used for both signing
            (caller-side) and verification (this side). Stored as
            :class:`bytes` to discourage accidental string concatenation.
        previous_secret: Optional rotation-grace key. Valid for
            verification only -- the design's 90-day rotation cadence
            (Resolved Decision #8) keeps the previous key live so
            in-flight callers do not 401 on the seam.
        graph_id: Target graph to enqueue when verification succeeds.
        params_extractor: Optional callable mapping ``(raw_body, headers)``
            to a JSON-serializable params dict. Default = body parsed
            as JSON (raises 400 on malformed JSON).
        timestamp_window_seconds: ±window for the
            ``X-Harbor-Timestamp`` header. Default 300 (5 min) per
            design §6.4. Set to 0 to disable (NOT recommended).
        nonce_lru_size: Capacity of the per-trigger nonce LRU.
            Default 10000 ≈ 1h at typical webhook traffic; bump for
            hot triggers.
        signature_header: Header name carrying the HMAC digest.
            Default ``X-Harbor-Signature``.
        timestamp_header: Header name carrying the timestamp.
            Default ``X-Harbor-Timestamp``.
    """

    trigger_id: str
    path: str
    current_secret: bytes
    graph_id: str
    previous_secret: bytes | None = None
    params_extractor: Any = None  # ``Callable[[bytes, dict], dict] | None``
    timestamp_window_seconds: int = 300
    nonce_lru_size: int = 10000
    signature_header: str = "X-Harbor-Signature"
    timestamp_header: str = "X-Harbor-Timestamp"

    model_config: ClassVar[dict[str, Any]] = {
        **IRBase.model_config,
        "arbitrary_types_allowed": True,
    }


class _NonceLRU:
    """Tiny async-safe ``OrderedDict``-based LRU for ``(triple) -> True`` membership.

    Used to detect replays of webhook calls within the timestamp window.
    The ``triple`` is ``(trigger_id, signature, timestamp)``: same digest
    re-submitted within the 5-min grace is by definition a replay (the
    HMAC binds (timestamp, body), so a duplicate (sig, ts) implies the
    same body).

    Concurrency: a single :class:`asyncio.Lock` guards the
    :class:`OrderedDict` so concurrent route handlers do not race on
    insert+evict. Lock contention is microseconds at typical webhook
    rates (the critical section is two dict operations).
    """

    _data: OrderedDict[tuple[str, str, int], None]
    _capacity: int
    _lock: asyncio.Lock

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("nonce_lru_size must be > 0")
        self._data = OrderedDict()
        self._capacity = capacity
        self._lock = asyncio.Lock()

    async def check_and_record(self, key: tuple[str, str, int]) -> bool:
        """Return ``True`` on first sight (recorded), ``False`` on replay.

        Atomic test-and-set under ``_lock`` so two concurrent requests
        with the same triple cannot both pass.
        """
        async with self._lock:
            if key in self._data:
                # Promote to MRU even on replay so a flood of replays does
                # not evict legitimate recent entries.
                self._data.move_to_end(key)
                return False
            self._data[key] = None
            if len(self._data) > self._capacity:
                self._data.popitem(last=False)
            return True


class WebhookTrigger:
    """HTTP-webhook trigger plugin (HMAC + nonce + dual-secret).

    One :class:`WebhookTrigger` instance owns N :class:`WebhookSpec` rows;
    on :meth:`routes` it returns one FastAPI ``POST`` route per spec. The
    route handler runs the verification gauntlet inline (no background
    task) and either enqueues via :meth:`Scheduler.enqueue` or returns
    a 401/409 response.

    Audit-event emission on bad signature: if ``deps["audit_sink"]`` is
    present in :meth:`init`, the route handler awaits
    ``audit_sink.write(BosunAuditEvent(...))`` on each verification
    failure (FR-9.5 "audit on bad sig"). Missing sink → silent no-op
    (the 401 still fires; the audit trail just lives only in the
    structured request log).
    """

    _scheduler: Scheduler | None
    _audit_sink: Any | None
    _specs: list[WebhookSpec]
    _nonce_caches: dict[str, _NonceLRU]
    _running: bool

    def __init__(self) -> None:
        self._scheduler = None
        self._audit_sink = None
        self._specs = []
        self._nonce_caches = {}
        self._running = False

    def init(self, deps: dict[str, Any]) -> None:
        """Capture the :class:`Scheduler` + optional audit sink and parse specs.

        ``deps["scheduler"]`` is the lifespan-built scheduler.
        ``deps["webhook_specs"]`` is an iterable of :class:`WebhookSpec`
        (or dicts that parse to one). ``deps["audit_sink"]`` is optional;
        when present, bad-signature 401s emit a :class:`BosunAuditEvent`.

        Raises :class:`HarborRuntimeError` if ``deps`` is missing required
        keys or the spec list is empty.
        """
        scheduler = deps.get("scheduler")
        if scheduler is None:
            raise HarborRuntimeError(
                "WebhookTrigger.init(deps) requires deps['scheduler']; "
                "lifespan must build the Scheduler before initialising triggers"
            )
        raw_specs: Iterable[Any] | None = deps.get("webhook_specs")
        if raw_specs is None:
            raise HarborRuntimeError(
                "WebhookTrigger.init(deps) requires deps['webhook_specs']: "
                "an iterable of WebhookSpec rows"
            )
        parsed: list[WebhookSpec] = []
        for raw in raw_specs:
            spec = raw if isinstance(raw, WebhookSpec) else WebhookSpec.model_validate(raw)
            if not spec.current_secret:
                raise HarborRuntimeError(
                    f"WebhookSpec(trigger_id={spec.trigger_id!r}) has empty "
                    "current_secret; HMAC verification requires a non-empty key"
                )
            parsed.append(spec)
        if not parsed:
            raise HarborRuntimeError(
                "WebhookTrigger.init(deps) received empty deps['webhook_specs']; "
                "at least one WebhookSpec is required"
            )
        self._scheduler = scheduler
        self._audit_sink = deps.get("audit_sink")
        self._specs = parsed
        self._nonce_caches = {spec.trigger_id: _NonceLRU(spec.nonce_lru_size) for spec in parsed}

    def start(self) -> None:
        """No-op: webhook triggers are pull (FastAPI dispatches inbound)."""
        self._running = True

    def stop(self) -> None:
        """No-op: nothing to drain. Routes stop serving when the app shuts down."""
        self._running = False

    def routes(self) -> list[_Route]:
        """Return one FastAPI ``POST`` route per :class:`WebhookSpec.path`.

        Each route delegates to :meth:`_handle_request` for the
        verification gauntlet + enqueue. FastAPI is imported lazily so
        non-serve plugin hosts can still import :mod:`harbor.triggers.webhook`.
        """
        if not self._specs:
            return []
        # Lazy import to keep the module import-light (mirrors
        # :mod:`harbor.triggers.cron` and :mod:`harbor.triggers.manual`).
        from fastapi import APIRouter

        router = APIRouter()
        for spec in self._specs:
            router.add_api_route(
                spec.path,
                self._make_handler(spec),
                methods=["POST"],
                name=f"harbor.triggers.webhook.{spec.trigger_id}",
            )
        return [router]

    def _make_handler(
        self,
        spec: WebhookSpec,
    ) -> Callable[..., Any]:
        """Build a FastAPI-introspectable route handler bound to ``spec``.

        FastAPI inspects the handler's signature to build the OpenAPI
        schema + request-binding glue. The handler must therefore take
        only request-binding parameters (``Request``) -- no closure-level
        defaults (FastAPI mis-interprets those as query-string fields).
        We close over ``spec`` lexically and expose only ``Request``.

        ``Request`` is imported at module-top so FastAPI's annotation
        resolver (which looks in the function's ``__globals__`` -- i.e.
        this module's globals) can find it. A method-local
        ``from fastapi import Request`` would be invisible to that
        resolver and cause FastAPI to treat ``request`` as a missing
        query parameter (HTTP 422).
        """

        async def handler(request: Request) -> dict[str, Any]:
            return await self._handle_request(request, spec)

        return handler

    @staticmethod
    def sign(secret: bytes, timestamp: int, raw_body: bytes) -> str:
        """Return the lowercase hex HMAC-SHA256 over ``f"{ts}.{body}"``.

        Public for unit-style smoke tests + caller-side signing helpers.
        Stripe-convention signed-payload format (design §6.4).
        """
        signed_payload = f"{timestamp}.{raw_body.decode('utf-8', errors='strict')}"
        return hmac.new(secret, signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def verify(
        *,
        current_secret: bytes,
        previous_secret: bytes | None,
        timestamp: int,
        raw_body: bytes,
        signature: str,
    ) -> bool:
        """Verify ``signature`` against ``current`` then ``previous``.

        Constant-time compare via :func:`hmac.compare_digest` for both
        the current-key path and the rotation-grace fallback. Returns
        ``True`` on either match, ``False`` if both fail.
        """
        expected_current = WebhookTrigger.sign(current_secret, timestamp, raw_body)
        if hmac.compare_digest(expected_current, signature):
            return True
        if previous_secret is None:
            return False
        expected_previous = WebhookTrigger.sign(previous_secret, timestamp, raw_body)
        return hmac.compare_digest(expected_previous, signature)

    @staticmethod
    def idempotency_key(trigger_id: str, raw_body: bytes) -> str:
        """Compute ``sha256(trigger_id || body_hash)`` (design §6.4)."""
        body_hash = hashlib.sha256(raw_body).hexdigest()
        payload = f"{trigger_id}|{body_hash}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def _handle_request(self, request: Any, spec: WebhookSpec) -> dict[str, Any]:
        """Run the verification gauntlet + enqueue for one inbound POST.

        Returns a small JSON-serializable dict on success
        (``{"accepted": true, "idempotency_key": "..."}``). On failure
        raises :class:`fastapi.HTTPException` with the appropriate status
        code (401 for sig/timestamp issues, 409 for replay, 400 for
        malformed JSON when the default extractor is used).
        """
        from fastapi import HTTPException, status

        raw_body = await request.body()
        headers = {k.lower(): v for k, v in request.headers.items()}

        signature = headers.get(spec.signature_header.lower())
        ts_raw = headers.get(spec.timestamp_header.lower())
        if signature is None or ts_raw is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing_headers",
            )

        # Parse timestamp (must be ASCII int seconds; reject anything else).
        try:
            timestamp = int(ts_raw)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid_timestamp",
            ) from None

        # Window check (FR-9.2).
        now = int(time.time())
        if abs(now - timestamp) > spec.timestamp_window_seconds:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="timestamp_out_of_window",
            )

        # HMAC verify (FR-9.1, FR-9.4 dual-secret rotation grace).
        if not self.verify(
            current_secret=spec.current_secret,
            previous_secret=spec.previous_secret,
            timestamp=timestamp,
            raw_body=raw_body,
            signature=signature,
        ):
            await self._emit_audit(
                spec=spec,
                kind="webhook_signature_invalid",
                detail={"timestamp": timestamp, "path": spec.path},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid_signature",
            )

        # Nonce check (FR-9.3).
        cache = self._nonce_caches[spec.trigger_id]
        first_sight = await cache.check_and_record((spec.trigger_id, signature, timestamp))
        if not first_sight:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="duplicate_nonce",
            )

        # Extract params (default = body-as-JSON).
        params = self._extract_params(spec, raw_body, headers)

        # Enqueue (FR-9.5 success path).
        if self._scheduler is None:
            raise HarborRuntimeError(
                "WebhookTrigger received a request before init(deps) wired the Scheduler"
            )
        idem = self.idempotency_key(spec.trigger_id, raw_body)
        self._scheduler.enqueue(
            graph_id=spec.graph_id,
            params=params,
            idempotency_key=idem,
        )
        return {"accepted": True, "idempotency_key": idem}

    @staticmethod
    def _extract_params(
        spec: WebhookSpec,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        """Run the spec's extractor (default = JSON-decode the raw body).

        Default behaviour: decode UTF-8, ``json.loads``. Empty body → ``{}``.
        Malformed JSON raises 400 (caller error: signature was good but
        the body is unparseable).
        """
        from fastapi import HTTPException, status

        extractor: Callable[[bytes, dict[str, str]], dict[str, Any]] | None = spec.params_extractor
        if extractor is not None:
            return extractor(raw_body, headers)
        if not raw_body:
            return {}
        try:
            decoded: Any = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"invalid_body_json: {exc}",
            ) from exc
        if not isinstance(decoded, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="body_not_json_object",
            )
        # ``json.loads`` returns ``Any``; ``isinstance(..., dict)`` narrows
        # the container shape but the value types remain ``Any``. Build a
        # fresh ``dict[str, Any]`` so the return type is fully known.
        result: dict[str, Any] = {}
        for k, v in decoded.items():  # pyright: ignore[reportUnknownVariableType]
            result[str(k)] = v  # pyright: ignore[reportUnknownArgumentType]
        return result

    async def _emit_audit(
        self,
        *,
        spec: WebhookSpec,
        kind: str,
        detail: dict[str, Any],
    ) -> None:
        """Emit a :class:`BosunAuditEvent` to ``deps['audit_sink']`` if present.

        Silent no-op when the audit sink was not provided in :meth:`init`
        -- the request still 401s, the audit just lives only in the
        structured request log. Phase 2 wiring guarantees the sink is
        present in the lifespan-built deps.
        """
        sink = self._audit_sink
        now = datetime.now(UTC)
        run_id_str = f"webhook:{spec.trigger_id}"
        if sink is None:
            _structlog.info(
                "webhook_request",
                run_id=run_id_str,
                step=0,
                ts=now.isoformat(),
                pack_id="harbor.triggers.webhook",
                pack_version="1.0",
                fact={"kind": kind, **detail},
            )
            return
        # ProvenanceBundle (FR-55, AC-11.2): webhook signature failures
        # are system-emitted at the trigger boundary; origin="system"
        # with the trigger pack id as source matches the runtime
        # convention for non-Bosun-pack audit emitters.
        provenance: dict[str, Any] = {
            "origin": "system",
            "source": "harbor.triggers.webhook",
            "run_id": run_id_str,
            "step": 0,
            "confidence": 1.0,
            "timestamp": now.isoformat(),
        }
        ev = BosunAuditEvent(
            run_id=run_id_str,
            step=0,
            ts=now,
            pack_id="harbor.triggers.webhook",
            pack_version="1.0",
            fact={"kind": kind, **detail},
            provenance=provenance,
        )
        try:
            await sink.write(ev)
        except Exception:  # pragma: no cover - defensive; audit must not 500 the request
            _logger.exception(
                "audit-sink write failed for trigger_id=%s kind=%s",
                spec.trigger_id,
                kind,
            )
