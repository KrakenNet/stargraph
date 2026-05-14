# SPDX-License-Identifier: Apache-2.0
"""Mock ServiceNow Table API for cve_remediation demo.

Implements the subset of /api/now/table/<table> that the Nautilus
ServiceNowAdapter (and CVE-rem Phase 4 CR creation) exercises:

  - GET    /api/now/table/<table>         list with sysparm_query / sysparm_limit
  - POST   /api/now/table/<table>         create record, return sys_id
  - PATCH  /api/now/table/<table>/<sys_id> update record
  - GET    /api/now/table/<table>/<sys_id> read record

In-memory store; resets on restart. Purpose: exercise the wire shape
without needing a real ServiceNow instance.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

INSTANCE = os.environ.get("SN_INSTANCE_NAME", "cve-rem-mock")

app = FastAPI(title=f"Mock ServiceNow ({INSTANCE})", version="1.0")

# Per-table in-memory store: {table_name: {sys_id: record}}
_TABLES: dict[str, dict[str, dict[str, Any]]] = {}


def _table(name: str) -> dict[str, dict[str, Any]]:
    return _TABLES.setdefault(name, {})


def _new_sys_id() -> str:
    return uuid.uuid4().hex


def _now() -> str:
    return datetime.now(UTC).isoformat()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "instance": INSTANCE, "tables": str(len(_TABLES))}


@app.get("/api/now/table/{table}")
def list_records(
    table: str,
    sysparm_query: str | None = Query(default=None),
    sysparm_limit: int = Query(default=1000),
) -> dict[str, list[dict[str, Any]]]:
    rows = list(_table(table).values())
    if sysparm_query:
        # very loose substring filter — sufficient for adapter wire-test
        needle = sysparm_query.lower()
        rows = [r for r in rows if any(needle in str(v).lower() for v in r.values())]
    return {"result": rows[:sysparm_limit]}


@app.get("/api/now/table/{table}/{sys_id}")
def get_record(table: str, sys_id: str) -> dict[str, dict[str, Any]]:
    rec = _table(table).get(sys_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="record-not-found")
    return {"result": rec}


class _CreateBody(BaseModel):
    model_config = {"extra": "allow"}


@app.post("/api/now/table/{table}", status_code=201)
def create_record(table: str, body: _CreateBody) -> dict[str, dict[str, Any]]:
    sys_id = _new_sys_id()
    record = {
        **body.model_dump(),
        "sys_id": sys_id,
        "sys_created_on": _now(),
        "sys_updated_on": _now(),
        "number": f"CHG{len(_table(table)) + 1000:07d}",
        "state": body.model_dump().get("state", "draft"),
    }
    _table(table)[sys_id] = record
    return {"result": record}


@app.patch("/api/now/table/{table}/{sys_id}")
def update_record(table: str, sys_id: str, body: _CreateBody) -> dict[str, dict[str, Any]]:
    rec = _table(table).get(sys_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="record-not-found")
    rec.update(body.model_dump())
    rec["sys_updated_on"] = _now()
    return {"result": rec}
