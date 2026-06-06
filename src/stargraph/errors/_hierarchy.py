# SPDX-License-Identifier: Apache-2.0
"""Stargraph exception hierarchy.

All Stargraph errors inherit from :class:`StargraphError`, which carries a
human-readable ``message`` plus arbitrary keyword ``context`` for
structured logging. Subclasses are pass-through: they exist purely so
callers can pattern-match on category.

Engine-specific runtime errors (:class:`AdapterFallbackError`,
:class:`CapabilityError`, :class:`IRValidationError`,
:class:`IncompatibleSklearnVersion`, :class:`IncompatibleModelHashError`,
:class:`MLNodeError`) inherit from :class:`StargraphRuntimeError` so a
single ``except StargraphRuntimeError`` catches every engine-side runtime
failure while :class:`StargraphError` remains the catch-all for both
validation and runtime categories. Structured field expectations per
design §7 error matrix are documented on each subclass.
"""

from __future__ import annotations

from typing import Any


class StargraphError(Exception):
    """Base class for all Stargraph exceptions.

    Stores ``message`` (the user-facing string) and ``context`` (a dict
    populated from keyword arguments) for structured logging downstream.
    """

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message: str = message
        self.context: dict[str, Any] = context


class ValidationError(StargraphError):
    """Raised when input fails Stargraph validation rules."""


class PluginLoadError(StargraphError):
    """Raised when a Stargraph plugin cannot be discovered or imported."""


class StargraphRuntimeError(StargraphError):
    """Raised for runtime failures inside Stargraph.

    Renamed to avoid shadowing builtin RuntimeError. Acts as the engine
    runtime base: every engine-emitted runtime failure inherits from
    this so operators can ``except StargraphRuntimeError`` and catch the
    whole category at once.
    """


class CheckpointError(StargraphError):
    """Raised when checkpoint write/read fails.

    Per design §7: callers populate ``context`` with one or more of
    ``path``, ``reason`` (e.g. ``"network-fs"``, ``"concurrent-writer"``,
    ``"cf-prefix-hash-refused"``), ``expected_hash``, ``actual_hash``,
    ``migrate_available`` (FR-17, FR-20).
    """


class ReplayError(StargraphError):
    """Raised when replaying a recorded run fails.

    Per design §7: raised when a replayed step would re-execute a node
    whose ``side_effects`` ∈ ``{write, external}`` without an explicit
    replay policy (FR-21). Callers populate ``context`` with
    ``run_id``, ``node_id``, ``side_effects``.
    """


class AdapterFallbackError(StargraphRuntimeError):
    """Raised when an adapter silently degrades to a fallback path.

    Per design §7: emitted via the DSPy logging filter when the adapter
    would otherwise fall back to ``JSONAdapter`` on schema mismatch
    (FR-6, force-loud at the seam). Callers populate ``context`` with
    ``adapter`` (e.g. ``"dspy"``), ``original_adapter``,
    ``fallback_adapter``, ``signature``.
    """


class CapabilityError(StargraphRuntimeError):
    """Raised when a tool requires a capability that has not been granted.

    Per design §7 / NFR-7: cleared deployments default-deny; unscoped
    grants are refused. Callers populate ``context`` with
    ``capability`` (e.g. ``"fs.read:/workspace/*"``), ``tool_id``,
    ``deployment``.
    """


class IRValidationError(StargraphRuntimeError):
    """Raised at ``Graph.__init__`` for compile-time IR safety violations.

    Per design §7: parallel-write without a reducer (FR-11), or a
    ``race``/``any`` branch with ``side_effects`` ∈ ``{write, external}``
    when ``allow_unsafe_cancel`` is not set (FR-12). Callers populate
    ``context`` with ``node_ids``, ``violation`` (e.g.
    ``"parallel-write-no-reducer"``, ``"unsafe-cancel"``), and any
    relevant IR pointers.
    """


class IncompatibleSklearnVersion(StargraphRuntimeError):  # noqa: N818  -- name fixed by design §7
    """Raised on ``__sklearn_version__`` skew when loading a pickled model.

    Per design §7 / FR-30: guards the sklearn ML-node antipattern -- a
    pickle saved under one sklearn version is not safe to load under
    another. Callers populate ``context`` with ``model_id``,
    ``expected_version``, ``actual_version``, ``model_path``.
    """


class MLNodeError(StargraphRuntimeError):
    """Raised by :class:`stargraph.nodes.ml.MLNode` for ML-runtime gate violations.

    Per design §3.9.2 / FR-30: covers the default-deny pickle gate
    (``allow_unsafe_pickle=False``), the XGBoost ``.bin`` rejection
    (the binary format was removed in 3.1), and any other loader
    contract violation that is not already covered by the more
    specific :class:`IncompatibleSklearnVersion` /
    :class:`IncompatibleModelHashError` siblings. Callers populate
    ``context`` with ``model_id``, ``version``, ``runtime``, and any
    relevant ``file_uri`` / ``reason`` keys.
    """


