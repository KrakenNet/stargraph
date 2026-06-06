# SPDX-License-Identifier: Apache-2.0
"""Lifespan singleton holders for the serve layer.

Phase 1 POC shipped only the two singletons that
:mod:`stargraph.serve.lifecycle` actually consults from a request handler:
the configured :class:`~stargraph.artifacts.base.ArtifactStore` and the
audit sink (:class:`~stargraph.audit.AuditSink`-shaped). Both are
:class:`contextvars.ContextVar` holders so the FastAPI lifespan can
:meth:`set` them once at startup and any awaitable on the same task
tree can :meth:`get` the live instance without threading an explicit
dependency through every helper function.

Phase 2 (task 2.30) adds the lifespan-singleton Nautilus
:class:`Broker` consumed by :func:`stargraph.tools.nautilus.broker_request`
and :class:`stargraph.nodes.nautilus.BrokerNode` (design §8.3, §8.5):

* ``_broker_var`` -- the live :class:`nautilus.Broker` instance set on
  startup, cleared on teardown.
* :func:`current_broker` -- the typed accessor; raises
  :class:`StargraphRuntimeError` when the lifespan is inactive (or when
  ``nautilus.yaml`` was missing and the broker was never wired).

Future Phase 2 extensions (per design §3.1 / §1177):

* ``_jwks_cache_var`` -- the JWKS cache shared by ``BearerJwtProvider``
  instances across requests (design §5.2.1).
* Registry handles, OpenAPI cache, etc. as the api/auth surfaces grow.

Design refs: §3.1 (Lifespan row), §6.1 (lifespan singletons),
§8.3 (Broker singleton wiring), §8.5 (current_broker accessor),
§9.4 (audit emission flow).
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from stargraph.errors import StargraphRuntimeError

if TYPE_CHECKING:
    from nautilus import Broker  # pyright: ignore[reportMissingTypeStubs]

    from stargraph.artifacts.base import ArtifactStore

__all__ = [
    "_artifact_store_var",
    "_audit_sink_var",
    "_broker_var",
    "current_broker",
]


# Lifespan-singleton ArtifactStore. Set by the FastAPI lifespan factory
# (Phase 2 task 2.30) to the configured FilesystemArtifactStore (or
# whatever provider the deployment selects). Read by request handlers
# (e.g. GET /artifacts/{artifact_id}) and by the engine's WriteArtifactNode
# context wiring once the GraphRun.start() entry point pulls this value.
_artifact_store_var: ContextVar[ArtifactStore | None] = ContextVar(
    "_artifact_store_var",
    default=None,
)


# Lifespan-singleton audit sink. Typed as ``Any | None`` rather than
# ``AuditSink | None`` to avoid forcing :mod:`stargraph.audit` into the
# import graph at module load time -- the sink is only ever consumed
# via duck typing (``await sink.write(event)`` / ``await sink.close()``)
# in the POC. Phase 2 tightens the annotation to ``AuditSink | None``
# once the lifespan factory (task 2.30) is the only assignment site.
_audit_sink_var: ContextVar[Any | None] = ContextVar(
    "_audit_sink_var",
    default=None,
)


# Lifespan-singleton Nautilus Broker (design §8.3). Set on FastAPI
# lifespan startup from ``<stargraph-config>/nautilus.yaml`` (or left
# ``None`` when the YAML is absent -- Nautilus is optional and the
# warning emitted at startup is the operator's signal). Read by
# :class:`stargraph.nodes.nautilus.BrokerNode` and
# :func:`stargraph.tools.nautilus.broker_request` via :func:`current_broker`.
_broker_var: ContextVar[Broker | None] = ContextVar(
    "_broker_var",
    default=None,
)


def current_broker() -> Broker:
    """Return the lifespan-singleton :class:`nautilus.Broker` (design §8.5).

    Raises
    ------
    StargraphRuntimeError
        If the broker contextvar is unset -- i.e. the call is happening
        outside an active FastAPI lifespan, or the lifespan factory
        skipped broker construction (e.g. ``<stargraph-config>/nautilus.yaml``
        not found, which the lifespan logs as a warning rather than a
        fatal error).

    Notes
    -----
    The accessor is intentionally synchronous: it only reads the
    contextvar -- there is no I/O. Callers (typically async tool
    bodies and node ``execute`` methods) invoke it without ``await``.
    """
    broker = _broker_var.get()
    if broker is None:
        raise StargraphRuntimeError(
            "Broker not initialized; ensure lifespan is active and "
            "<stargraph-config>/nautilus.yaml is present",
            broker=None,
        )
    return broker
