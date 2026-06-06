# SPDX-License-Identifier: Apache-2.0
"""``stargraph respond <run_id>`` -- HITL response delivery (FR-85, AC-14.4).

Per design §3.1 (``respond.py`` row), the CLI is a thin wrapper over
``POST /v1/runs/{run_id}/respond`` -- the HTTP shape served by
:mod:`stargraph.serve.api`. The CLI is the developer-ergonomic surface
for analyst-driven HITL flows in the CVE-triage workload (design
§9.4 step 5).

CLI shape:

    stargraph respond <run_id> --response @file.json --actor <name>
                   [--server http://localhost:8000]

* ``--response @file.json`` -- JSON payload to send as the body's
  ``response`` field. Mirrors the ``_RespondRequest`` model in
  :mod:`stargraph.serve.api`.
* ``--actor <name>`` -- principal identifier; sent as
  ``Authorization: Bypass <actor>`` so the POC
  :class:`stargraph.serve.auth.BypassAuthProvider` can attribute the
  fact, and Phase 2's :class:`BearerJwtProvider` can also accept the
  same shape when bound to a developer-mode JWKS.
* ``--server <url>`` -- base URL of the running ``stargraph serve``
  process. Defaults to ``http://localhost:8000``.

Error handling per spec:

* 401 -> ``"auth failed"`` (caller's actor was rejected)
* 404 -> ``"run not found or not awaiting input"``
* 409 -> ``"run not awaiting input"`` / ``"already responded"``
* 200 -> JSON body printed to stdout (the ``RunSummary``)
"""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003 -- runtime use by typer.Annotated
from typing import Annotated, Any, cast

import httpx
import typer

__all__ = ["cmd"]


def cmd(
    run_id: Annotated[
        str,
        typer.Argument(help="Run id awaiting input (state: 'awaiting-input')."),
    ],
    response: Annotated[
        Path,
        typer.Option(
            "--response",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="JSON file containing the analyst response payload.",
        ),
    ],
    actor: Annotated[
        str,
        typer.Option(
            "--actor",
            help=(
                "Principal identifier. Sent as 'Authorization: Bypass <actor>' "
                "so the BypassAuthProvider attributes the response fact."
            ),
        ),
    ],
    server: Annotated[
        str,
        typer.Option(
            "--server",
            help="Base URL of the running stargraph serve process.",
        ),
    ] = "http://localhost:8000",
) -> None:
    """Deliver a HITL response to an ``awaiting-input`` run via HTTP (FR-85, AC-14.4).

    Reads ``response.json``, POSTs ``{"response": <body>}`` to
    ``{server}/v1/runs/{run_id}/respond`` with
    ``Authorization: Bypass <actor>``, and prints the resulting
    ``RunSummary`` JSON. Surfaces 401/404/409 with operator-friendly
    messages.
    """
    payload = cast(
        "dict[str, Any]",
        json.loads(response.read_text(encoding="utf-8")),
    )
    body = {"response": payload}
    headers = {"Authorization": f"Bypass {actor}"}
    url = f"{server.rstrip('/')}/v1/runs/{run_id}/respond"

    with httpx.Client() as client:
        resp = client.post(url, json=body, headers=headers)
    status_code = resp.status_code

    if status_code == 200:
        # Surface the RunSummary body verbatim so callers can pipe it.
        try:
            decoded = resp.json()
        except (ValueError, json.JSONDecodeError):
            typer.echo(resp.text)
            return
        typer.echo(json.dumps(decoded, indent=2))
        return

    if status_code == 401:
        typer.echo(f"auth failed for actor={actor!r} (HTTP 401)", err=True)
        raise typer.Exit(code=1)
    if status_code == 404:
        typer.echo(
            f"run {run_id!r} not found or not awaiting input (HTTP 404)",
            err=True,
        )
        raise typer.Exit(code=1)
    if status_code == 409:
        typer.echo(
            f"run {run_id!r} not awaiting input -- already responded "
            f"or in conflicting state (HTTP 409)",
            err=True,
        )
        raise typer.Exit(code=1)

    # Force-loud (FR-6) for any other non-2xx: surface the response body
    # so operators can debug the wire-level error.
    typer.echo(
        f"unexpected status HTTP {status_code} from {url}: {resp.text}",
        err=True,
    )
    raise typer.Exit(code=1)
