# SPDX-License-Identifier: Apache-2.0
"""Unit guard for :func:`stargraph.ovarp.harness.load_bundle_from_store`.

The replay-bundle reader takes its digest from the (untrusted) receipt
``replay_trace``. A crafted digest must be rejected as a non-content-address
*before* it is used as a path component, so a malicious receipt cannot drive a
path traversal or an unbounded read (`/dev/zero`) at offline-verify time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from stargraph.errors import StargraphRuntimeError
from stargraph.ovarp.harness import load_bundle_from_store

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.unit
@pytest.mark.parametrize(
    "digest",
    [
        "sha256:../../../../etc/passwd",  # path traversal
        "sha256:../../dev/zero",  # unbounded-read target
        "sha256:not-hex-at-all",
        "sha256:" + "a" * 63,  # one short of a sha256 hexdigest
        "sha256:" + "a" * 65,  # one over
        "sha256:" + "A" * 64,  # uppercase — blobs are lowercase hex
        "a" * 64 + "/../../etc/passwd",  # no scheme prefix, still traversal
    ],
)
def test_load_bundle_rejects_non_sha256_digest(tmp_path: Path, digest: str) -> None:
    """A digest that is not a lowercase 64-char sha256 hex raises before any read."""
    with pytest.raises(StargraphRuntimeError, match="not a sha256 content-address"):
        load_bundle_from_store(str(tmp_path), digest)
