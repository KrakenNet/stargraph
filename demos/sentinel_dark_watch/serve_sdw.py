# SPDX-License-Identifier: Apache-2.0
"""``harbor serve`` wrapper that pins the SDW capability profile.

Mirrors the CVE-rem ``serve_cve_rem.py`` pattern: thin argparse
wrapper around :func:`harbor.serve.api.create_app` that inserts the
engine-side :class:`Capabilities` from
:func:`demos.sentinel_dark_watch.capabilities.build_sdw_capabilities`
into ``deps`` before constructing the FastAPI app.

Includes an APScheduler nightly retrain trigger (02:00 UTC) that
POSTs to ``/v1/runs`` against ``graph:sdw-retrain``.

Usage::

    uv run --no-project python -m demos.sentinel_dark_watch.serve_sdw \
        --host 0.0.0.0 --port 9001
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from demos.sentinel_dark_watch.capabilities import build_sdw_capabilities

# ---------------------------------------------------------------------------
# JSONL audit log writer
# ---------------------------------------------------------------------------

_AUDIT_DIR = Path(__file__).resolve().parent / "data" / "audit"


def _ensure_audit_dir() -> None:
    """Create ``data/audit/`` if it does not exist."""
    _AUDIT_DIR.mkdir(parents=True, exist_ok=True)


def write_audit_event(
    run_id: str,
    node_id: str,
    event: str,
    duration_ms: float = 0.0,
    **extra: Any,
) -> None:
    """Append a single JSONL audit line for *run_id*.

    Each run gets its own file: ``data/audit/{run_id}.jsonl``.
    """
    _ensure_audit_dir()
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "node_id": node_id,
        "event": event,
        "duration_ms": round(duration_ms, 2),
        **extra,
    }
    path = _AUDIT_DIR / f"{run_id}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def main(argv: list[str] | None = None) -> int:
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent / ".env", override=False)

    parser = argparse.ArgumentParser(
        prog="serve_sdw",
        description="harbor serve with the SDW capability profile pinned.",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9001)
    parser.add_argument(
        "--profile",
        default="oss-default",
        choices=("oss-default", "cleared"),
    )
    parser.add_argument(
        "--graph",
        action="append",
        default=None,
        help=(
            "IR YAML graph to load and register at boot (repeatable). "
            "Defaults to both harbor.yaml and retrain.yaml."
        ),
    )
    args = parser.parse_args(argv)

    import os
    import tempfile
    from collections.abc import AsyncGenerator  # noqa: TC003
    from contextlib import asynccontextmanager

    import uvicorn
    from fastapi import FastAPI  # noqa: TC002

    from harbor.artifacts.fs import FilesystemArtifactStore
    from harbor.checkpoint.sqlite import SQLiteCheckpointer
    from harbor.errors import HarborRuntimeError
    from harbor.registry import StoreRegistry, ToolRegistry
    from harbor.serve.api import create_app
    from harbor.serve.history import RunHistory
    from harbor.serve.lifecycle import broker_lifespan
    from harbor.serve.profiles import select_profile
    from harbor.serve.scheduler import Scheduler

    os.environ["HARBOR_PROFILE"] = args.profile
    selected = select_profile()

    # Wire dspy.LM from env.
    from harbor.cli.run import _configure_lm

    lm_url = os.environ.get("LLM_BASE_URL") or None
    lm_model = os.environ.get("LLM_MODEL") or None
    lm_key = os.environ.get("LLM_API_KEY", "no-key")
    lm_timeout = int(os.environ.get("LLM_TIMEOUT_SECONDS", "180"))
    if lm_url and lm_model:
        _configure_lm(lm_url, lm_model, lm_key, lm_timeout)
        print(f"[serve_sdw] dspy.LM → {lm_model} @ {lm_url}")
    else:
        print("[serve_sdw] no LLM_BASE_URL/LLM_MODEL set — DSPy nodes will fail-loud")

    # Nautilus config dir.
    os.environ.setdefault("HARBOR_CONFIG_DIR", str(Path(__file__).parent.resolve()))
    tmpdir = Path(tempfile.mkdtemp(prefix="sdw-serve-"))
    checkpointer = SQLiteCheckpointer(tmpdir / "checkpoint.sqlite")
    artifact_store = FilesystemArtifactStore(tmpdir / "artifacts")
    scheduler = Scheduler()

    # Load + register graphs.
    import yaml as _yaml

    from harbor.cli.run import _build_node_registry
    from harbor.graph.definition import Graph
    from harbor.ir._models import IRDocument

    graph_dir = Path(__file__).parent / "graph"
    default_graphs = [
        str(graph_dir / "harbor.yaml"),
        str(graph_dir / "retrain.yaml"),
    ]
    graph_paths = [Path(p) for p in (args.graph or default_graphs)]
    graphs: dict[str, Any] = {}
    node_registries: dict[str, dict[str, Any]] = {}
    for path in graph_paths:
        ir_doc = IRDocument.model_validate(_yaml.safe_load(path.read_text()))
        graph_obj = Graph(ir=ir_doc)
        graphs[ir_doc.id] = graph_obj
        node_registries[ir_doc.id] = _build_node_registry(
            ir_doc.nodes,
            ir_dir=path.parent.resolve(),
        )
        print(
            f"[serve_sdw] loaded graph {ir_doc.id!r} "
            f"(nodes={len(ir_doc.nodes)}, hash={graph_obj.graph_hash[:12]}, path={path})"
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
        "capabilities": build_sdw_capabilities(),
    }

    # ---- APScheduler nightly retrain trigger (02:00 UTC) ----------------
    def _schedule_nightly_retrain(port: int) -> None:
        """Register a CronTrigger that POSTs /v1/runs for sdw-retrain."""
        try:
            import httpx
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.cron import CronTrigger
        except ImportError:
            print("[serve_sdw] apscheduler/httpx not installed — nightly retrain disabled")
            return

        retrain_scheduler = AsyncIOScheduler()

        async def _trigger_retrain() -> None:
            url = f"http://127.0.0.1:{port}/v1/runs"
            payload = {"graph_id": "graph:sdw-retrain"}
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=30)
                print(f"[serve_sdw] nightly retrain trigger: {resp.status_code}")

        retrain_scheduler.add_job(
            _trigger_retrain,
            trigger=CronTrigger(hour=2, minute=0),
            id="sdw-nightly-retrain",
            replace_existing=True,
        )
        retrain_scheduler.start()
        deps["_retrain_scheduler"] = retrain_scheduler
        print("[serve_sdw] nightly retrain scheduled at 02:00 UTC")

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        await checkpointer.bootstrap()
        db = checkpointer._db  # pyright: ignore[reportPrivateUsage]
        if db is None:
            raise HarborRuntimeError("checkpointer bootstrap failed")
        run_history = RunHistory(db)
        await run_history.bootstrap()
        deps["run_history"] = run_history
        await artifact_store.bootstrap()
        scheduler.set_deps(deps)
        scheduler.set_run_history(run_history)
        await scheduler.start()
        _schedule_nightly_retrain(args.port)
        try:
            async with broker_lifespan():
                yield
        finally:
            retrain_sched = deps.get("_retrain_scheduler")
            if retrain_sched is not None:
                retrain_sched.shutdown(wait=False)
            await scheduler.stop()
            await checkpointer.close()

    app = create_app(selected, deps=deps, lifespan=_lifespan)

    @app.get("/health")
    async def _health() -> dict[str, str]:
        return {"status": "ok"}

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
