# SPDX-License-Identifier: Apache-2.0
"""Tests for stargraph run --lm-* flags (DSPy LM configuration)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from stargraph.cli import app

SAMPLE_GRAPH = Path(__file__).resolve().parents[2] / "fixtures" / "sample-graph.yaml"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.mark.integration
def test_lm_url_without_lm_model_fails(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            str(SAMPLE_GRAPH),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
            "--lm-url",
            "http://localhost:11434/v1",
            "--quiet",
            "--no-summary",
        ],
    )
    assert result.exit_code != 0
    assert "must be specified together" in result.output.lower()


@pytest.mark.integration
def test_lm_model_without_lm_url_fails(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            str(SAMPLE_GRAPH),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
            "--lm-model",
            "gpt-oss:20b",
            "--quiet",
            "--no-summary",
        ],
    )
    assert result.exit_code != 0


@pytest.mark.integration
def test_neither_lm_flag_skips_dspy_configure(runner: CliRunner, tmp_path: Path) -> None:
    """Graphs without DSPy nodes work fine without --lm-* flags."""
    result = runner.invoke(
        app,
        [
            "run",
            str(SAMPLE_GRAPH),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
            "--quiet",
            "--no-summary",
        ],
    )
    assert result.exit_code == 0


@pytest.mark.integration
def test_both_lm_flags_configure_dspy(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When both --lm-url and --lm-model are set, dspy.configure is called."""
    captured: dict[str, object] = {}

    import dspy  # pyright: ignore[reportMissingTypeStubs]

    real_configure = cast("object", dspy.configure)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    real_lm_cls = dspy.LM  # pyright: ignore[reportUnknownMemberType]

    def fake_configure(**kwargs: object) -> None:
        captured["configure_kwargs"] = kwargs

    class FakeLM:
        def __init__(self, model: str, **kwargs: object) -> None:
            captured["model"] = model
            captured["lm_kwargs"] = kwargs

    monkeypatch.setattr(dspy, "configure", fake_configure)
    monkeypatch.setattr(dspy, "LM", FakeLM)

    result = runner.invoke(
        app,
        [
            "run",
            str(SAMPLE_GRAPH),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
            "--lm-url",
            "http://localhost:11434/v1",
            "--lm-model",
            "gpt-oss:20b",
            "--lm-key",
            "test-key",
            "--lm-timeout",
            "30",
            "--quiet",
            "--no-summary",
        ],
    )

    # restore (defensive)
    monkeypatch.setattr(dspy, "configure", real_configure)
    monkeypatch.setattr(dspy, "LM", real_lm_cls)

    assert result.exit_code == 0, result.output
    assert captured["model"] == "openai/gpt-oss:20b"
    lm_kwargs = captured["lm_kwargs"]
    assert isinstance(lm_kwargs, dict)
    assert lm_kwargs["api_base"] == "http://localhost:11434/v1"
    assert lm_kwargs["api_key"] == "test-key"
    assert lm_kwargs["timeout"] == 30


@pytest.mark.integration
def test_lm_key_default_is_placeholder(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}
    import dspy  # pyright: ignore[reportMissingTypeStubs]

    def fake_configure(**kwargs: object) -> None:
        del kwargs

    class FakeLM:
        def __init__(self, model: str, **kwargs: object) -> None:
            del model
            captured["api_key"] = kwargs.get("api_key")

    monkeypatch.setattr(dspy, "configure", fake_configure)
    monkeypatch.setattr(dspy, "LM", FakeLM)

    result = runner.invoke(
        app,
        [
            "run",
            str(SAMPLE_GRAPH),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
            "--lm-url",
            "http://localhost:11434/v1",
            "--lm-model",
            "gpt-oss:20b",
            "--quiet",
            "--no-summary",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["api_key"] == "placeholder"
