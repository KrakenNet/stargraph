# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``bosun/budgets`` pack-signing wiring (T12).

Pins ``_PACK_ROOT`` constant exposure and round-trip through
:func:`stargraph.bosun.signing.sign_pack`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from stargraph.bosun import budgets
from stargraph.bosun.signing import sign_pack

pytestmark = pytest.mark.unit


@pytest.mark.unit
def test_pack_root_constant_resolves_to_module_dir() -> None:
    """``_PACK_ROOT = Path(__file__).parent`` pins module-local pack tree (T12)."""
    pack_root: Path = budgets._PACK_ROOT  # pyright: ignore[reportPrivateUsage]
    assert isinstance(pack_root, Path)
    assert pack_root.is_dir()
    assert (pack_root / "rules.clp").exists()


@pytest.mark.unit
def test_sign_pack_against_budgets_pack_root_returns_jwt() -> None:
    """``sign_pack(tree=_PACK_ROOT, ...)`` returns a JWT string (T12)."""
    signing_key = Ed25519PrivateKey.generate()
    pem_bytes = signing_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    jwt = sign_pack(
        tree=budgets._PACK_ROOT,  # pyright: ignore[reportPrivateUsage]
        signing_key=pem_bytes,
        key_id="test-key-1",
    )
    assert isinstance(jwt, str)
    assert jwt.count(".") == 2
