# SPDX-License-Identifier: Apache-2.0
"""``harbor serve`` wrapper that pins the cve-rem capability profile.

Mirrors the behavior of ``python -m harbor.cli.serve serve`` but
inserts the engine-side :class:`Capabilities` produced by
:func:`demos.cve_remediation.capabilities.build_cve_rem_capabilities`
into ``deps`` before constructing the FastAPI app. Production
deployments of the CVE remediation pipeline MUST use this entry
point (or an equivalent lifespan) -- otherwise ``run.capabilities``
is ``None`` and the engine-side gate is a silent no-op (CRITERIA
fancy #13 hardening).

Usage:

    set -a; source demos/cve_remediation/.env; set +a
    uv run --no-project python -m demos.cve_remediation.serve_cve_rem \
        --host 0.0.0.0 --port 9000

This script is intentionally thin: any divergence from
``harbor.cli.serve.main`` would be a maintenance hazard.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from demos.cve_remediation.capabilities import build_cve_rem_capabilities


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="serve_cve_rem",
        description=(
            "harbor serve with the cve-rem capability profile pinned."
        ),
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9000)
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
            "Defaults to demos/cve_remediation/graph/harbor.yaml so the "
            "watcher launcher can POST /v1/runs against the real "
            "graph:cve-rem-pipeline."
        ),
    )
    args = parser.parse_args(argv)

    # Lazy-import the harbor serve plumbing so this script works even
    # if the upstream CLI surface evolves.
    import asyncio
    import json
    import re
    import tempfile
    from contextlib import asynccontextmanager
    from collections.abc import AsyncGenerator
    from pathlib import Path

    import uvicorn
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse

    from harbor.artifacts.fs import FilesystemArtifactStore
    from harbor.checkpoint.sqlite import SQLiteCheckpointer
    from harbor.errors import HarborRuntimeError
    from harbor.serve.api import create_app
    from harbor.serve.history import RunHistory
    from harbor.serve.lifecycle import broker_lifespan
    from harbor.serve.profiles import select_profile
    from harbor.serve.scheduler import Scheduler
    from harbor.registry import StoreRegistry, ToolRegistry

    import os

    os.environ["HARBOR_PROFILE"] = args.profile
    selected = select_profile()

    # Wire dspy.LM from env so the live pipeline can call extractors /
    # planner / remediation_discovery against the local LM. Pairing is
    # mandatory (both URL + MODEL or neither) -- match harbor.cli.serve.
    from harbor.cli.run import _configure_lm
    lm_url = os.environ.get("LLM_BASE_URL") or None
    lm_model = os.environ.get("LLM_MODEL") or None
    lm_key = os.environ.get("LLM_API_KEY", "no-key")
    lm_timeout = int(os.environ.get("LLM_TIMEOUT_SECONDS", "180"))
    if lm_url and lm_model:
        _configure_lm(lm_url, lm_model, lm_key, lm_timeout)
        print(f"[serve_cve_rem] dspy.LM → {lm_model} @ {lm_url}")
    else:
        print("[serve_cve_rem] no LLM_BASE_URL/LLM_MODEL set — DSPy nodes will fail-loud")

    # Nautilus broker_lifespan looks for ``<HARBOR_CONFIG_DIR>/nautilus.yaml``.
    # Point it at this demo's config dir if the user didn't override.
    os.environ.setdefault("HARBOR_CONFIG_DIR", str(Path(__file__).parent.resolve()))
    tmpdir = Path(tempfile.mkdtemp(prefix="cve-rem-serve-"))
    checkpointer = SQLiteCheckpointer(tmpdir / "checkpoint.sqlite")
    artifact_store = FilesystemArtifactStore(tmpdir / "artifacts")
    scheduler = Scheduler()

    # Load + register graphs so POST /v1/runs can find them. Mirrors
    # harbor.cli.serve.serve's --graph loader so serve_cve_rem.py is a
    # drop-in superset of the upstream CLI for the cve-rem pipeline.
    from harbor.cli.run import _build_node_registry
    from harbor.graph.definition import Graph
    from harbor.ir._models import IRDocument
    import yaml as _yaml

    default_graph = Path(__file__).parent / "graph" / "harbor.yaml"
    graph_paths = [Path(p) for p in (args.graph or [str(default_graph)])]
    graphs: dict[str, Any] = {}
    node_registries: dict[str, dict[str, Any]] = {}
    ir_docs: dict[str, IRDocument] = {}
    for path in graph_paths:
        ir_doc = IRDocument.model_validate(_yaml.safe_load(path.read_text()))
        graph_obj = Graph(ir=ir_doc)
        graphs[ir_doc.id] = graph_obj
        ir_docs[ir_doc.id] = ir_doc
        node_registries[ir_doc.id] = _build_node_registry(
            ir_doc.nodes, ir_dir=path.parent.resolve(),
        )
        print(
            f"[serve_cve_rem] loaded graph {ir_doc.id!r} "
            f"(nodes={len(ir_doc.nodes)}, hash={graph_obj.graph_hash[:12]}, path={path})"
        )

    # ---- Topology JSON builder (used by /watch/api/graph) ----------------
    _NODE_ID_RE = re.compile(r"\(node-id\s*\(id\s+([A-Za-z0-9_\-:.]+)\s*\)\s*\)")

    def _topology_for(graph_id: str) -> dict[str, Any] | None:
        doc = ir_docs.get(graph_id)
        if doc is None:
            return None
        # Coerce node.config into JSON-safe (dump enums, paths, etc.) by
        # round-tripping through the same canonical pydantic-json mode the
        # event audit log uses.
        nodes_json = [
            {
                "id": n.id,
                "kind": n.kind,
                "spec": n.spec,
                "config": json.loads(json.dumps(n.config, default=str)),
            }
            for n in doc.nodes
        ]
        edges_json: list[dict[str, Any]] = []
        rules_by_source: dict[str, list[dict[str, Any]]] = {}
        for rule in doc.rules:
            src_match = _NODE_ID_RE.search(rule.when or "")
            source = rule.node_id or (src_match.group(1) if src_match else None)
            if source:
                rules_by_source.setdefault(source, []).append({
                    "id": rule.id,
                    "when": rule.when or "",
                    "actions": [a.model_dump(mode="json") for a in (rule.then or [])],
                })
            for action in rule.then or []:
                kind = getattr(action, "kind", None)
                if kind in ("goto", "retry"):
                    target = getattr(action, "target", None)
                    if source and target:
                        edges_json.append({
                            "source": source,
                            "target": target,
                            "via_rule": rule.id,
                            "kind": kind,
                            "when": rule.when or "",
                        })
                elif kind == "parallel":
                    for tgt in getattr(action, "targets", []) or []:
                        if source:
                            edges_json.append({
                                "source": source,
                                "target": tgt,
                                "via_rule": rule.id,
                                "kind": "parallel",
                                "when": rule.when or "",
                            })
                elif kind == "halt":
                    if source:
                        edges_json.append({
                            "source": source,
                            "target": "__halt__",
                            "via_rule": rule.id,
                            "kind": "halt",
                            "when": rule.when or "",
                            "reason": getattr(action, "reason", "") or "",
                        })
                elif kind == "interrupt":
                    if source:
                        edges_json.append({
                            "source": source,
                            "target": "__interrupt__",
                            "via_rule": rule.id,
                            "kind": "interrupt",
                            "when": rule.when or "",
                            "prompt": getattr(action, "prompt", "") or "",
                        })
        # Inject the rules-by-source map onto each node so the UI can render
        # "branch routing" panels with the rule when-conditions and targets.
        for n in nodes_json:
            n["rules"] = rules_by_source.get(n["id"], [])
        return {
            "graph_id": graph_id,
            "graph_hash": graphs[graph_id].graph_hash,
            "nodes": nodes_json,
            "edges": edges_json,
        }

    # ---- Per-run JSONL audit (so finished runs replay in /watch UI) ------
    audit_dir = tmpdir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    audit_tap_tasks: dict[str, asyncio.Task[None]] = {}
    audit_tap_seen: set[str] = set()

    async def _tap_broadcaster(run_id: str, broadcaster: Any) -> None:
        path = audit_dir / f"{run_id}.jsonl"
        try:
            with path.open("a", buffering=1, encoding="utf-8") as fh:
                async for event in broadcaster.subscribe():
                    try:
                        record = event.model_dump(mode="json")
                    except AttributeError:
                        record = {"raw": str(event)}
                    fh.write(json.dumps(record, default=str))
                    fh.write("\n")
        except Exception as exc:  # noqa: BLE001
            print(f"[serve_cve_rem] audit tap for {run_id} ended: {exc!r}")

    async def _audit_supervisor() -> None:
        # Polls deps["broadcasters"] for newly-registered runs and spawns
        # one tap task per run. The broadcaster keeps fanning out events
        # for the lifetime of the run; one extra subscriber per run is
        # the cheapest way to persist a per-run JSONL audit log without
        # patching the scheduler.
        while True:
            try:
                bcs = deps.get("broadcasters") or {}
                for run_id, broadcaster in list(bcs.items()):
                    if run_id in audit_tap_seen:
                        continue
                    audit_tap_seen.add(run_id)
                    audit_tap_tasks[run_id] = asyncio.create_task(
                        _tap_broadcaster(run_id, broadcaster),
                        name=f"serve_cve_rem.audit_tap.{run_id}",
                    )
            except Exception as exc:  # noqa: BLE001
                print(f"[serve_cve_rem] audit supervisor blip: {exc!r}")
            await asyncio.sleep(0.25)

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
        # CRITERIA fancy #13 hardening: engine-side capability profile.
        # Default-deny + scoped grants for every @tool referenced by
        # the cve-rem graph; tightens the engine boundary so a graph
        # that imports an undeclared tool fails loud at dispatch.
        "capabilities": build_cve_rem_capabilities(),
    }

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
        supervisor_task = asyncio.create_task(
            _audit_supervisor(), name="serve_cve_rem.audit_supervisor",
        )
        try:
            async with broker_lifespan():
                yield
        finally:
            supervisor_task.cancel()
            for task in audit_tap_tasks.values():
                task.cancel()
            await scheduler.stop()
            await checkpointer.close()

    app = create_app(selected, deps=deps, lifespan=_lifespan)

    # ---- Extra routes for the run-watcher UI -----------------------------
    # NOTE: declared BEFORE the StaticFiles mount at /watch so the
    # /watch/api/* paths route to FastAPI rather than 404 from
    # StaticFiles. The mount path is /watch, so anything under
    # /watch/api/* would otherwise fall through to the static dir.

    @app.get("/watch/api/graph")
    async def _watch_graph(  # pyright: ignore[reportUnusedFunction]
        graph_id: str | None = None,
    ) -> JSONResponse:
        gid = graph_id or next(iter(graphs.keys()), None)
        if gid is None:
            raise HTTPException(status_code=404, detail="no graphs loaded")
        topo = _topology_for(gid)
        if topo is None:
            raise HTTPException(status_code=404, detail=f"graph {gid!r} not found")
        return JSONResponse(topo)

    @app.get("/watch/api/run/{run_id}/events")
    async def _watch_run_events(run_id: str) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        path = audit_dir / f"{run_id}.jsonl"
        events: list[dict[str, Any]] = []
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except (ValueError, TypeError):
                        continue
        return JSONResponse({"run_id": run_id, "events": events})

    @app.get("/watch/api/run/{run_id}/checkpoints")
    async def _watch_run_checkpoints(run_id: str) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        """List per-step checkpoints for ``run_id`` as ``(step, last_node, state, ts)``.

        Reads the SQLiteCheckpointer directly so the UI can compute per-node
        state deltas (diff state[step] against state[step-1] to attribute new
        fields to the node that just ran). No hardcoding -- this is the canonical
        durability record the engine wrote at each step boundary.
        """
        db = checkpointer._db  # pyright: ignore[reportPrivateUsage]
        if db is None:
            raise HTTPException(status_code=503, detail="checkpointer not bootstrapped")
        rows: list[dict[str, Any]] = []
        try:
            async with db.execute(
                "SELECT step_idx, ts, state_snapshot, last_node, next_action "
                "FROM checkpoints WHERE run_id = ? "
                "ORDER BY step_idx ASC",
                (run_id,),
            ) as cur:
                async for row in cur:
                    step_idx, ts, state_snapshot, last_node, next_action = row
                    try:
                        state_obj = json.loads(state_snapshot) if state_snapshot else {}
                    except (ValueError, TypeError):
                        state_obj = {}
                    try:
                        next_action_obj = json.loads(next_action) if next_action else None
                    except (ValueError, TypeError):
                        next_action_obj = None
                    rows.append({
                        "step": int(step_idx),
                        "ts": ts,
                        "last_node": last_node,
                        "state": state_obj,
                        "next_action": next_action_obj,
                    })
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"checkpoint read failed: {exc!r}") from exc
        return JSONResponse({"run_id": run_id, "checkpoints": rows})

    # Mount the WorkGraph run-watcher UI at /watch (static React+Babel page
    # under demos/cve_remediation/watcher/). Visiting /watch with no query
    # plays the simulated cve-rem run baked into graph-data.jsx; /watch/?run=
    # <run_id> binds to the live /v1/runs/{run_id}/stream WebSocket.
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    watcher_dir = Path(__file__).parent / "watcher"
    if watcher_dir.is_dir():
        app.mount(
            "/watch",
            StaticFiles(directory=str(watcher_dir), html=True),
            name="cve-rem-watcher",
        )

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
