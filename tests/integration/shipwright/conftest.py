# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for Shipwright integration tests."""

from __future__ import annotations

import os
from pathlib import Path

import dspy  # type: ignore[import-untyped]
import httpx
import pytest


@pytest.fixture(scope="session")
def shipwright_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "src" / "stargraph" / "skills" / "shipwright"


@pytest.fixture
def tmp_artifact_root(tmp_path: Path) -> Path:
    out = tmp_path / "graphs"
    out.mkdir()
    return out


@pytest.fixture(scope="session")
def ollama_config() -> dict[str, str | int]:
    """Connection settings for the llm-ollama Docker container.

    Tests that need a real LLM use the `ollama_lm` fixture, which skips
    if the container isn't reachable. Plain unit tests that stub the
    predictor never touch this fixture.
    """
    return {
        "url": os.environ.get("LLM_OLLAMA_URL", "http://localhost:11434/v1"),
        "model": os.environ.get("LLM_OLLAMA_MODEL", "llama3.1:8b"),
        "timeout_s": int(os.environ.get("LLM_OLLAMA_TIMEOUT_S", "30")),
    }


@pytest.fixture(scope="session")
def ollama_lm(ollama_config: dict[str, str | int]) -> dspy.LM:  # pyright: ignore[reportUnknownParameterType]
    """A configured `dspy.LM` pointing at llm-ollama; skip if not reachable."""
    url = ollama_config["url"]
    try:
        r = httpx.get(f"{url}/models", timeout=2.0)
        r.raise_for_status()
    except (httpx.HTTPError, OSError):
        pytest.skip(f"llm-ollama not reachable at {url}")

    return dspy.LM(
        f"openai/{ollama_config['model']}",
        api_base=url,
        api_key="ollama",  # any non-empty string; ollama ignores
        timeout=ollama_config["timeout_s"],
    )
