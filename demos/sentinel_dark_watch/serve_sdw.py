# SPDX-License-Identifier: Apache-2.0
"""``stargraph serve`` wrapper that pins the SDW capability profile.

Thin argparse wrapper around :func:`stargraph.serve.api.create_app` that inserts the
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
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(name)s %(levelname)s %(message)s",
)

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
        description="stargraph serve with the SDW capability profile pinned.",
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
            "Defaults to both stargraph.yaml and retrain.yaml."
        ),
    )
    args = parser.parse_args(argv)

    import os
    import tempfile
    from collections.abc import AsyncGenerator  # noqa: TC003
    from contextlib import asynccontextmanager

    import uvicorn
    from fastapi import FastAPI  # noqa: TC002

    from stargraph.artifacts.fs import FilesystemArtifactStore
    from stargraph.checkpoint.sqlite import SQLiteCheckpointer
    from stargraph.errors import StargraphRuntimeError
    from stargraph.registry import StoreRegistry, ToolRegistry
    from stargraph.serve.api import create_app
    from stargraph.serve.history import RunHistory
    from stargraph.serve.lifecycle import broker_lifespan
    from stargraph.serve.profiles import select_profile
    from stargraph.serve.scheduler import Scheduler

    os.environ["STARGRAPH_PROFILE"] = args.profile
    selected = select_profile()

    # Wire dspy.LM from env.
    from stargraph.cli.run import _configure_lm

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
    os.environ.setdefault("STARGRAPH_CONFIG_DIR", str(Path(__file__).parent.resolve()))
    tmpdir = Path(tempfile.mkdtemp(prefix="sdw-serve-"))
    checkpointer = SQLiteCheckpointer(tmpdir / "checkpoint.sqlite")
    artifact_store = FilesystemArtifactStore(tmpdir / "artifacts")
    scheduler = Scheduler()

    # Load + register graphs.
    import yaml as _yaml

    from stargraph.cli.run import _build_node_registry
    from stargraph.graph.definition import Graph
    from stargraph.ir._models import IRDocument

    graph_dir = Path(__file__).parent / "graph"
    default_graphs = [
        str(graph_dir / "stargraph.yaml"),
        str(graph_dir / "retrain.yaml"),
        str(graph_dir / "evolve.yaml"),
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

    # Bootstrap Fathom engine with Bosun governance packs.
    fathom_adapter = None
    try:
        import fathom as _fathom

        from stargraph.fathom._adapter import FathomAdapter

        engine = _fathom.Engine(default_decision="deny")
        fathom_adapter = FathomAdapter(engine)
        fathom_adapter.register_stargraph_action_template()

        # Register stargraph_action in Fathom's template registry so query() works
        from fathom.models import SlotDefinition, TemplateDefinition

        stargraph_action_def = TemplateDefinition(
            name="stargraph_action",
            description="Stargraph routing action",
            slots=[
                SlotDefinition(name="kind", type="symbol"),
                SlotDefinition(name="target", type="string"),
                SlotDefinition(name="reason", type="string"),
                SlotDefinition(name="rule_id", type="string"),
                SlotDefinition(name="step", type="integer"),
                SlotDefinition(name="join", type="string"),
                SlotDefinition(name="fact", type="string"),
                SlotDefinition(name="slots", type="string"),
                SlotDefinition(name="pattern", type="string"),
            ],
        )
        engine.template_registry["stargraph_action"] = stargraph_action_def

        # Install CLIPS deftemplate stubs for audit pack (dots OK in CLIPS)
        _stargraph_stubs = [
            "(deftemplate stargraph.transition (slot _run_id) (slot _step) (slot kind))",
            "(deftemplate stargraph.tool_call (slot _run_id) (slot _step) (slot name))",
            "(deftemplate stargraph.node_run (slot _run_id) (slot _step) (slot node_id))",
            "(deftemplate stargraph.respond (slot _run_id) (slot _step) (slot caller))",
            "(deftemplate stargraph.cancel (slot _run_id) (slot _step) (slot reason))",
            "(deftemplate stargraph.pause (slot _run_id) (slot _step) (slot reason))",
            "(deftemplate stargraph.artifact_write (slot _run_id) (slot _step) (slot artifact_id))",
        ]
        for stub in _stargraph_stubs:
            engine._env.build(stub)

        # Load Bosun packs declared in any graph's governance section.
        bosun_root = Path(__file__).parent.parent.parent / "src" / "stargraph" / "bosun"
        sdw_bosun_root = Path(__file__).parent / "bosun"
        loaded_packs: set[str] = set()
        for graph_obj in graphs.values():
            for pack in graph_obj.ir.governance:
                if pack.id in loaded_packs:
                    continue
                # Resolve pack location: stargraph.bosun.X → src/stargraph/bosun/X
                # sdw.X → demos/sentinel_dark_watch/bosun/X
                parts = pack.id.split(".")
                if parts[0] == "stargraph" and parts[1] == "bosun" and len(parts) > 2:
                    pack_dir = bosun_root / parts[2]
                elif parts[0] == "sdw" and len(parts) > 1:
                    pack_dir = sdw_bosun_root / parts[1]
                else:
                    print(f"[serve_sdw] unknown pack namespace: {pack.id}")
                    continue

                rules_path = pack_dir / "rules.clp"
                if rules_path.exists():
                    src = rules_path.read_text(encoding="utf-8")
                    # Strip CLIPS comments and split into constructs
                    lines = []
                    for line in src.splitlines():
                        idx = line.find(";")
                        lines.append(line[:idx] if idx >= 0 else line)
                    clean = "\n".join(lines)
                    constructs, cur, depth = [], [], 0
                    for ch in clean:
                        if depth == 0 and ch.isspace():
                            continue
                        cur.append(ch)
                        if ch == "(":
                            depth += 1
                        elif ch == ")":
                            depth -= 1
                            if depth == 0:
                                constructs.append("".join(cur))
                                cur = []
                    for construct in constructs:
                        engine._env.build(construct)
                    loaded_packs.add(pack.id)
                    print(f"[serve_sdw] loaded Bosun pack {pack.id!r} from {rules_path}")
                else:
                    print(f"[serve_sdw] Bosun pack {pack.id!r} not found at {rules_path}")

        if loaded_packs:
            print(f"[serve_sdw] Fathom engine ready with {len(loaded_packs)} governance packs")
        else:
            fathom_adapter = None
            print("[serve_sdw] no Bosun packs loaded — Fathom disabled")
    except ImportError:
        print("[serve_sdw] fathom not installed — governance disabled")

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
        "fathom": fathom_adapter,
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
            raise StargraphRuntimeError("checkpointer bootstrap failed")
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

    @app.post("/sdw/scan")
    async def _scan() -> dict[str, Any]:
        """Trigger a pipeline run over all sar_tiles in PostGIS."""
        import asyncpg

        from demos.sentinel_dark_watch.db import get_pg_dsn

        conn = await asyncpg.connect(get_pg_dsn())
        try:
            rows = await conn.fetch("SELECT tile_id FROM sar_tiles ORDER BY tile_id")
        finally:
            await conn.close()
        tile_ids = [r["tile_id"] for r in rows]
        if not tile_ids:
            return {"error": "no sar_tiles in database — run bootstrap first"}

        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"http://localhost:{args.port}/v1/runs",
                json={
                    "graph_id": "graph:sdw-pipeline",
                    "state": {"tile_queue": tile_ids, "run_id": f"scan-{int(__import__('time').time())}"},
                },
            )
            return resp.json()

    @app.post("/sdw/evolve")
    async def _evolve() -> dict[str, Any]:
        """Trigger one evolution (self-improvement) cycle."""
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"http://localhost:{args.port}/v1/runs",
                json={
                    "graph_id": "graph:sdw-evolve",
                    "state": {"run_id": f"evolve-{int(__import__('time').time())}"},
                },
            )
            return resp.json()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
