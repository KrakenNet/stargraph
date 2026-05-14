#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Pull and triage retrospective suggestions from the pgvector store.

Designed for post-100-CVE-sweep review.  Group rows by CVE, summarize by
prevention category + confidence, surface the top-N per CVE.

Examples
--------
Pull all suggestions written in the last 6 hours, group by CVE::

    uv run python demos/cve_remediation/scripts/review_retros.py \
        --since "6 hours" --top 5

Filter by CWE family::

    uv run python demos/cve_remediation/scripts/review_retros.py \
        --cwe CWE-78 --top 10

Dump CSV (one row per suggestion) for spreadsheet triage::

    uv run python demos/cve_remediation/scripts/review_retros.py \
        --since "12 hours" --csv > retros.csv

Notes
-----
* Requires the ``cve-rem-pgvector`` Postgres container (POSTGRES_DB=cve_rem_vec).
* Joins ``cve_rem_retro_suggestions`` against ``cve_rem_retro_embeddings``
  for any rows missing direct ``cve_id`` (legacy rows from earlier graph
  binaries).  New rows carry cve_id directly.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from typing import Any

import asyncpg


_DEFAULT_DSN = os.environ.get(
    "PGVECTOR_DSN",
    "postgresql://harbor:harbor@localhost:5440/cve_rem_vec",
)


# Tagged-line format example:
# [prevention/pipeline|cited=verify_unpatched|conf=9100] [cite=URL] body...
_TAG_RE = re.compile(
    r"^\[prevention/(?P<cat>[^|\]]+)"
    r"\|cited=(?P<cited>[^|\]]*)"
    r"\|conf=(?P<conf>\d+)\]"
    r"(?:\s*\[cite=(?P<cite>[^\]]+)\])?\s*"
    r"(?P<body>.*)$"
)


def _parse_tagged(text: str) -> dict[str, Any]:
    m = _TAG_RE.match(text or "")
    if not m:
        return {
            "category": "untagged",
            "cited_signals": "",
            "confidence_bp": 0,
            "citation_url": "",
            "body": text or "",
        }
    return {
        "category": m.group("cat"),
        "cited_signals": m.group("cited") or "",
        "confidence_bp": int(m.group("conf") or "0"),
        "citation_url": m.group("cite") or "",
        "body": m.group("body").strip(),
    }


async def _fetch(
    dsn: str, since: str, cwe: str | None, cve: str | None
) -> list[dict[str, Any]]:
    # Sanitize: only digits, letters, space allowed in interval string.
    safe_since = re.sub(r"[^0-9a-zA-Z ]", "", since or "24 hours") or "24 hours"
    conn = await asyncpg.connect(dsn)
    try:
        # Resolve missing cve_id / cwe via embeddings table (legacy rows).
        sql = f"""
        SELECT s.id, s.retro_id, s.suggestion_text,
               COALESCE(s.cve_id, e.cve_id) AS cve_id,
               COALESCE(s.cwe,    e.cwe)    AS cwe,
               s.generated_at
          FROM cve_rem_retro_suggestions s
          LEFT JOIN cve_rem_retro_embeddings e
                 ON e.retro_id = s.retro_id
         WHERE s.generated_at > now() - INTERVAL '{safe_since}'
        """
        params: list[Any] = []
        if cwe:
            sql += "   AND COALESCE(s.cwe, e.cwe) = $%d" % (len(params) + 1)
            params.append(cwe)
        if cve:
            sql += "   AND COALESCE(s.cve_id, e.cve_id) = $%d" % (len(params) + 1)
            params.append(cve)
        sql += " ORDER BY s.generated_at DESC, s.id DESC"
        rows = await conn.fetch(sql, *params)
    finally:
        await conn.close()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "retro_id": r["retro_id"],
            "suggestion_text": r["suggestion_text"],
            "cve_id": r["cve_id"] or "",
            "cwe": r["cwe"] or "",
            "generated_at": r["generated_at"].isoformat()
                if r["generated_at"] else "",
            **_parse_tagged(r["suggestion_text"] or ""),
        })
    return out


def _group_by_cve(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        cve = r["cve_id"] or "UNKNOWN"
        groups[cve].append(r)
    return groups


def _print_grouped(rows: list[dict[str, Any]], top: int) -> None:
    groups = _group_by_cve(rows)
    print(f"=== retro suggestions: {len(rows)} total across {len(groups)} CVEs ===\n")
    for cve in sorted(groups):
        items = sorted(
            groups[cve], key=lambda r: r["confidence_bp"], reverse=True
        )
        print(f"--- {cve} ({len(items)} suggestions) ---")
        cwe = items[0]["cwe"] if items else ""
        if cwe:
            print(f"   cwe: {cwe}")
        for r in items[:top]:
            cat = r["category"]
            conf = r["confidence_bp"]
            cited = r["cited_signals"]
            cite = r["citation_url"]
            body = r["body"][:240]
            print(f"   [{cat}|conf={conf}|cited={cited}]"
                  f"{' [cite=' + cite + ']' if cite else ''}")
            print(f"     {body}")
        print()


def _print_csv(rows: list[dict[str, Any]]) -> None:
    cols = [
        "cve_id", "cwe", "category", "confidence_bp",
        "cited_signals", "citation_url", "body",
        "retro_id", "generated_at",
    ]
    w = csv.DictWriter(sys.stdout, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in cols})


def main() -> int:
    ap = argparse.ArgumentParser(description="Triage retro suggestions")
    ap.add_argument(
        "--since", default="24 hours",
        help="PostgreSQL interval string (default: '24 hours')",
    )
    ap.add_argument("--cwe", help="filter by CWE-id (e.g. CWE-78)")
    ap.add_argument("--cve", help="filter by exact CVE-id")
    ap.add_argument(
        "--top", type=int, default=5,
        help="top-N suggestions per CVE (grouped output, default: 5)",
    )
    ap.add_argument(
        "--csv", action="store_true",
        help="emit CSV (one row per suggestion) instead of grouped text",
    )
    ap.add_argument(
        "--dsn", default=_DEFAULT_DSN,
        help="postgres DSN (default from PGVECTOR_DSN env)",
    )
    args = ap.parse_args()

    import asyncio
    try:
        rows = asyncio.run(_fetch(args.dsn, args.since, args.cwe, args.cve))
    except Exception as exc:  # noqa: BLE001
        print(f"! fetch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.csv:
        _print_csv(rows)
    else:
        _print_grouped(rows, args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
