# SPDX-License-Identifier: Apache-2.0
"""Lightweight serve script for the graph-viewer demo.

Loads one or more stargraph.yaml files, builds topology JSON from the IR,
and serves both the API and the static viewer UI.

Usage:

    uv run --no-project python -m demos.graph_viewer.serve_graph_viewer \
        --graph demos/sentinel_dark_watch/graph/stargraph.yaml

    # or multiple graphs:
    uv run --no-project python -m demos.graph_viewer.serve_graph_viewer \
        --graph demos/sentinel_dark_watch/graph/stargraph.yaml \
        --graph demos/everything-demo/graph/stargraph.yaml
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import uvicorn
import yaml as _yaml
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from stargraph.ir._models import IRDocument

_NODE_ID_RE = re.compile(r"\(node-id\s*\(id\s+([A-Za-z0-9_\-:.]+)\s*\)\s*\)")
_PHASE_RE = re.compile(r"^#\s*-{3,}\s*(.+?)\s*-{3,}\s*$")


def _detect_phases(raw_yaml: str) -> list[dict[str, Any]]:
    """Extract phase groups from YAML comment headers.

    Scans for lines like ``# ---------- Phase 1: intake ----------``
    within the ``nodes:`` section and maps each node ID to its phase.
    """
    phases: list[dict[str, Any]] = []
    in_nodes = False
    current_phase: dict[str, Any] | None = None

    for line in raw_yaml.splitlines():
        stripped = line.strip()
        if stripped == "nodes:":
            in_nodes = True
            continue
        if in_nodes and not stripped.startswith("#") and not stripped.startswith("-") and not stripped.startswith(" ") and stripped and not stripped.startswith("kind") and not stripped.startswith("spec") and not stripped.startswith("config"):
            if not stripped.startswith("id:"):
                in_nodes = False
                continue

        if not in_nodes:
            continue

        phase_match = _PHASE_RE.match(stripped)
        if phase_match:
            label = phase_match.group(1).strip()
            current_phase = {"label": label, "node_ids": []}
            phases.append(current_phase)
            continue

        id_match = re.match(r"-\s*id:\s*(\S+)", stripped)
        if id_match and current_phase is not None:
            current_phase["node_ids"].append(id_match.group(1))

    return phases


def _extract_docstrings(nodes: list[Any]) -> dict[str, str]:
    """Import node classes and extract their docstrings."""
    import importlib

    docstrings: dict[str, str] = {}
    for n in nodes:
        kind = n.kind
        if ":" not in kind:
            continue
        module_path, class_name = kind.rsplit(":", 1)
        try:
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name, None)
            if cls and cls.__doc__:
                doc_text = cls.__doc__.strip()
                first_para = doc_text.split("\n\n")[0].replace("\n", " ").strip()
                docstrings[n.id] = first_para
        except Exception:
            continue
    return docstrings


def _topology_for(doc: IRDocument, raw_yaml: str, graph_hash: str) -> dict[str, Any]:
    """Build topology JSON from an IRDocument + raw YAML (for phase comments)."""
    docstrings = _extract_docstrings(doc.nodes)
    nodes_json = [
        {
            "id": n.id,
            "kind": n.kind,
            "spec": n.spec,
            "config": json.loads(json.dumps(n.config, default=str)),
            "description": docstrings.get(n.id, ""),
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
                join_node = getattr(action, "join", "")
                if join_node and source:
                    for tgt in getattr(action, "targets", []) or []:
                        edges_json.append({
                            "source": tgt,
                            "target": join_node,
                            "via_rule": rule.id,
                            "kind": "parallel_join",
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

    for n in nodes_json:
        n["rules"] = rules_by_source.get(n["id"], [])

    phases = _detect_phases(raw_yaml)

    return {
        "graph_id": doc.id,
        "ir_version": doc.ir_version,
        "graph_hash": graph_hash,
        "state_class": doc.state_class,
        "nodes": nodes_json,
        "edges": edges_json,
        "tools": [{"id": t.id, "version": t.version} for t in doc.tools],
        "governance": [
            {
                "id": g.id,
                "version": g.version,
                "requires": g.requires.model_dump(mode="json") if g.requires else None,
            }
            for g in doc.governance
        ],
        "stores": [{"name": s.name, "provider": s.provider} for s in doc.stores],
        "skills": [{"id": s.id, "version": s.version} for s in doc.skills],
        "phases": phases,
        "parallel": [
            {"targets": p.targets, "join": p.join, "strategy": p.strategy}
            for p in doc.parallel
        ],
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="serve_graph_viewer",
        description="Stargraph graph viewer — interactive IR visualizer.",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument(
        "--graph",
        action="append",
        default=None,
        help="IR YAML graph to pre-load (repeatable).",
    )
    parser.add_argument(
        "--upstream",
        default=None,
        help=(
            "URL of an upstream stargraph serve (e.g. http://localhost:9000). "
            "When set, /api/runs* routes proxy to upstream /v1/runs* and the "
            "WS /api/runs/{id}/stream forwards from upstream /v1/runs/{id}/stream."
        ),
    )
    args = parser.parse_args(argv)

    from stargraph.graph.definition import Graph

    topologies: dict[str, dict[str, Any]] = {}

    for path_str in args.graph or []:
        path = Path(path_str)
        raw = path.read_text()
        ir_doc = IRDocument.model_validate(_yaml.safe_load(raw))
        graph_obj = Graph(ir=ir_doc)
        topo = _topology_for(ir_doc, raw, graph_obj.graph_hash)
        topologies[ir_doc.id] = topo
        print(
            f"[graph-viewer] loaded {ir_doc.id!r} "
            f"(nodes={len(ir_doc.nodes)}, rules={len(ir_doc.rules)}, hash={graph_obj.graph_hash[:12]})"
        )

    app = FastAPI(title="stargraph-graph-viewer")
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/api/graphs")
    async def _list_graphs() -> JSONResponse:
        summaries = [
            {
                "graph_id": gid,
                "node_count": len(t["nodes"]),
                "rule_count": len(t["edges"]),
                "tool_count": len(t["tools"]),
            }
            for gid, t in topologies.items()
        ]
        return JSONResponse({"graphs": summaries})

    @app.get("/api/graph")
    async def _get_graph(graph_id: str | None = None) -> JSONResponse:
        gid = graph_id or next(iter(topologies.keys()), None)
        if gid is None:
            raise HTTPException(status_code=404, detail="no graphs loaded")
        topo = topologies.get(gid)
        if topo is None:
            raise HTTPException(status_code=404, detail=f"graph {gid!r} not found")
        return JSONResponse(topo)

    @app.post("/api/upload")
    async def _upload_graph(file: UploadFile) -> JSONResponse:
        raw = (await file.read()).decode("utf-8")
        try:
            ir_doc = IRDocument.model_validate(_yaml.safe_load(raw))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid stargraph.yaml: {exc}") from exc
        graph_obj = Graph(ir=ir_doc)
        topo = _topology_for(ir_doc, raw, graph_obj.graph_hash)
        topologies[ir_doc.id] = topo
        return JSONResponse(topo)

    if args.upstream:
        import httpx
        import asyncio
        from fastapi import WebSocket, WebSocketDisconnect
        from starlette.websockets import WebSocketState
        import websockets

        upstream = args.upstream.rstrip("/")
        ws_upstream = upstream.replace("http://", "ws://").replace("https://", "wss://")
        print(f"[graph-viewer] proxying runs to upstream {upstream}")

        client = httpx.AsyncClient(base_url=upstream, timeout=10.0)

        @app.get("/api/runs")
        async def _proxy_list_runs(
            status: str | None = None,
            limit: int = 50,
            offset: int = 0,
        ) -> JSONResponse:
            params = {"limit": limit, "offset": offset}
            if status:
                params["status"] = status
            try:
                r = await client.get("/v1/runs", params=params)
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=502, detail=f"upstream error: {exc}") from exc
            return JSONResponse(r.json(), status_code=r.status_code)

        @app.get("/api/runs/{run_id}")
        async def _proxy_get_run(run_id: str) -> JSONResponse:
            try:
                r = await client.get(f"/v1/runs/{run_id}")
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=502, detail=f"upstream error: {exc}") from exc
            return JSONResponse(r.json(), status_code=r.status_code)

        @app.get("/api/runs/{run_id}/events")
        async def _proxy_run_events(run_id: str) -> JSONResponse:
            for path_attempt in (
                f"/watch/api/run/{run_id}/events",
                f"/v1/runs/{run_id}/events",
            ):
                try:
                    r = await client.get(path_attempt)
                    if r.status_code == 200:
                        return JSONResponse(r.json())
                except httpx.HTTPError:
                    continue
            raise HTTPException(status_code=404, detail=f"no events for {run_id}")

        @app.get("/api/runs/{run_id}/checkpoints")
        async def _proxy_run_checkpoints(run_id: str) -> JSONResponse:
            try:
                r = await client.get(f"/watch/api/run/{run_id}/checkpoints")
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=502, detail=f"upstream error: {exc}") from exc
            return JSONResponse(r.json(), status_code=r.status_code)

        @app.websocket("/api/runs/{run_id}/stream")
        async def _proxy_run_stream(websocket: WebSocket, run_id: str) -> None:
            await websocket.accept()
            ws_url = f"{ws_upstream}/v1/runs/{run_id}/stream"
            try:
                async with websockets.connect(ws_url, max_size=None) as upstream_ws:
                    async def downstream_to_upstream() -> None:
                        try:
                            while True:
                                msg = await websocket.receive_text()
                                await upstream_ws.send(msg)
                        except WebSocketDisconnect:
                            pass

                    async def upstream_to_downstream() -> None:
                        try:
                            async for msg in upstream_ws:
                                if websocket.client_state != WebSocketState.CONNECTED:
                                    break
                                if isinstance(msg, bytes):
                                    await websocket.send_bytes(msg)
                                else:
                                    await websocket.send_text(msg)
                        except websockets.ConnectionClosed:
                            pass

                    await asyncio.gather(
                        downstream_to_upstream(),
                        upstream_to_downstream(),
                        return_exceptions=True,
                    )
            except Exception as exc:  # noqa: BLE001
                try:
                    await websocket.send_json({"error": str(exc)})
                except Exception:
                    pass
            finally:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.close()

        # Optionally pull topology from upstream on first request.
        @app.get("/api/upstream-graph")
        async def _upstream_graph() -> JSONResponse:
            for path_attempt in ("/watch/api/graph", "/api/graph"):
                try:
                    r = await client.get(path_attempt)
                    if r.status_code == 200:
                        return JSONResponse(r.json())
                except httpx.HTTPError:
                    continue
            raise HTTPException(status_code=404, detail="upstream has no topology API")

    viewer_dir = Path(__file__).parent / "viewer"
    if viewer_dir.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=str(viewer_dir), html=True),
            name="graph-viewer",
        )

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
