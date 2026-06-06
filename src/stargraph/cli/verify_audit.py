# SPDX-License-Identifier: Apache-2.0
"""``stargraph verify-audit <log>`` -- offline chained-audit-log verifier.

Verifies a hash-chained, JWS-signed audit log written by
:class:`stargraph.audit.jsonl.ChainedJSONLAuditSink`: structural shape,
``prev_sha256`` hash linkage, and every line's EdDSA JWS signature.
With ``--expected-head`` / ``--anchor-token`` it also detects tail
truncation against an out-of-band anchor (a mirrored line hash or a
checkpoint JWS). Read-only -- never writes to the log.

Exit codes: 0 chain valid, 1 user error (missing log / pubkey),
2 verification failure (broken chain, bad signature, missing anchor).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path  # noqa: TC003 -- runtime use by typer.Annotated
from typing import Annotated

import typer
from fathom.chained_log import verify_chain

__all__ = ["cmd"]


def cmd(
    log: Annotated[
        Path,
        typer.Argument(help="Chained JSONL audit log written by ChainedJSONLAuditSink."),
    ],
    pubkey: Annotated[
        Path | None,
        typer.Option(
            "--pubkey",
            help="Ed25519 public key PEM (default: <log>.pub.pem beside the log).",
        ),
    ] = None,
    expected_head: Annotated[
        str | None,
        typer.Option(
            "--expected-head",
            help="Out-of-band mirrored line hash; fails if absent (tail truncation).",
        ),
    ] = None,
    anchor_token: Annotated[
        str | None,
        typer.Option(
            "--anchor-token",
            help="Checkpoint JWS token; its checkpoint line must appear in the log.",
        ),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit the verification result as JSON to stdout."),
    ] = False,
) -> None:
    """Offline-verify a chained audit log (hash linkage + JWS signatures)."""
    pubkey_path = pubkey if pubkey is not None else log.with_name(log.name + ".pub.pem")
    if not log.exists():
        typer.echo(f"verify-audit: log not found: {log}", err=True)
        raise typer.Exit(code=1)
    if not pubkey_path.exists():
        typer.echo(f"verify-audit: pubkey not found: {pubkey_path}", err=True)
        raise typer.Exit(code=1)

    result = verify_chain(
        log,
        pubkey_path,
        expected_head=expected_head,
        anchor_token=anchor_token,
    )

    if as_json:
        typer.echo(json.dumps(asdict(result)))
    elif result.ok:
        anchored = " (anchor ok)" if result.anchor_ok else ""
        typer.echo(f"chain valid -- {result.count} records, head {result.head_sha256}{anchored}")
    else:
        typer.echo(f"verify-audit: {result.error}", err=True)

    if not result.ok:
        raise typer.Exit(code=2)
