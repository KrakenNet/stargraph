# SPDX-License-Identifier: Apache-2.0
"""TDD-GREEN: FR-8 embed-hash 5-tuple payload unit tests.

Exercises the payload-building portion of the FR-8 drift gate
(:func:`stargraph.stores._common._expected_embed_meta`) without booting
LanceDB. Three checks per spec task 3.3:

1. ``test_payload_includes_5tuple`` -- payload exposes all five drift-gate
   keys (``model_id``, ``revision``, ``content_hash``, ``ndims``,
   ``schema_v``).
2. ``test_payload_serialization_orjson`` -- payload roundtrips through
   :mod:`orjson` byte-for-byte.
3. ``test_schema_v_starts_at_1`` -- first bootstrap pins ``schema_v`` at
   ``"1"`` (string-encoded per the sidecar Arrow schema).
"""

from __future__ import annotations

import orjson
import pytest

from stargraph.stores import _common
from stargraph.stores.embeddings import FakeEmbedder

# Internal helpers under test (FR-8 drift gate primitives).
_EMBED_META_KEYS = _common._EMBED_META_KEYS  # pyright: ignore[reportPrivateUsage]
_expected_embed_meta = _common._expected_embed_meta  # pyright: ignore[reportPrivateUsage]

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


def test_payload_includes_5tuple() -> None:
    """Payload dict surfaces every FR-8 drift-gate key."""
    payload = _expected_embed_meta(FakeEmbedder(), schema_v=1)

    assert set(payload.keys()) == set(_EMBED_META_KEYS)
    assert set(payload.keys()) == {
        "model_id",
        "revision",
        "content_hash",
        "ndims",
        "schema_v",
    }


def test_payload_serialization_orjson() -> None:
    """Payload roundtrips identically through ``orjson.dumps`` / ``loads``."""
    payload = _expected_embed_meta(FakeEmbedder(), schema_v=1)

    blob = orjson.dumps(payload)
    restored = orjson.loads(blob)

    assert restored == payload


def test_schema_v_starts_at_1() -> None:
    """First bootstrap pins ``schema_v`` at ``"1"`` (sidecar uses str values)."""
    payload = _expected_embed_meta(FakeEmbedder(), schema_v=1)

    assert payload["schema_v"] == "1"
