# SPDX-License-Identifier: Apache-2.0
"""``harbor serve`` subcommand -- POC uvicorn boot (FR-26, AC-7.1, design §3.1).

Boots the Harbor serve FastAPI app via :func:`harbor.serve.api.create_app`
under :func:`uvicorn.run`. The active deployment profile is resolved by
:func:`harbor.serve.profiles.select_profile`; the ``--profile`` flag, when
supplied, overrides the env-var rung by setting ``HARBOR_PROFILE`` before
the call (the POC ``select_profile`` honors only the env-var rung -- the
CLI rung deferral is documented in :mod:`harbor.serve.profiles`).

POC scope (per task 1.28 + VE2-1 fix):

* The ``deps`` dict is populated with a real in-process
  :class:`~harbor.serve.scheduler.Scheduler` plus empty ``runs`` and
  ``broadcasters`` registries so ``POST /v1/runs`` returns a structured
  202 instead of a 500-on-``KeyError``. The Scheduler is started /
  stopped via a small FastAPI lifespan callable so the consumer task
  is alive whenever the app accepts requests. Phase 2 task 2.30 swaps
  this stub set for the full lifespan singleton wiring (Scheduler +
  Checkpointer + ArtifactStore + audit sink).
* Sync :func:`uvicorn.run` (not the programmatic ``Server`` + ``Config``
  variant). Phase 2 may switch to programmatic boot for graceful
  shutdown hooks (SIGTERM -> drain in-flight runs).
* ``log_level="info"`` is uvicorn's friendly default; cleared profiles
  may want a quieter level later.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
import uvicorn

from harbor.artifacts.fs import FilesystemArtifactStore
from harbor.checkpoint.sqlite import SQLiteCheckpointer
from harbor.errors import HarborRuntimeError, ProfileViolationError
from harbor.graph.definition import Graph
from harbor.ir._models import IRDocument
from harbor.registry.stores import StoreRegistry
from harbor.registry.tools import ToolRegistry
from harbor.serve.api import create_app
from harbor.serve.history import RunHistory
from harbor.serve.lifecycle import broker_lifespan
from harbor.serve.profiles import select_profile
from harbor.serve.scheduler import Scheduler

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastapi import FastAPI

__all__ = ["cmd"]


def cmd(
    profile: str = typer.Option(
        "oss-default",
        "--profile",
        help="Deployment profile name (oss-default | cleared).",
    ),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Bind host for uvicorn.",
    ),
    port: int = typer.Option(
        8000,
        "--port",
        help="Bind port for uvicorn.",
    ),
    db_path: Annotated[
        Path | None,
        typer.Option(
            "--db",
            help=(
                "SQLite checkpointer DB path. When unset, a temp "
                "file is used (per-process; not durable across "
                "restarts). Phase 3 wires this from harbor.toml."
            ),
        ),
    ] = None,
    audit_log: Annotated[
        Path | None,
        typer.Option(
            "--audit-log",
            help=(
                "JSONL audit-log path; passed to RunHistory for the "
                "``run_event_offsets`` index. When unset the index "
                "is empty and ``RunHistory.get_event_offset`` "
                "returns ``None`` for all lookups."
            ),
        ),
    ] = None,
    allow_pack_mutation: Annotated[
        bool,
        typer.Option(
            "--allow-pack-mutation",
            help=(
                "Permit at-runtime Bosun pack mutation (developer "
                "convenience). FORBIDDEN under --profile cleared "
                "(FR-32, design §11.1, §15) -- the cleared startup "
                "gate exits non-zero with ProfileViolationError."
            ),
        ),
    ] = False,
    allow_side_effects: Annotated[
        bool,
        typer.Option(
            "--allow-side-effects",
            help=(
                "Permit nodes/tools declaring ``side_effects in "
                "{write, external}`` to execute (developer "
                "convenience). FORBIDDEN under --profile cleared "
                "(FR-32, FR-68, design §11.1, §15). Defense in "
                "depth: the engine still REFUSES write/external "
                "side effects under cleared regardless of this "
                "flag (FR-68)."
            ),
        ),
    ] = False,
    graph_paths: Annotated[
        list[Path] | None,
        typer.Option(
            "--graph",
            help=(
                "IR YAML graph to load and register at boot "
                "(repeatable). The graph's ``id`` (e.g. "
                "``graph:sdw-pipeline``) is the key downstream "
                "POST /v1/runs uses; submitting a run for an "
                "unregistered graph_id falls back to the synthetic "
                "POC RunSummary (status=done, no real execution). "
                "Phase 3 task 2.30 wires this from harbor.toml."
            ),
        ),
    ] = None,
    lm_url: Annotated[
        str | None,
        typer.Option(
            "--lm-url",
            help="LLM endpoint URL for DSPy nodes (OpenAI-compatible). Pair with --lm-model.",
        ),
    ] = None,
    lm_model: Annotated[
        str | None,
        typer.Option(
            "--lm-model",
            help="LLM model identifier (e.g. gpt-oss:20b). Required if --lm-url is set.",
        ),
    ] = None,
    lm_key: Annotated[
        str,
        typer.Option(
            "--lm-key",
            help="API key for the LLM endpoint. Defaults to 'placeholder' (works for ollama).",
        ),
    ] = "placeholder",
    lm_timeout: Annotated[
        int,
        typer.Option(
            "--lm-timeout",
            help="LLM call timeout in seconds.",
        ),
    ] = 60,
) -> None:
    """Boot the Harbor serve FastAPI app under uvicorn (POC).

    The ``--profile`` flag is forwarded to
    :func:`harbor.serve.profiles.select_profile` via the
    ``HARBOR_PROFILE`` env var (the POC selector reads only the env-var
    rung; the CLI rung deferral is signposted in that module).

    Builds a stub dependency set so the POC routes return structured
    responses without needing the full Phase 2 lifespan factory:

    * ``scheduler`` -- a real :class:`Scheduler` instance; started by
      the FastAPI lifespan on app startup, stopped on shutdown.
    * ``runs`` -- empty ``dict[str, GraphRun]`` registry (POC; Phase 2
      task 2.30 swaps in Checkpointer-backed lookup).
    * ``broadcasters`` -- empty ``dict[str, EventBroadcaster]`` registry
      (populated when a run starts in Phase 2).
    * ``run_history`` -- a :class:`RunHistory` over the SQLite
      Checkpointer DB; powers the ``GET /v1/runs`` paginated list
      route (task 2.15). When ``--db`` is unset a per-process temp
      file is created so the route returns an empty page rather than
      500ing.
    """
    os.environ["HARBOR_PROFILE"] = profile
    selected = select_profile()

    # Configure DSPy LM up front so any --graph that uses DSPy nodes finds
    # a real endpoint at boot. _configure_lm is a no-op when both flags are
    # None; raises typer.BadParameter if exactly one is set.
    # Local import: cli cycle.
    from harbor.cli.run import _configure_lm  # pyright: ignore[reportPrivateUsage]

    _configure_lm(lm_url, lm_model, lm_key, lm_timeout)

    # Profile-conditional startup gate (task 2.37, FR-32, FR-68, AC-4.2,
    # design §11.1, §15). Cleared profile FORBIDS the two boot-time
    # escape hatches; raise ProfileViolationError BEFORE any I/O so
    # operators see a clear non-zero exit on stderr instead of the
    # gate firing mid-bootstrap with a partial state. Defense-in-depth
    # against `--allow-side-effects` is the engine-side refusal at the
    # node-executor boundary (FR-68 — surfaced as a separate follow-up
    # if not yet wired here, since the engine's side-effect classifier
    # is the canonical gate; this CLI gate stops the misconfiguration
    # at boot, not the runtime fall-through).
    if selected.name == "cleared":
        if allow_side_effects:
            msg = "--allow-side-effects not permitted under cleared profile"
            raise ProfileViolationError(
                msg,
                profile="cleared",
                flag="--allow-side-effects",
            )
        if allow_pack_mutation:
            msg = "--allow-pack-mutation not permitted under cleared profile"
            raise ProfileViolationError(
                msg,
                profile="cleared",
                flag="--allow-pack-mutation",
            )

    # POC: use a temp DB when no path was supplied so the run_history
    # wiring is always live (the GET /v1/runs route otherwise returns
    # an empty page on a missing deps key, which is harmless but
    # surprising). Phase 3 reads ``harbor.toml: serve.checkpoint.path``
    # via the config loader (task 1.27) and feeds it here.
    if db_path is None:
        tmpdir = Path(tempfile.mkdtemp(prefix="harbor-serve-"))
        db_path = tmpdir / "checkpoint.sqlite"
    else:
        tmpdir = Path(tempfile.mkdtemp(prefix="harbor-serve-"))
    checkpointer = SQLiteCheckpointer(db_path)
    # POC: artifact store roots under a sibling temp dir alongside the
    # checkpointer DB so a single ``--db <path>`` flag colocates state.
    # Phase 3 task 2.30 reads this from ``harbor.toml: artifacts.root``.
    artifact_root = tmpdir / "artifacts"
    artifact_store = FilesystemArtifactStore(artifact_root)

    scheduler = Scheduler()
    # ``run_history`` is initialized after the checkpointer's bootstrap
    # so its aiosqlite connection is alive; populated inside the
    # lifespan body and reaped on shutdown.
    #
    # ``checkpointer`` is exposed so the resume / counterfactual routes
    # (task 2.17) can drive ``GraphRun.resume`` / ``GraphRun.counterfactual``
    # against the same persistent store the dispatcher writes through.
    # ``artifact_store`` powers the artifact list / get routes.
    # ``graphs`` and ``registry`` are POC-empty -- the lifespan factory
    # in Phase 3 task 2.30 wires the pluggy-loaded registries here so
    # the ``GET /v1/graphs`` and ``GET /v1/registry/{kind}`` routes
    # surface the real plugin manifest contents instead of empty lists.
    # Graph + node registry. Each --graph PATH is loaded as IR YAML,
    # validated via IRDocument.model_validate, wrapped in Graph, and
    # registered under its ir.id. The dispatcher (_drive_real_run)
    # reads deps["node_registry"][graph_id] to resolve each NodeSpec's
    # `kind: "module:Class"` to a real callable; without this it
    # raises KeyError("no node implementation registered ...") on the
    # first dispatched node. _build_node_registry is reused from the
    # `harbor run` CLI so serve + run share the same import-and-wire
    # path (matching FR-1 invariant: identical IR -> identical
    # behaviour across surfaces).
    # Local import: cli cycle.
    from harbor.cli.run import _build_node_registry  # pyright: ignore[reportPrivateUsage]

    graphs: dict[str, Graph] = {}
    node_registries: dict[str, dict[str, Any]] = {}
    for path in graph_paths or []:
        import yaml as _yaml  # local import: yaml is an indirect dep

        ir_doc = IRDocument.model_validate(_yaml.safe_load(path.read_text()))
        graph = Graph(ir=ir_doc)
        graphs[ir_doc.id] = graph
        node_registries[ir_doc.id] = _build_node_registry(
            ir_doc.nodes,
            ir_dir=path.parent.resolve(),
        )
        typer.echo(
            f"  loaded graph: {ir_doc.id}  "
            f"(nodes={len(ir_doc.nodes)}, hash={graph.graph_hash[:12]}, "
            f"path={path})"
        )

    deps: dict[str, Any] = {
        "scheduler": scheduler,
        "runs": {},
        "broadcasters": {},
        "run_history": None,
        "checkpointer": checkpointer,
        "artifact_store": artifact_store,
        "graphs": graphs,
        "node_registry": node_registries,
        "registry": {
            "tools": ToolRegistry(),
            "stores": StoreRegistry(),
        },
    }

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        """Start the Scheduler + Checkpointer + RunHistory on startup.

        POC: bootstrap the Checkpointer first (creates the
        ``runs_history`` + ``pending_runs`` tables via migration 002),
        then construct :class:`RunHistory` over its aiosqlite
        connection and stash on ``deps["run_history"]`` so the
        ``GET /v1/runs`` route handler can read it. Phase 3 task 2.30
        expands this further to wire the audit sink + ArtifactStore +
        webhook trigger machinery.
        """
        await checkpointer.bootstrap()
        # Reach into the Checkpointer's private connection: single-
        # writer SQLite WAL means RunHistory must share this connection
        # rather than open its own (would deadlock on the writer lock).
        # The Phase 3 lifespan factory (task 2.30) hides this with a
        # clean adapter API once the Checkpointer Protocol grows a
        # ``connection`` accessor.
        db = checkpointer._db  # pyright: ignore[reportPrivateUsage]
        if db is None:
            msg = "checkpointer bootstrap did not populate _db"
            raise HarborRuntimeError(msg)
        run_history = RunHistory(db, jsonl_audit_path=audit_log)
        # Bootstrap is idempotent -- the migration runner already
        # applied the DDL, but this also builds the JSONL offsets
        # index when an audit log path was supplied.
        await run_history.bootstrap()
        deps["run_history"] = run_history
        # Bootstrap the artifact store root + NFS-refusal probe before
        # the artifacts routes start serving requests.
        await artifact_store.bootstrap()
        # Inject deps into the scheduler so its dispatcher can resolve
        # ``deps["graphs"]`` -> :class:`Graph` and drive a real
        # :class:`GraphRun` through the loop. Without this hand-off the
        # dispatcher falls back to a synthetic POC ``RunSummary`` even
        # when the lifespan factory has loaded graphs.
        scheduler.set_deps(deps)
        # Wire the post-bootstrap RunHistory into the scheduler so
        # _record_history_pending writes the runs_history row on
        # enqueue. Without this, POST /v1/runs queues a row that
        # never lands in history -> GET /v1/runs is empty.
        scheduler.set_run_history(run_history)
        await scheduler.start()
        # Compose the Nautilus :class:`Broker` lifespan inside the
        # outer scheduler / checkpointer / artifact-store lifespan so
        # the broker is wired before request handlers start serving
        # and torn down before the rest of the deps go away. Missing
        # ``<config>/nautilus.yaml`` is a soft-fail: ``broker_lifespan``
        # logs a warning, leaves the contextvar unset, and yields
        # normally so the app still boots.
        try:
            async with broker_lifespan():
                yield
        finally:
            await scheduler.stop()
            await checkpointer.close()

    app = create_app(selected, deps=deps, lifespan=_lifespan)
    cfg = uvicorn.Config(app, host=host, port=port, log_level="info")
    uvicorn.Server(cfg).run()