class SimulationError(StargraphRuntimeError):
    """Raised by :meth:`stargraph.graph.Graph.simulate` for fixture-input violations.

    Per design §3.10 / FR-9: ``simulate`` runs the graph against synthetic
    node outputs (the ``fixtures`` mapping). Every IR node must have a
    corresponding fixture entry -- there is no fall-through to live tool
    or LLM calls inside ``simulate`` (FR-6 force-loud), so a missing
    fixture cannot be silently treated as ``None``. Callers populate
    ``context`` with ``node_id`` and ``violation`` (e.g.
    ``"missing-fixture"``).
    """


class IncompatibleModelHashError(StargraphRuntimeError):
    """Raised when a model file's content hash does not match the registry.

    Per design §7 / FR-31: defends against silent model-file swaps in
    the SQLite tiny-model registry. Callers populate ``context`` with
    ``model_id``, ``expected_sha256``, ``actual_sha256``,
    ``model_path``.
    """


class StoreError(StargraphRuntimeError):
    """Base class for knowledge-store runtime failures.

    Per stargraph-knowledge design §4.5: every store-side runtime failure
    (vector / graph / doc / memory / fact) inherits from this so a
    single ``except StoreError`` catches the whole category. Callers
    populate ``context`` with at minimum ``store`` (e.g. ``"lancedb"``,
    ``"ryugraph"``, ``"sqlite"``) and ``path``/``table``/``namespace`` as
    relevant.
    """


class IncompatibleEmbeddingHashError(StoreError):
    """Raised on embedding-content-hash drift between bootstrap and re-entry.

    Per stargraph-knowledge design §4.5 / FR-8: the 5-tuple drift gate
    ``(model_id, revision, content_hash, ndims, schema_v)`` written to
    LanceDB table-level metadata at ``bootstrap()`` must match on every
    re-entry. Callers populate ``context`` with ``model_id``,
    ``expected_content_hash``, ``actual_content_hash``, ``ndims``,
    ``schema_v``, ``table``.
    """


class EmbeddingModelHashMismatch(StoreError):  # noqa: N818
    """Raised when the embedding model file hash does not match the pinned value.

    Per stargraph-knowledge design §4.5 / FR-8: defends against silent
    embedding-model-file swaps (e.g. MiniLM safetensors sha256 mismatch).
    Callers populate ``context`` with ``model_id``, ``expected_sha256``,
    ``actual_sha256``, ``model_path``.
    """


class IncompatibleSchemaError(StoreError):
    """Raised when a store's persisted schema is incompatible with the loaded code.

    Per stargraph-knowledge design §4.5 / FR-12: store schema versioning
    rejects unknown ``schema_v`` values or unsupported schema shapes.
    Callers populate ``context`` with ``store``, ``table``,
    ``expected_schema_v``, ``actual_schema_v``.
    """


class IncompatibleMigrationError(StoreError):
    """Raised when a requested migration cannot be applied to the current schema.

    Per stargraph-knowledge design §4.5 / FR-12: ``migrate()`` v1 rejects
    type narrows + renames; only add-nullable-column is forward-safe
    in Lance. Callers populate ``context`` with ``store``, ``table``,
    ``from_schema_v``, ``to_schema_v``, ``violation``.
    """


class MigrationNotSupported(StoreError):  # noqa: N818
    """Raised when a store does not implement the requested migration path.

    Per stargraph-knowledge design §4.5 / FR-12: a backend may legitimately
    decline a migration (e.g. RyuGraph does not yet support online column
    drops). Callers populate ``context`` with ``store``, ``operation``,
    ``reason``.
    """


class UnportableCypherError(StoreError):
    """Raised when Cypher fails the portable-subset linter.

    Per stargraph-knowledge design §4.5 / FR-17: queries must run on both
    RyuGraph and Neo4j 5; the linter bans constructs outside the agreed
    subset (e.g. unbounded variable-length paths, APOC/GDS calls).
    Callers populate ``context`` with ``cypher``, ``violation``,
    ``rule``.
    """


class NamespaceConflictError(PluginLoadError):
    """Raised when two skills or stores claim the same namespace.

    Per stargraph-knowledge design §4.5: namespaces (skill ids, store
    capability strings) must be globally unique at plugin-load time.
    Callers populate ``context`` with ``namespace``, ``existing_owner``,
    ``new_owner``.
    """


class MemoryScopeError(StoreError):
    """Raised when a memory read/write scope is malformed or unauthorized.

    Per stargraph-knowledge design §4.5 / FR-12: memory scoping writes at
    ``(user, session, agent)`` and widens reads to ``(user, agent)`` /
    ``(user)``; missing components or unauthorized widening rejects.
    Callers populate ``context`` with ``scope``, ``operation``,
    ``violation``.
    """


class ConsolidationRuleError(StoreError):
    """Raised when a consolidation CLIPS rule emits an invalid typed delta.

    Per stargraph-knowledge design §4.5: Mem0-style typed deltas must be
    one of ``ADD|UPDATE|DELETE|NOOP`` with the required provenance
    fields populated. Callers populate ``context`` with ``rule_id``,
    ``delta_type``, ``violation``.
    """


