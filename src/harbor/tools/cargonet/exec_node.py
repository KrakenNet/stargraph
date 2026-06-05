# SPDX-License-Identifier: Apache-2.0
"""CargoNet REST helpers: list nodes, find by name, run command remotely."""

from __future__ import annotations

import os
from typing import Any

import httpx

from harbor.errors import HarborRuntimeError
from harbor.tools.decorator import tool
from harbor.tools.spec import SideEffects

_NAMESPACE = "cargonet"
_VERSION = "0.1.0"
_DEFAULT_TIMEOUT = 30.0


def _resolve_base_url() -> str:
    base = os.environ.get("CARGONET_BASE_URL", "http://localhost:28080").strip().rstrip("/")
    if not base:
        raise HarborRuntimeError("CARGONET_BASE_URL is unset; cannot reach CargoNet")
    return base


def _auth_headers() -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/json"}
    bearer = os.environ.get("CARGONET_BEARER_TOKEN", "").strip()
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    return headers


@tool(
    name="cargonet_list_nodes",
    namespace=_NAMESPACE,
    version=_VERSION,
    side_effects=SideEffects.read,
    requires_capability="tools:cargonet:read",
    description=(
        "List CargoNet lab nodes. With lab_id unset, returns nodes across every running lab."
    ),
)
async def cargonet_list_nodes(
    *,
    lab_id: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,  # noqa: ASYNC109 -- explicit timeout forwarded to subprocess wait
) -> list[dict[str, Any]]:
    base = _resolve_base_url()
    headers = _auth_headers()
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        if lab_id:
            lab_ids = [lab_id]
        else:
            r = await client.get(f"{base}/api/v1/labs", headers=headers)
            r.raise_for_status()
            labs_body: dict[str, Any] = r.json() or {}
            items: list[dict[str, Any]] = labs_body.get("items", [])
            lab_ids = [
                str(item.get("id"))
                for item in items
                if str(item.get("status") or "").lower() == "running" and item.get("id")
            ]
        for lid in lab_ids:
            r = await client.get(f"{base}/api/v1/labs/{lid}/nodes", headers=headers)
            r.raise_for_status()
            nodes_body: dict[str, Any] = r.json() or {}
            nodes: list[dict[str, Any]] = nodes_body.get("items", [])
            for node in nodes:
                rows.append(
                    {
                        "lab_id": lid,
                        "id": str(node.get("id") or ""),
                        "name": str(node.get("name") or ""),
                        "kind": str(node.get("kind") or ""),
                        "health": str(node.get("health") or ""),
                    }
                )
    return rows


@tool(
    name="cargonet_find_node",
    namespace=_NAMESPACE,
    version=_VERSION,
    side_effects=SideEffects.read,
    requires_capability="tools:cargonet:read",
    description="Locate a CargoNet node by exact name.",
)
async def cargonet_find_node(
    *,
    name: str,
    timeout: float = _DEFAULT_TIMEOUT,  # noqa: ASYNC109 -- explicit timeout forwarded to subprocess wait
) -> dict[str, Any]:
    rows = await cargonet_list_nodes(timeout=timeout)
    for row in rows:
        if row.get("name") == name:
            return {
                "lab_id": row["lab_id"],
                "node_id": row["id"],
                "kind": row["kind"],
                "health": row["health"],
            }
    return {}


@tool(
    name="cargonet_exec",
    namespace=_NAMESPACE,
    version=_VERSION,
    side_effects=SideEffects.external,
    requires_capability="tools:cargonet:exec",
    description=(
        "Run a shell command inside a CargoNet node container via the "
        "REST surface. Returns {exit_code, output, stderr}."
    ),
)
async def cargonet_exec(
    *,
    lab_id: str,
    node_id: str,
    command: str,
    timeout: float = _DEFAULT_TIMEOUT,  # noqa: ASYNC109 -- explicit timeout forwarded to subprocess wait
) -> dict[str, Any]:
    if not lab_id or not node_id:
        raise HarborRuntimeError(
            "cargonet_exec requires both lab_id and node_id",
            lab_id=lab_id,
            node_id=node_id,
        )
    if not command:
        raise HarborRuntimeError("cargonet_exec command must be non-empty")
    base = _resolve_base_url()
    headers = dict(_auth_headers())
    headers["Content-Type"] = "application/json"
    url = f"{base}/api/v1/labs/{lab_id}/nodes/{node_id}/exec"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json={"command": command}, headers=headers)
    if resp.status_code >= 300:
        raise HarborRuntimeError(
            f"cargonet_exec failed: HTTP {resp.status_code}",
            status=resp.status_code,
            body=resp.text[:500],
            url=url,
        )
    body: dict[str, Any] = resp.json() or {}
    return {
        "exit_code": int(body.get("exit_code", -1)),
        "output": str(body.get("output", "") or ""),
        "stderr": str(body.get("stderr", "") or ""),
    }
