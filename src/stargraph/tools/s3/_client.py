# SPDX-License-Identifier: Apache-2.0
"""Lazy boto3 client factory for the ``s3`` pack (the tests' mock seam)."""

from __future__ import annotations

import importlib
from typing import Any

from stargraph.errors import StargraphRuntimeError

__all__ = ["build_client"]


def build_client() -> Any:
    """An S3 client from boto3's default credential/region chain.

    boto3 ships no type stubs; the client is deliberately ``Any`` (the
    same seam pattern as ``stargraph.ml.export``). Tools call this via
    the module (``_client.build_client()``) so tests can monkeypatch one
    seam with a fake.
    """
    try:
        boto3 = importlib.import_module("boto3")
    except ImportError as exc:
        raise StargraphRuntimeError(
            "s3 tools require boto3; install it with: pip install stargraph[tools-saas]",
        ) from exc
    return boto3.client("s3")
