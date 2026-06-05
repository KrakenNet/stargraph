# SPDX-License-Identifier: Apache-2.0
"""Integration tests for the chained audit sink + dual-read + verify CLI.

Coverage:
* chained sink round-trip -- events chain on disk, ``verify_chain`` passes,
  and ``unwrap_audit_record`` recovers each event payload;
* dual-read -- ``unwrap_audit_record`` handles all three line generations
  (bare event, signed envelope, chained);
* ``is_chained_log`` shape detection;
* ``_build_audit_sink`` -- chain-write default, legacy fallback for
  existing unchained logs (with warning), resume for chained logs;
* ``harbor verify-audit`` CLI -- exit 0 valid / 1 user error / 2 broken.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import anyio
import orjson
from fathom.chained_log import load_or_create_key, verify_chain
from typer.testing import CliRunner

from harbor.audit.jsonl import (
    ChainedJSONLAuditSink,
    JSONLAuditSink,
    is_chained_log,
    unwrap_audit_record,
)
from harbor.cli import app
from harbor.cli.run import _build_audit_sink  # pyright: ignore[reportPrivateUsage]
from harbor.runtime.events import TokenEvent

if TYPE_CHECKING:
    from pathlib import Path


def _make_token_event(idx: int) -> TokenEvent:
    """Build a deterministic TokenEvent for fixture use."""
    return TokenEvent(
        run_id="run-1",
        step=idx,
        ts=datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC),
        model="gpt-4",
        token=f"tok-{idx}",
        index=idx,
    )


def _write_chained(log: Path, n: int) -> None:
    """Write *n* events through a ChainedJSONLAuditSink (key beside log)."""
    service = load_or_create_key(log.with_name(log.name + ".key"))

    async def _run() -> None:
        sink = ChainedJSONLAuditSink(log, service)
        try:
            for i in range(n):
                await sink.write(_make_token_event(i))
        finally:
            await sink.close()

    anyio.run(_run)


class TestChainedSink:
    def test_round_trip_chains_and_verifies(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        _write_chained(log, 3)

        result = verify_chain(log, log.with_name(log.name + ".pub.pem"))
        assert result.ok, result.error
        assert result.count == 3

        lines = [orjson.loads(line) for line in log.read_bytes().splitlines()]
        assert lines[0]["prev_sha256"] is None
        assert all(line["prev_sha256"] for line in lines[1:])
        # Line 1 is the auto-written fathom.genesis record; events follow.
        assert unwrap_audit_record(lines[0])["type"] == "fathom.genesis"
        for i, line in enumerate(lines[1:]):
            inner = unwrap_audit_record(line)
            assert inner["type"] == "token"
            assert inner["token"] == f"tok-{i}"

    def test_reopen_resumes_chain(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        _write_chained(log, 2)
        _write_chained(log, 2)

        result = verify_chain(log, log.with_name(log.name + ".pub.pem"))
        assert result.ok, result.error
        assert result.count == 4


class TestDualRead:
    def test_unwrap_all_three_generations(self) -> None:
        bare = {"type": "token", "run_id": "r"}
        signed = {"event": bare, "sig": "ab" * 32}
        chained = {"record": bare, "jws": "x.y.z", "prev_sha256": None, "seq": 0, "iat": 1}
        assert unwrap_audit_record(bare) is bare
        assert unwrap_audit_record(signed) is bare
        assert unwrap_audit_record(chained) is bare

    def test_is_chained_log_detection(self, tmp_path: Path) -> None:
        chained = tmp_path / "chained.jsonl"
        _write_chained(chained, 1)
        assert is_chained_log(chained)

        bare = tmp_path / "bare.jsonl"
        bare.write_bytes(orjson.dumps({"type": "token"}) + b"\n")
        assert not is_chained_log(bare)

        assert not is_chained_log(tmp_path / "missing.jsonl")
        empty = tmp_path / "empty.jsonl"
        empty.touch()
        assert not is_chained_log(empty)


class TestBuildAuditSink:
    def test_new_log_gets_chained_sink(self, tmp_path: Path) -> None:
        sink = _build_audit_sink(tmp_path / "audit.jsonl")
        assert isinstance(sink, ChainedJSONLAuditSink)
        anyio.run(sink.close)
        assert (tmp_path / "audit.jsonl.key").exists()
        assert (tmp_path / "audit.jsonl.pub.pem").exists()

    def test_existing_chained_log_resumes_chained(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        _write_chained(log, 1)
        sink = _build_audit_sink(log)
        assert isinstance(sink, ChainedJSONLAuditSink)
        anyio.run(sink.close)

    def test_existing_unchained_log_falls_back_to_legacy(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        log.write_bytes(orjson.dumps({"type": "token", "run_id": "r"}) + b"\n")
        sink = _build_audit_sink(log)
        assert isinstance(sink, JSONLAuditSink)
        anyio.run(sink.close)


class TestVerifyAuditCli:
    def test_valid_chain_exits_zero(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        _write_chained(log, 2)
        result = CliRunner().invoke(app, ["verify-audit", str(log)])
        assert result.exit_code == 0, result.output
        assert "chain valid" in result.output
        assert "2 records" in result.output

    def test_json_output(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        _write_chained(log, 1)
        result = CliRunner().invoke(app, ["verify-audit", str(log), "--json"])
        assert result.exit_code == 0, result.output
        payload = orjson.loads(result.output)
        assert payload["ok"] is True
        assert payload["count"] == 1

    def test_tampered_log_exits_two(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        _write_chained(log, 3)
        lines = log.read_bytes().splitlines()
        del lines[1]  # deletion breaks prev_sha256 linkage
        log.write_bytes(b"\n".join(lines) + b"\n")
        result = CliRunner().invoke(app, ["verify-audit", str(log)])
        assert result.exit_code == 2

    def test_truncated_tail_detected_with_expected_head(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        _write_chained(log, 3)
        # Mirror the head hash out-of-band, then truncate the last line.
        head = verify_chain(log, log.with_name(log.name + ".pub.pem")).head_sha256
        assert head is not None
        lines = log.read_bytes().splitlines()
        log.write_bytes(b"\n".join(lines[:-1]) + b"\n")

        plain = CliRunner().invoke(app, ["verify-audit", str(log)])
        assert plain.exit_code == 0  # truncation invisible without anchor

        anchored = CliRunner().invoke(app, ["verify-audit", str(log), "--expected-head", head])
        assert anchored.exit_code == 2

    def test_missing_log_exits_one(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(app, ["verify-audit", str(tmp_path / "nope.jsonl")])
        assert result.exit_code == 1

    def test_missing_pubkey_exits_one(self, tmp_path: Path) -> None:
        log = tmp_path / "audit.jsonl"
        _write_chained(log, 1)
        log.with_name(log.name + ".pub.pem").unlink()
        result = CliRunner().invoke(app, ["verify-audit", str(log)])
        assert result.exit_code == 1
