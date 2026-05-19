# SPDX-License-Identifier: Apache-2.0
"""Test helper: auto-approve every stuck CR in PDI.

Walks every change_request where ``short_description`` matches
``CVE-`` and the lifecycle state is non-terminal (draft / assess /
review / awaiting_hitl). For each, PATCHes the record through the
ServiceNow state machine to either ``closed-complete`` (success path)
or ``closed-rejected`` (the rejected cohort), so the demo environment
doesn't accumulate stuck records across runs.

This is a TEST helper. It is NOT the same as the in-pipeline HITL
decision — the pipeline's HITL gate writes a real decision via
``hitl_approval_node``. This script only cleans up CRs left in
intermediate states because the demo ran in non-interactive mode and
no human ever clicked through.

Usage::

    set -a; source demos/cve_remediation/.env; set +a
    uv run --no-project python -m demos.cve_remediation.scripts.hitl_auto_approve \\
        --max 200 --dry-run   # preview
    uv run --no-project python -m demos.cve_remediation.scripts.hitl_auto_approve \\
        --max 200             # actually advance

State codes (ITIL change_request):
   -5  new
   -4  assess
   -3  authorize
   -2  scheduled  (Nautilus broker default when creating)
   -1  implement
    0  review
    3  closed
    4  cancelled
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import httpx

_SN_BASE = os.environ.get("SERVICENOW_BASE_URL", "").rstrip("/")
_SN_USER = os.environ.get("SERVICENOW_USERNAME", "")
_SN_PASS = os.environ.get("SERVICENOW_PASSWORD", "")

# State codes for ITIL change_request. We close everything non-terminal
# to "closed-complete" except records whose work_notes contain a known
# reject marker — those go to "closed-cancelled".
_TERMINAL_CODES = {"3", "4", "closed", "cancelled"}
_REJECT_MARKER = "rejected_by_pipeline"


async def _list_open_crs(client, *, max_records: int, number_min: str, number_max: str) -> list[dict]:
    """Page through change_request looking for CVE-tagged non-terminal.

    When ``number_min``/``number_max`` are provided, scopes to that
    ``CHG\\d{7}`` range so a run cleans up only its own batch.
    """
    out: list[dict] = []
    offset = 0
    batch = 100
    while len(out) < max_records:
        q = "short_descriptionLIKECVE-^stateNOT IN3,4"
        if number_min:
            q += f"^number>={number_min}"
        if number_max:
            q += f"^number<={number_max}"
        params = {
            "sysparm_query": q,
            "sysparm_fields": "sys_id,number,state,short_description,work_notes",
            "sysparm_limit": str(batch),
            "sysparm_offset": str(offset),
        }
        r = await client.get("/api/now/table/change_request", params=params, timeout=20)
        r.raise_for_status()
        rows = r.json().get("result", [])
        if not rows:
            break
        out.extend(rows)
        offset += batch
        if len(rows) < batch:
            break
    return out[:max_records]


async def _patch_state(client, sys_id: str, body: dict) -> tuple[bool, str]:
    r = await client.patch(
        f"/api/now/table/change_request/{sys_id}",
        json=body, timeout=20,
    )
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    return True, ""


async def _close_cr(client, sys_id: str, *, current_state: str, reject: bool, dry_run: bool) -> tuple[bool, str]:
    """Walk a CR through ITIL state machine to closed.

    PDI's Change Model business rule enforces sequential transitions.
    From any non-terminal state we step: -> implement (-1) -> review (0)
    -> closed (3). For rejected, we cancel via state=4 from current.
    """
    if reject:
        body = {
            "state": "4",
            "close_code": "unsuccessful",
            "close_notes": "auto-cancelled by hitl_auto_approve.py (test helper)",
            "work_notes": "Test helper auto-cancel; pipeline marked rejected.",
        }
        if dry_run:
            return True, "DRY: would cancel (state=4)"
        return await _patch_state(client, sys_id, body)

    # Sequential walk -2 -> -1 -> 0 -> 3
    steps = [
        ("-1", {"state": "-1", "work_notes": "auto: implement"}),
        ("0",  {"state": "0",  "work_notes": "auto: review"}),
        ("3",  {
            "state": "3",
            "close_code": "successful",
            "close_notes": "auto-closed by hitl_auto_approve.py (test helper; no human reviewed)",
            "work_notes": "auto: closed-complete",
        }),
    ]
    if dry_run:
        return True, "DRY: would walk -2 -> -1 -> 0 -> 3"
    last_state = current_state
    for tgt, body in steps:
        ok, err = await _patch_state(client, sys_id, body)
        if not ok:
            return False, f"at {last_state}->{tgt}: {err}"
        last_state = tgt
    return True, "walked -> 3"


async def _amain(args):
    if not _SN_BASE or not _SN_USER:
        print("! SERVICENOW_BASE_URL or SERVICENOW_USERNAME unset", file=sys.stderr)
        return 2

    print(f"=== hitl_auto_approve ===")
    print(f"  PDI    : {_SN_BASE}")
    print(f"  user   : {_SN_USER}")
    print(f"  max    : {args.max}")
    print(f"  dry-run: {args.dry_run}")
    print()

    auth = (_SN_USER, _SN_PASS)
    async with httpx.AsyncClient(base_url=_SN_BASE, auth=auth) as client:
        rows = await _list_open_crs(
            client,
            max_records=args.max,
            number_min=args.number_min,
            number_max=args.number_max,
        )
        print(f"  open CVE CRs: {len(rows)}\n")
        if not rows:
            return 0
        ok_count = 0
        for row in rows:
            sys_id = row["sys_id"]
            num = row.get("number", "")
            state = row.get("state", "")
            short = (row.get("short_description") or "")[:60]
            notes = (row.get("work_notes") or "").lower()
            reject = _REJECT_MARKER in notes or "rejected" in notes
            ok, detail = await _close_cr(client, sys_id, current_state=str(state), reject=reject, dry_run=args.dry_run)
            tag = "OK" if ok else "FAIL"
            verb = "REJECT" if reject else "CLOSE "
            print(f"  [{tag}] {verb} {num}  state={state:>3}  {short:60}  {detail}")
            if ok:
                ok_count += 1
        print(f"\n  advanced: {ok_count}/{len(rows)}")
        return 0 if ok_count == len(rows) else 1


def main():
    ap = argparse.ArgumentParser(description="auto-advance stuck CVE CRs in PDI")
    ap.add_argument("--max", type=int, default=200)
    ap.add_argument("--number-min", default="", help="lower CR number bound (e.g. CHG0041511)")
    ap.add_argument("--number-max", default="", help="upper CR number bound (e.g. CHG0041610)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
