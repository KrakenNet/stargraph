# SPDX-License-Identifier: Apache-2.0
"""``stargraph ovarp-reproduce --store <dir>`` -- the OVARP producer-runtime reproducer.

This is the ``--replayer`` command ``ovarp verify --replay`` drives for a
StarGraph producer-runtime ``replayable`` receipt (ADR-0012). OVARP writes a JSON
request to this process's **stdin** and reads a single JSON object from its
**stdout**:

* in  (stdin):  ``{"ovarp_reproduce":"v1", "runtime":..., "bundle":"sha256:...",
  "store":..., "model_digest":..., "decoding_digest":..., "seed":"<hex>"}``
* out (stdout): ``{"output": <producer_output>}``

OVARP then canonicalizes (JCS) + hashes the returned ``output`` and requires it to
equal the receipt's ``result.digest``; any spawn/exit/parse failure VOIDs the
receipt at step 6. So this command must write **only** the result JSON to stdout —
every diagnostic goes to stderr.

The tick itself is reconstructed by :func:`stargraph.ovarp.harness.reproduce_from_bundle`
from the content-addressed replay bundle; this module is the thin stdin/stdout +
RATS reference-value-gate wrapper around it.

Exit codes: 0 reproduced (``output`` printed), 2 any failure (bad request, pinned
model/decoding mismatch, bundle/integrity error) — non-zero is what OVARP treats
as a failed reproduction.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path  # noqa: TC003 -- runtime use by typer.Annotated
from typing import Annotated, Any

import typer
from typer import Exit

from stargraph.ovarp.harness import (
    FATHOM_ROUTER_DECODING_DIGEST,
    FATHOM_ROUTER_MODEL_DIGEST,
    reproduce_from_bundle,
)

__all__ = ["cmd"]


def _fail(message: str) -> Exit:
    """Emit ``message`` to stderr and return a code-2 Exit (VOID at OVARP step 6).

    The ``-> Exit`` annotation is a bare ``Name`` so the FR-24 raise walker's
    helper carve-out recognizes ``raise _fail(...)`` as producing an
    allow-listed framework-exit exception.
    """
    typer.echo(f"ovarp-reproduce: {message}", err=True)
    return Exit(code=2)


def cmd(
    store: Annotated[
        Path,
        typer.Option(
            "--store", help="OVARP content-addressed store root (holds the replay bundle)."
        ),
    ],
) -> None:
    """Reproduce a StarGraph producer-runtime tick from an OVARP replay request on stdin."""
    try:
        request: dict[str, Any] = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError) as exc:
        raise _fail(f"stdin is not a JSON reproduce request: {exc}") from exc

    if request.get("ovarp_reproduce") != "v1":
        raise _fail(f"unknown reproduce protocol {request.get('ovarp_reproduce')!r} (want 'v1')")

    # RATS reference-value gate (ADR-0012): only reproduce under the model/decoding
    # this deterministic Fathom router actually implements. A request pinned to a
    # different reference value is refused, not silently reproduced.
    if request.get("model_digest") != FATHOM_ROUTER_MODEL_DIGEST:
        raise _fail(
            f"pinned model_digest {request.get('model_digest')!r} is not the Fathom router's"
        )
    if request.get("decoding_digest") != FATHOM_ROUTER_DECODING_DIGEST:
        raise _fail(
            f"pinned decoding_digest {request.get('decoding_digest')!r} is not the Fathom router's"
        )

    bundle_digest = request.get("bundle")
    if not isinstance(bundle_digest, str) or not bundle_digest:
        raise _fail("reproduce request is missing a 'bundle' content-address")

    try:
        output = asyncio.run(reproduce_from_bundle(str(store), bundle_digest))
    except Exception as exc:
        raise _fail(f"reproduction failed: {exc}") from exc

    # The ONLY stdout write: OVARP parses this for `output` and JCS-hashes it.
    sys.stdout.write(json.dumps({"output": output}))