class FactConflictError(StoreError):
    """Raised when promoted facts conflict with existing graph state.

    Per stargraph-knowledge design §4.5 / FR-17: KG promotion is asymmetric
    one-way; a conflicting fact (contradictory predicate/object for the
    same subject) requires an explicit retraction rule. Callers populate
    ``context`` with ``subject``, ``predicate``, ``existing_object``,
    ``new_object``.
    """


class BroadcasterOverflow(StargraphRuntimeError):  # noqa: N818  -- "Overflow" reads as a state, not an error suffix; matches NumPy/asyncio naming (e.g. ``QueueFull``).
    """Raised when an :class:`EventBroadcaster` per-subscriber buffer overflows.

    Per stargraph-serve-and-bosun design §5.6 + task 2.21: WS subscribers
    have bounded per-connection buffers (size 100). When the broadcaster
    cannot push an event into a subscriber's stream non-blocking
    (``anyio.WouldBlock``), the subscriber is dropped and its iterator
    raises this exception so the WS handler can ``close(1011, "slow
    consumer")``. Distinguishes "slow consumer overflow" from natural
    bus closure / generic runtime errors. Callers populate ``context``
    with ``run_id`` (when known) and ``buffer_size``.
    """


class ArtifactStoreError(StargraphRuntimeError):
    """Base class for :mod:`stargraph.artifacts` runtime failures.

    Per stargraph-serve-and-bosun design §10 / FR-91 / NFR-15: every
    artifact-store provider raises this (or a subclass) on bootstrap
    refusal (NFS/SMB/AFP), atomic-write failure, or sidecar-metadata
    corruption. Sibling of :class:`StoreError` — artifact stores are
    a separate subsystem from knowledge stores. Callers populate
    ``context`` with at minimum ``backend`` (e.g. ``"filesystem"``)
    and ``path`` / ``run_id`` / ``artifact_id`` as relevant.
    """


class ArtifactNotFound(ArtifactStoreError):  # noqa: N818  -- FR-91 typed-not-found contract
    """Raised when :meth:`ArtifactStore.get` / ``delete`` cannot find ``artifact_id``.

    Per stargraph-serve-and-bosun design §10.2: content-addressable
    lookups must distinguish "missing" from "I/O failure" so callers
    can return HTTP 404 vs 500. Callers populate ``context`` with
    ``backend``, ``artifact_id``, and ``path``.
    """


class PackSignatureError(StargraphError):
    """Raised when a Bosun pack JWT fails signature/trust verification (FR-41, FR-43).

    Per stargraph-serve-and-bosun design §7.3 / §7.5 / §17 Decision #4:
    pack-load-time signature verification refuses any of:

    * unknown algorithm at decode (anything other than ``"EdDSA"``),
    * missing or non-matching public key in the trust store,
    * tree-content-hash drift (BLAKE3 / SHA-256 fallback) between
      sign-time and verify-time,
    * TOFU fingerprint mismatch — the sidecar ``<key_id>.pub.pem`` does
      not hash to the value recorded on first use,
    * embedded ``x5c`` JWT header (untrusted certificate-in-JWT —
      rejected at decode time, never trusted),
    * cleared-profile first-sight against a static allow-list that does
      not list the ``key_id``.

    StargraphError-rooted (load-fail surface is operator-facing config, not
    engine runtime — same shape as :class:`PackCompatError`). Callers
    populate ``context`` with at minimum ``key_id`` and ``reason``
    (e.g. ``"alg-not-eddsa"``, ``"tree-hash-mismatch"``,
    ``"fingerprint-mismatch"``, ``"untrusted-key"``,
    ``"x5c-rejected"``).
    """


class ProfileViolationError(StargraphError):
    """Raised when a CLI flag or runtime state violates a deployment profile.

    Per stargraph-serve-and-bosun design §11.1 + §15 / FR-32 / FR-68 / AC-4.2:
    the cleared profile forbids the ``--allow-pack-mutation`` and
    ``--allow-side-effects`` boot-time escape hatches; setting either
    under ``--profile cleared`` raises this at startup BEFORE uvicorn
    binds the listening socket. StargraphError-rooted (config-time
    operator-facing failure, not engine runtime — same shape as
    :class:`PackCompatError` / :class:`PackSignatureError`). Callers
    populate ``context`` with at minimum ``profile`` (e.g. ``"cleared"``)
    and ``flag`` (e.g. ``"--allow-side-effects"``).
    """


class PackCompatError(StargraphError):
    """Raised when a Bosun pack mount's ``requires`` doesn't match the host (FR-39).

    Per stargraph-serve-and-bosun design §3.2 / §7.4 / AC-3.2: rule packs
    declare ``PackMount.requires.stargraph_facts_version`` and
    ``api_version`` they were authored against;
    :func:`stargraph.ir._versioning.check_pack_compat` raises this at
    pack-load time (NOT runtime) so silent runtime drift is impossible
    (FR-6 force-loud). Callers populate ``context`` with ``pack_id``,
    ``field`` (``"stargraph_facts_version"`` or ``"api_version"``),
    ``required``, ``actual``.
    """
