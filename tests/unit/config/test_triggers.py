# SPDX-License-Identifier: Apache-2.0
"""Tests for ``harbor.config.triggers.load_triggers``.

Covers:
  * empty / missing-file paths
  * field-name translation (``id``→``trigger_id``, ``expr``→``cron_expression``)
  * env-var resolution for webhook secrets (current required, previous optional)
  * ``description`` is dropped (operator-facing, not a spec field)
  * malformed YAML / non-mapping rows fail loud
  * unknown spec field fails at ``model_validate``
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harbor.config.triggers import (
    LoadedTriggers,
    ManualDescriptor,
    load_triggers,
)
from harbor.errors import HarborRuntimeError

if TYPE_CHECKING:
    from pathlib import Path


def _write_yaml(tmp_path: Path, body: str) -> Path:
    (tmp_path / "triggers.yaml").write_text(body, encoding="utf-8")
    return tmp_path


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    out = load_triggers(tmp_path)
    assert isinstance(out, LoadedTriggers)
    assert out.cron_specs == []
    assert out.webhook_specs == []
    assert out.manual_descriptors == []
    assert out.version == "1.0"


def test_empty_yaml_returns_empty(tmp_path: Path) -> None:
    _write_yaml(tmp_path, "")
    out = load_triggers(tmp_path)
    assert out.cron_specs == []
    assert out.webhook_specs == []
    assert out.manual_descriptors == []


def test_manual_rows_loaded(tmp_path: Path) -> None:
    _write_yaml(
        tmp_path,
        """
version: "1.0"
manual:
  - id: manual.replay
    graph_id: graph:sdw-main
    description: Operator-fired replay
  - id: manual.audit
    graph_id: graph:audit
""",
    )
    out = load_triggers(tmp_path)
    assert len(out.manual_descriptors) == 2
    assert out.manual_descriptors[0] == ManualDescriptor(
        trigger_id="manual.replay",
        graph_id="graph:sdw-main",
        description="Operator-fired replay",
    )
    assert out.manual_descriptors[1].description == ""


def test_cron_translation(tmp_path: Path) -> None:
    _write_yaml(
        tmp_path,
        """
cron:
  - id: cron.daily_anchor
    graph_id: graph:sdw-audit-anchor
    expr: "0 3 * * *"
    tz: UTC
    missed_fire_policy: fire_once_catchup
    params: {trigger_kind: cron}
    description: Daily anchor
""",
    )
    out = load_triggers(tmp_path)
    assert len(out.cron_specs) == 1
    spec = out.cron_specs[0]
    assert spec.trigger_id == "cron.daily_anchor"
    assert spec.cron_expression == "0 3 * * *"
    assert spec.tz == "UTC"
    assert spec.graph_id == "graph:sdw-audit-anchor"
    assert spec.params == {"trigger_kind": "cron"}
    assert spec.missed_fire_policy == "fire_once_catchup"


def test_cron_invalid_field_fails(tmp_path: Path) -> None:
    _write_yaml(
        tmp_path,
        """
cron:
  - id: cron.bad
    graph_id: g
    expr: "* * * * *"
    tz: UTC
    bogus_field: yes
""",
    )
    with pytest.raises(HarborRuntimeError, match=r"cron\[cron\.bad\] invalid"):
        load_triggers(tmp_path)


def test_webhook_translation_with_env_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WHK_CURRENT", "current-secret-value")
    monkeypatch.setenv("WHK_PREV", "prev-secret-value")
    _write_yaml(
        tmp_path,
        """
webhook:
  - id: webhook.feed
    graph_id: graph:sdw-main
    path: /triggers/feed
    current_secret_env: WHK_CURRENT
    previous_secret_env: WHK_PREV
    timestamp_window_seconds: 300
    nonce_lru_size: 10000
    params_extractor: json
    description: NVD feed
""",
    )
    out = load_triggers(tmp_path)
    assert len(out.webhook_specs) == 1
    spec = out.webhook_specs[0]
    assert spec.trigger_id == "webhook.feed"
    assert spec.path == "/triggers/feed"
    assert spec.current_secret == b"current-secret-value"
    assert spec.previous_secret == b"prev-secret-value"
    assert spec.graph_id == "graph:sdw-main"
    assert spec.timestamp_window_seconds == 300
    assert spec.nonce_lru_size == 10000
    # params_extractor: "json" sentinel is dropped (not callable)
    assert spec.params_extractor is None


def test_webhook_missing_required_secret_env(tmp_path: Path) -> None:
    _write_yaml(
        tmp_path,
        """
webhook:
  - id: webhook.x
    graph_id: g
    path: /x
    current_secret_env: WHK_DOES_NOT_EXIST_XYZ
""",
    )
    with pytest.raises(HarborRuntimeError, match="WHK_DOES_NOT_EXIST_XYZ"):
        load_triggers(tmp_path)


def test_webhook_no_previous_secret_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHK_CUR", "secret")
    _write_yaml(
        tmp_path,
        """
webhook:
  - id: webhook.x
    graph_id: g
    path: /x
    current_secret_env: WHK_CUR
""",
    )
    out = load_triggers(tmp_path)
    assert out.webhook_specs[0].current_secret == b"secret"
    assert out.webhook_specs[0].previous_secret is None


def test_webhook_missing_current_secret_env_ref_fails(tmp_path: Path) -> None:
    _write_yaml(
        tmp_path,
        """
webhook:
  - id: webhook.x
    graph_id: g
    path: /x
""",
    )
    with pytest.raises(HarborRuntimeError, match="missing current_secret_env"):
        load_triggers(tmp_path)


def test_top_level_must_be_mapping(tmp_path: Path) -> None:
    _write_yaml(tmp_path, "- not_a_mapping\n- another\n")
    with pytest.raises(HarborRuntimeError, match="top-level must be a mapping"):
        load_triggers(tmp_path)


def test_malformed_yaml_fails(tmp_path: Path) -> None:
    _write_yaml(tmp_path, "version: [unterminated\n")
    with pytest.raises(HarborRuntimeError, match="parse error"):
        load_triggers(tmp_path)


def test_non_mapping_row_fails(tmp_path: Path) -> None:
    _write_yaml(tmp_path, "cron:\n  - just_a_string\n")
    with pytest.raises(HarborRuntimeError, match="cron entry not a mapping"):
        load_triggers(tmp_path)


def test_version_field_propagated(tmp_path: Path) -> None:
    _write_yaml(tmp_path, 'version: "1.0"\n')
    assert load_triggers(tmp_path).version == "1.0"


def test_string_path_accepted(tmp_path: Path) -> None:
    _write_yaml(
        tmp_path,
        """
manual:
  - id: m.x
    graph_id: g
""",
    )
    out = load_triggers(str(tmp_path))
    assert len(out.manual_descriptors) == 1
