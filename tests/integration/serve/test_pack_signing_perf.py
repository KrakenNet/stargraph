# SPDX-License-Identifier: Apache-2.0
"""Phase-3 task 3.21: pack-signing cold-load perf budget (NFR-5).

Asserts the production budget for :func:`stargraph.bosun.signing.verify_pack`
on a fixture pack tree of ~1 MiB content: mean latency under 50 ms.

Each benchmark iteration uses a *fresh* :class:`StaticTrustStore` keyed
on the same pubkey so the JWKS cache (if any) cannot amortize across
iterations -- mirroring "first-sight cold-load" of a pack on a freshly
booted Bosun lifespan. ``pytest-benchmark`` runs the callable
repeatedly and computes ``stats["mean"]`` for the assertion.

The pack tree is built at session scope and reused across iterations
(constructing a 1 MiB tree per iteration would dwarf the verify cost
we're trying to measure). The token is also session-scoped -- signing
is not the contract under test here.

Requirements: NFR-5 (50 ms per pack on cold load), FR-41/FR-42.
Design: §16.8 (perf budget), §16.9 (signing budget calibration).
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from stargraph.bosun.signing import (
    StaticTrustStore,
    sign_pack,
    verify_pack,
)
from stargraph.serve.profiles import ClearedProfile

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_benchmark.fixture import BenchmarkFixture


# ``slow`` keeps this wall-clock benchmark out of the parallel ``test`` lane
# (``-m "not slow" -n auto``): pytest-benchmark is unreliable under xdist and a
# <50 ms budget flakes under multi-worker CPU contention. The ``serve-test`` job
# (``-m "serve"``, serial) is the accurate measurement surface for NFR-5.
pytestmark = [pytest.mark.serve, pytest.mark.integration, pytest.mark.slow]


_PERF_BUDGET_SECONDS = 0.050  # NFR-5: <50 ms per pack on cold load
_PACK_PAYLOAD_BYTES = 1024 * 1024  # ~1 MiB pack content


@pytest.fixture(scope="session")
def _signed_pack(  # pyright: ignore[reportUnusedFunction]
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, str, bytes, str]:
    """Build a ~1 MiB pack tree once per session and sign it.

    Returns ``(pack_dir, token, pub_pem, key_id)`` so each benchmark
    iteration constructs a fresh :class:`StaticTrustStore` over the
    same pubkey (cache-cold from the trust-store side; signature
    verification still has to redo the EdDSA crypto each call).
    """
    base = tmp_path_factory.mktemp("perf_pack")
    pack = base / "pack"
    pack.mkdir()

    # Distribute the ~1 MiB across a handful of files so the tree-walk
    # cost is non-trivial (not a single big file). Use deterministic
    # repeated content (not ``os.urandom``) so the same pack hashes
    # consistently across pytest invocations within the same session.
    (pack / "manifest.yaml").write_bytes(b"id: perf-pack\nversion: 1.0\n")
    rules_dir = pack / "rules"
    rules_dir.mkdir()
    chunk = _PACK_PAYLOAD_BYTES // 8
    for i in range(8):
        line = b"; rule " + str(i).encode() + b"\n"
        (rules_dir / f"rule_{i}.clp").write_bytes(line * (chunk // len(line)))

    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    pub_pem = pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    pub_der = pub.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    key_id = hashlib.sha256(pub_der).hexdigest()[:16]

    token = sign_pack(pack, priv_pem, key_id)
    return pack, token, pub_pem, key_id


@pytest.mark.serve
def test_verify_pack_cold_load_under_50ms(
    _signed_pack: tuple[Path, str, bytes, str],
    benchmark: BenchmarkFixture,
) -> None:
    """``verify_pack(...)`` mean latency under 50 ms on a ~1 MiB pack (NFR-5).

    Builds a fresh :class:`StaticTrustStore` per iteration so the
    benchmark cannot amortize trust-store warmup across calls. The
    JWT signature crypto + the BLAKE3 tree-walk run every iteration.
    """
    pack, token, pub_pem, key_id = _signed_pack
    profile = ClearedProfile()

    def _verify_once() -> None:
        # Fresh trust store each iteration -- cold-load semantics.
        trust = StaticTrustStore({key_id: pub_pem})
        result = verify_pack(pack, token, trust, profile)
        assert result.verified is True

    benchmark(_verify_once)

    # ``benchmark.stats`` exposes the timing distribution. The "mean"
    # field is the canonical NFR-5 surface (per design §16.9 worded
    # "<50 ms per pack on cold load"). pytest-benchmark's stats object
    # is dynamically attributed; cast through ``Any`` so the float
    # comparison type-checks without polluting prod typing.
    mean = cast("float", benchmark.stats.stats.mean)  # pyright: ignore[reportUnknownMemberType, reportOptionalMemberAccess]
    assert mean < _PERF_BUDGET_SECONDS, (
        f"verify_pack mean latency {mean * 1000:.2f}ms exceeded NFR-5 "
        f"budget {_PERF_BUDGET_SECONDS * 1000:.0f}ms"
    )
