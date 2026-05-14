# SPDX-License-Identifier: Apache-2.0
"""End-to-end live driver for the cve_remediation demo.

Boots the Harbor serve FastAPI app in-process (no uvicorn / network), pre-
registers every demo IR into ``deps["graphs"]``, then drives the
``graph:cve-rem-pipeline`` graph with a seeded CVE state, auto-responding
to every HITL gate as ``approve`` and reporting the terminal run summary.

Usage::

    uv run --no-project python -m demos.cve_remediation.live_test
    uv run --no-project python -m demos.cve_remediation.live_test --graph graph:cve-rem-doctrine-ingest
    uv run --no-project python -m demos.cve_remediation.live_test --json

The driver does NOT spawn uvicorn -- it uses ``httpx.AsyncClient`` over
``ASGITransport`` to dispatch requests directly into the app instance.
This keeps the harness deterministic and avoids port-binding races.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import warnings
from contextlib import AsyncExitStack

# Pydantic v2 emits ``UserWarning: Pydantic serializer warnings:`` when
# StrEnum fields round-trip through ``model_dump(mode='json')`` -> dict
# -> ``state_class(**...)`` because the resulting input value is a plain
# ``str`` rather than the enum. The string IS a valid enum member, so the
# round-trip is correct; the warning is cosmetic. Filtering keeps CI logs
# free of false-positive noise. Scoped to ``pydantic.main`` so unrelated
# Pydantic serialization issues remain visible.
warnings.filterwarnings(
    "ignore",
    message="Pydantic serializer warnings:.*",
    category=UserWarning,
    module=r"pydantic\.main",
)
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import FastAPI

from harbor.checkpoint.sqlite import SQLiteCheckpointer
from harbor.cli.run import _build_node_registry
from harbor.graph import Graph
from harbor.ir import IRDocument
from harbor.serve.api import create_app
from harbor.serve.history import RunHistory
from harbor.serve.profiles import select_profile
from harbor.serve.scheduler import Scheduler
from harbor.artifacts.fs import FilesystemArtifactStore

DEMO_ROOT = Path(__file__).resolve().parent
GRAPH_DIR = DEMO_ROOT / "graph"

DEFAULT_SEED: dict[str, Any] = {
    # Only ``cve_id`` is seeded; the IntakeFetchNode at the top of the
    # graph fetches the canonical advisory body from NVD by id (or short-
    # circuits when the caller pre-supplies ``raw_source_body``, e.g. a
    # cassette / webhook ingestion path).
    "cve_id": "CVE-2021-44228",
}


def _load_all_irs() -> dict[str, IRDocument]:
    """Walk demo graph dir; return ``{graph_id -> IRDocument}``."""
    out: dict[str, IRDocument] = {}
    for p in sorted(GRAPH_DIR.rglob("*.yaml")):
        if p.parent.name in ("tests",):
            continue
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(doc, dict) or "ir_version" not in doc:
            continue
        ir = IRDocument.model_validate(doc)
        out[doc["id"]] = ir
    return out


async def _wait_for_status(
    client: httpx.AsyncClient,
    run_id: str,
    *,
    terminal: set[str],
    interval_s: float = 0.05,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Poll ``GET /v1/runs/{run_id}`` until status hits ``terminal``."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    last: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        r = await client.get(f"/v1/runs/{run_id}")
        if r.status_code == 200:
            last = r.json()
            if last.get("status") in terminal:
                return last
        await asyncio.sleep(interval_s)
    raise TimeoutError(f"run {run_id} did not reach {terminal!r}; last={last!r}")


async def _approve_hitl_loop(
    client: httpx.AsyncClient,
    run_id: str,
    *,
    max_approvals: int = 8,
    poll_interval_s: float = 0.05,
    overall_timeout_s: float = 60.0,
) -> dict[str, Any]:
    """Drive the run to a terminal state, auto-approving every HITL gate."""
    approvals = 0
    deadline = asyncio.get_event_loop().time() + overall_timeout_s
    last: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        r = await client.get(f"/v1/runs/{run_id}")
        if r.status_code != 200:
            await asyncio.sleep(poll_interval_s)
            continue
        last = r.json()
        status = last.get("status")
        if status in {"done", "failed", "cancelled", "error"}:
            return last
        if status == "awaiting-input":
            if approvals >= max_approvals:
                raise RuntimeError(
                    f"exceeded {max_approvals} HITL approvals; last summary={last!r}"
                )
            body = {
                "response": {
                    "decision": "approve",
                    "actor": "live-harness",
                    "note": f"auto-approve #{approvals + 1}",
                    "at": datetime.now(UTC).isoformat(),
                }
            }
            resp = await client.post(f"/v1/runs/{run_id}/respond", json=body)
            if resp.status_code not in (200, 202):
                raise RuntimeError(
                    f"respond failed: {resp.status_code} {resp.text}"
                )
            approvals += 1
        await asyncio.sleep(poll_interval_s)
    raise TimeoutError(f"run {run_id} did not terminate; last summary={last!r}")


async def _drive_one(
    graph_id: str,
    seed: dict[str, Any],
    *,
    json_out: bool,
) -> dict[str, Any]:
    """Boot in-process app, register graphs, start run, drive to terminal."""
    profile = select_profile()

    irs = _load_all_irs()
    if graph_id not in irs:
        raise SystemExit(
            f"graph {graph_id!r} not found in {GRAPH_DIR}; "
            f"known: {sorted(irs.keys())}"
        )

    tmpdir = Path(tempfile.mkdtemp(prefix="cve-rem-live-"))
    db_path = tmpdir / "checkpoint.sqlite"
    artifact_root = tmpdir / "artifacts"
    artifact_store = FilesystemArtifactStore(artifact_root)

    checkpointer = SQLiteCheckpointer(db_path)
    scheduler = Scheduler()

    # Build a Graph + node registry per IR; register on deps["graphs"].
    graphs: dict[str, Graph] = {}
    node_registries: dict[str, dict] = {}
    for gid, ir in irs.items():
        g = Graph(ir)
        graphs[gid] = g
        # Resolve relative subgraph specs against each IR's own dir.
        # Locate each IR by id-to-path.
        ir_path = next(
            (
                p
                for p in GRAPH_DIR.rglob("*.yaml")
                if yaml.safe_load(p.read_text(encoding="utf-8") or "{}").get("id")
                == gid
            ),
            GRAPH_DIR,
        )
        node_registries[gid] = _build_node_registry(
            ir.nodes, ir_dir=ir_path.parent.resolve()
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
        "registry": {},
    }

    async with AsyncExitStack() as stack:
        await checkpointer.bootstrap()
        stack.push_async_callback(checkpointer.close)
        db = checkpointer._db  # pyright: ignore[reportPrivateUsage]
        run_history = RunHistory(db, jsonl_audit_path=None)
        await run_history.bootstrap()
        deps["run_history"] = run_history
        await artifact_store.bootstrap()

        # Compose the Nautilus broker lifespan BEFORE starting the
        # scheduler so worker tasks inherit the broker contextvar.
        # ``ContextVar.set()`` only propagates to tasks created AFTER
        # the set call; the scheduler spawns long-lived workers in
        # ``start()``, so it must run inside an active broker_lifespan
        # for cve_rem nodes' ``current_broker()`` lookup to resolve.
        # Soft-fails (no nautilus.yaml in HARBOR_CONFIG_DIR) leave the
        # contextvar unset and demo nodes fall back to offline envelopes.
        from harbor.serve.lifecycle import broker_lifespan

        await stack.enter_async_context(broker_lifespan())

        scheduler.set_deps(deps)
        await scheduler.start()
        stack.push_async_callback(scheduler.stop)

        app: FastAPI = create_app(profile, deps=deps)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://live.test",
            timeout=15.0,
        ) as client:
            # Probe a real route to confirm the app initialized.
            probe = await client.get("/v1/graphs")
            if probe.status_code != 200:
                raise SystemExit(
                    f"/v1/graphs probe returned {probe.status_code}: "
                    f"{probe.text}"
                )

            start_resp = await client.post(
                "/v1/runs",
                json={"graph_id": graph_id, "params": seed},
            )
            if start_resp.status_code != 202:
                raise SystemExit(
                    f"POST /v1/runs failed: {start_resp.status_code} "
                    f"{start_resp.text}"
                )
            start_body = start_resp.json()
            run_id = start_body["run_id"]

            try:
                summary = await _approve_hitl_loop(client, run_id)
            except TimeoutError as exc:
                summary = {"status": "timeout", "error": str(exc)}

    out = {
        "graph_id": graph_id,
        "run_id": run_id,
        "summary": summary,
        "artifact_root": str(artifact_root),
    }
    if json_out:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(f"graph_id={graph_id}")
        print(f"run_id={run_id}")
        print(f"status={summary.get('status')}")
        print(f"halt_reason={summary.get('halt_reason', '')}")
        print(f"artifact_root={artifact_root}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--graph",
        default="graph:cve-rem-pipeline",
        help="graph id to drive (default: main pipeline)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON output")
    parser.add_argument(
        "--cve-id",
        default=None,
        help="override seed cve_id (default: %s)" % DEFAULT_SEED["cve_id"],
    )
    args = parser.parse_args(argv)

    seed = dict(DEFAULT_SEED)
    if args.cve_id:
        seed["cve_id"] = args.cve_id
    # Honor env-driven artifact root if caller set one (test isolation).
    os.environ.setdefault("HARBOR_ARTIFACTS_ROOT", str(Path(".harbor") / "artifacts"))

    try:
        result = asyncio.run(_drive_one(args.graph, seed, json_out=args.json))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"live_test failed: {exc}", file=sys.stderr)
        return 2
    status = result.get("summary", {}).get("status")
    return 0 if status == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
