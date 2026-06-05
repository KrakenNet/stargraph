# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for integration tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import dspy  # type: ignore[import-untyped]
import pytest
from tests.fixtures.lm_stub import StandinLM

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def standin_lm() -> Iterator[None]:
    """Run the test under a canned-JSON DSPy LM (no live model required).

    Opt in per module with ``pytest.mark.usefixtures("standin_lm")`` so
    tests that assert no-LM failure modes (e.g. force-loud adapter errors)
    stay unaffected.
    """
    with dspy.context(lm=StandinLM()):  # pyright: ignore[reportUnknownMemberType]
        yield
