# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the SQLite tiny model registry (FR-31, design §3.9.3).

Covers the four core methods (``register`` / ``load`` / ``load_alias`` /
``alias``) plus the ``content_hash`` skew gate and the v1 ``file://``-only
URI restriction.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from stargraph.errors import IncompatibleModelHashError
from stargraph.ml.registry import ModelEntry, ModelRegistry

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


def _write_model_file(path: Path, payload: bytes = b"fake-model-bytes") -> str:
    """Write ``payload`` at ``path`` and return its sha256 hex digest."""
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture
async def registry(tmp_path: Path):  # type: ignore[no-untyped-def]
    """Bootstrapped :class:`ModelRegistry` against a tmp_path SQLite file."""
    reg = ModelRegistry(tmp_path / "models.db")
    await reg.bootstrap()
    try:
        yield reg
    finally:
        await reg.close()


# --------------------------------------------------------------------------- #
# register / load                                                             #
# --------------------------------------------------------------------------- #


async def test_register_then_load_round_trip(registry: ModelRegistry, tmp_path: Path) -> None:
    """register -> load round-trips all 8 columns and verifies content_hash."""
    model_path = tmp_path / "logreg.joblib"
    sha = _write_model_file(model_path)

    await registry.register(
        model_id="customer-churn",
        version="1.0.0",
        runtime="sklearn",
        file_uri=model_path.as_uri(),
        content_hash=sha,
        metadata={"trained_on": "2026-01-01", "rows": 12345},
    )

    entry = await registry.load("customer-churn", "1.0.0")
    assert isinstance(entry, ModelEntry)
    assert entry.model_id == "customer-churn"
    assert entry.version == "1.0.0"
    assert entry.runtime == "sklearn"
    assert entry.file_uri == model_path.as_uri()
    assert entry.content_hash == sha
    assert entry.framework == "sklearn"  # defaults to runtime
    assert entry.metadata == {"trained_on": "2026-01-01", "rows": 12345}
    assert entry.created_at  # ISO-8601 string populated


async def test_load_missing_row_raises_keyerror(registry: ModelRegistry) -> None:
    """load on an unregistered (model_id, version) raises KeyError."""
    with pytest.raises(KeyError, match="no model registered"):
        await registry.load("nope", "0.0.0")


async def test_register_rejects_unknown_runtime(registry: ModelRegistry, tmp_path: Path) -> None:
    """register surfaces ValueError before touching SQLite for bogus runtime."""
    model_path = tmp_path / "x.bin"
    sha = _write_model_file(model_path)
    with pytest.raises(ValueError, match="unknown runtime"):
        await registry.register(
            model_id="m",
            version="1",
            runtime="pytorch",  # not in the v1 set
            file_uri=model_path.as_uri(),
            content_hash=sha,
        )


# --------------------------------------------------------------------------- #
# alias / load_alias                                                          #
# --------------------------------------------------------------------------- #


async def test_alias_then_load_alias(registry: ModelRegistry, tmp_path: Path) -> None:
    """alias points 'production' at a version; load_alias resolves and verifies."""
    model_path = tmp_path / "model.onnx"
    sha = _write_model_file(model_path, payload=b"fake-onnx-bytes")
    await registry.register(
        model_id="ranker",
        version="2025.04",
        runtime="onnx",
        file_uri=model_path.as_uri(),
        content_hash=sha,
    )

    await registry.alias(model_id="ranker", alias="production", version="2025.04")
    entry = await registry.load_alias("ranker", "production")
    assert entry.model_id == "ranker"
    assert entry.version == "2025.04"
    assert entry.runtime == "onnx"


async def test_alias_repointed_resolves_to_new_version(
    registry: ModelRegistry, tmp_path: Path
) -> None:
    """alias is upsert-style: re-aliasing 'production' switches the pointer."""
    p1 = tmp_path / "v1.ubj"
    p2 = tmp_path / "v2.ubj"
    s1 = _write_model_file(p1, payload=b"bytes-v1")
    s2 = _write_model_file(p2, payload=b"bytes-v2")
    for ver, p, s in (("1.0.0", p1, s1), ("2.0.0", p2, s2)):
        await registry.register(
            model_id="rec",
            version=ver,
            runtime="xgboost",
            file_uri=p.as_uri(),
            content_hash=s,
        )

    await registry.alias(model_id="rec", alias="production", version="1.0.0")
    first = await registry.load_alias("rec", "production")
    assert first.version == "1.0.0"

    await registry.alias(model_id="rec", alias="production", version="2.0.0")
    second = await registry.load_alias("rec", "production")
    assert second.version == "2.0.0"


async def test_alias_to_unknown_version_raises_keyerror(
    registry: ModelRegistry,
) -> None:
    """alias() refuses to point at an unregistered (model_id, version)."""
    with pytest.raises(KeyError, match="not registered"):
        await registry.alias(model_id="ghost", alias="production", version="0.0.0")


async def test_load_alias_missing_alias_raises_keyerror(
    registry: ModelRegistry,
) -> None:
    """load_alias on an undefined alias raises KeyError."""
    with pytest.raises(KeyError, match="no alias"):
        await registry.load_alias("anything", "production")


# --------------------------------------------------------------------------- #
# content_hash skew gate                                                      #
# --------------------------------------------------------------------------- #


async def test_load_content_hash_mismatch_raises_skew_error(
    registry: ModelRegistry, tmp_path: Path
) -> None:
    """If the on-disk file bytes change after register, load() must loud-fail."""
    model_path = tmp_path / "model.joblib"
    sha_original = _write_model_file(model_path, payload=b"original")
    await registry.register(
        model_id="m",
        version="1",
        runtime="sklearn",
        file_uri=model_path.as_uri(),
        content_hash=sha_original,
    )
    # Tampering: rewrite the file with different bytes; sha will change.
    model_path.write_bytes(b"tampered-bytes")

    with pytest.raises(IncompatibleModelHashError) as excinfo:
        await registry.load("m", "1")
    err = excinfo.value
    assert err.context["model_id"] == "m"
    assert err.context["expected_sha256"] == sha_original
    assert err.context["actual_sha256"] != sha_original


async def test_load_alias_inherits_content_hash_check(
    registry: ModelRegistry, tmp_path: Path
) -> None:
    """load_alias goes through load(), so the hash gate fires for aliases too."""
    model_path = tmp_path / "model.joblib"
    sha = _write_model_file(model_path, payload=b"seed")
    await registry.register(
        model_id="m",
        version="1",
        runtime="sklearn",
        file_uri=model_path.as_uri(),
        content_hash=sha,
    )
    await registry.alias(model_id="m", alias="production", version="1")
    model_path.write_bytes(b"swapped")

    with pytest.raises(IncompatibleModelHashError):
        await registry.load_alias("m", "production")


# --------------------------------------------------------------------------- #
# v1 file:// URI restriction                                                  #
# --------------------------------------------------------------------------- #


async def test_load_rejects_non_file_uri(
    registry: ModelRegistry,
) -> None:
    """v1 only supports file:// URIs; other schemes raise ValueError on load."""
    # Register a row with an http:// URI — the SQL CHECK doesn't gate scheme;
    # the rejection happens at load() time when we'd otherwise have to fetch.
    await registry.register(
        model_id="remote",
        version="1",
        runtime="onnx",
        file_uri="https://example.invalid/model.onnx",
        content_hash="0" * 64,
    )
    with pytest.raises(ValueError, match="file://"):
        await registry.load("remote", "1")


# --------------------------------------------------------------------------- #
# Persistence across instances                                                #
# --------------------------------------------------------------------------- #


async def test_registry_persists_across_close_and_reopen(tmp_path: Path) -> None:
    """Bootstrapping a fresh ModelRegistry against the same path sees prior rows."""
    db_path = tmp_path / "models.db"
    model_path = tmp_path / "m.joblib"
    sha = _write_model_file(model_path)

    reg1 = ModelRegistry(db_path)
    await reg1.bootstrap()
    await reg1.register(
        model_id="persist",
        version="1",
        runtime="sklearn",
        file_uri=model_path.as_uri(),
        content_hash=sha,
    )
    await reg1.alias(model_id="persist", alias="production", version="1")
    await reg1.close()

    reg2 = ModelRegistry(db_path)
    await reg2.bootstrap()
    try:
        entry = await reg2.load_alias("persist", "production")
        assert entry.model_id == "persist"
        assert entry.version == "1"
    finally:
        await reg2.close()
