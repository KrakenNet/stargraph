# SPDX-License-Identifier: Apache-2.0
"""Hand-authored, gate-verified seed nodes — the trainset cold start.

Each entry is a verified ``(brief → node + test)`` pair covering a common node
shape (classify / rename / aggregate / enrich / threshold / normalize / extract
/ bucket). They give RAG retrieval and few-shot compile something to stand on
before the generator has produced anything. ``id`` is a fixed literal so
``seed_trainset`` is idempotent across runs.

``tests/integration/nodesmith/test_seeds.py`` runs every pair through
``gate.verify_sources`` — if a seed stops passing, that test fails.
"""

from __future__ import annotations

from typing import Any


def _pair(
    seed_id: str,
    brief: str,
    class_name: str,
    reads: list[str],
    writes: list[str],
    fixture: dict[str, Any],
    node_source: str,
    test_source: str,
) -> dict[str, Any]:
    return {
        "id": seed_id,
        "brief": brief,
        "class_name": class_name,
        "node_name": class_name,
        "reads": reads,
        "writes": writes,
        "fixture": fixture,
        "node_source": node_source,
        "test_source": test_source,
        "attempts": 1,
        "passed": True,
        "verdict": "accept",
    }


SEEDS: list[dict[str, Any]] = [
    _pair(
        "5eed00000001",
        "classify a raw severity score (0-10) into a high/medium/low band",
        "SeverityBand",
        ["severity_raw"],
        ["severity_band"],
        {"severity_raw": 8.0},
        """\
from stargraph.nodes.base import NodeBase


class SeverityBand(NodeBase):
    async def execute(self, state, ctx):
        raw = float(getattr(state, "severity_raw", 0.0) or 0.0)
        band = "high" if raw >= 7 else "medium" if raw >= 4 else "low"
        return {"severity_band": band}
""",
        """\
import asyncio

from node import SeverityBand


class _S:
    severity_raw = 8.0


class _Ctx:
    run_id = "t"


def test_severity_band():
    assert asyncio.run(SeverityBand().execute(_S(), _Ctx())) == {"severity_band": "high"}
""",
    ),
    _pair(
        "5eed00000002",
        "copy the raw source host field into a normalized hostname field",
        "HostnameCopy",
        ["src_host"],
        ["hostname"],
        {"src_host": "WEB01"},
        """\
from stargraph.nodes.base import NodeBase


class HostnameCopy(NodeBase):
    async def execute(self, state, ctx):
        return {"hostname": str(getattr(state, "src_host", "") or "")}
""",
        """\
import asyncio

from node import HostnameCopy


class _S:
    src_host = "WEB01"


class _Ctx:
    run_id = "t"


def test_hostname_copy():
    assert asyncio.run(HostnameCopy().execute(_S(), _Ctx())) == {"hostname": "WEB01"}
""",
    ),
    _pair(
        "5eed00000003",
        "count how many events are in the events list",
        "EventCount",
        ["events"],
        ["event_count"],
        {"events": [1, 2, 3]},
        """\
from stargraph.nodes.base import NodeBase


class EventCount(NodeBase):
    async def execute(self, state, ctx):
        events = getattr(state, "events", None) or []
        return {"event_count": len(events)}
""",
        """\
import asyncio

from node import EventCount


class _S:
    events = [1, 2, 3]


class _Ctx:
    run_id = "t"


def test_event_count():
    assert asyncio.run(EventCount().execute(_S(), _Ctx())) == {"event_count": 3}
""",
    ),
    _pair(
        "5eed00000004",
        "flag whether an IPv4 address is in RFC1918 private space",
        "PrivateIpFlag",
        ["ip"],
        ["ip_is_private"],
        {"ip": "10.1.2.3"},
        """\
from stargraph.nodes.base import NodeBase


class PrivateIpFlag(NodeBase):
    async def execute(self, state, ctx):
        ip = str(getattr(state, "ip", "") or "")
        private = (
            ip.startswith("10.")
            or ip.startswith("192.168.")
            or any(ip.startswith(f"172.{n}.") for n in range(16, 32))
        )
        return {"ip_is_private": private}
""",
        """\
import asyncio

from node import PrivateIpFlag


class _S:
    ip = "10.1.2.3"


class _Ctx:
    run_id = "t"


def test_private_ip_flag():
    assert asyncio.run(PrivateIpFlag().execute(_S(), _Ctx())) == {"ip_is_private": True}
""",
    ),
    _pair(
        "5eed00000005",
        "block the request when its score meets or exceeds the threshold",
        "ThresholdGate",
        ["score", "threshold"],
        ["blocked"],
        {"score": 90, "threshold": 80},
        """\
from stargraph.nodes.base import NodeBase


class ThresholdGate(NodeBase):
    async def execute(self, state, ctx):
        score = float(getattr(state, "score", 0) or 0)
        threshold = float(getattr(state, "threshold", 0) or 0)
        return {"blocked": score >= threshold}
""",
        """\
import asyncio

from node import ThresholdGate


class _S:
    score = 90
    threshold = 80


class _Ctx:
    run_id = "t"


def test_threshold_gate():
    assert asyncio.run(ThresholdGate().execute(_S(), _Ctx())) == {"blocked": True}
""",
    ),
    _pair(
        "5eed00000006",
        "normalize a raw email address to lowercase with surrounding whitespace stripped",
        "EmailNormalize",
        ["raw_email"],
        ["email"],
        {"raw_email": "  Alice@Example.COM "},
        """\
from stargraph.nodes.base import NodeBase


class EmailNormalize(NodeBase):
    async def execute(self, state, ctx):
        return {"email": str(getattr(state, "raw_email", "") or "").strip().lower()}
""",
        """\
import asyncio

from node import EmailNormalize


class _S:
    raw_email = "  Alice@Example.COM "


class _Ctx:
    run_id = "t"


def test_email_normalize():
    assert asyncio.run(EmailNormalize().execute(_S(), _Ctx())) == {"email": "alice@example.com"}
""",
    ),
    _pair(
        "5eed00000007",
        "extract the id from an alert dict, defaulting to empty string when absent",
        "AlertId",
        ["alert"],
        ["alert_id"],
        {"alert": {"id": "A-42", "sev": 5}},
        """\
from stargraph.nodes.base import NodeBase


class AlertId(NodeBase):
    async def execute(self, state, ctx):
        alert = getattr(state, "alert", None) or {}
        return {"alert_id": str(alert.get("id", ""))}
""",
        """\
import asyncio

from node import AlertId


class _S:
    alert = {"id": "A-42", "sev": 5}


class _Ctx:
    run_id = "t"


def test_alert_id():
    assert asyncio.run(AlertId().execute(_S(), _Ctx())) == {"alert_id": "A-42"}
""",
    ),
    _pair(
        "5eed00000008",
        "bucket an age in seconds into fresh (<3600) or stale",
        "Freshness",
        ["age_seconds"],
        ["freshness"],
        {"age_seconds": 120},
        """\
from stargraph.nodes.base import NodeBase


class Freshness(NodeBase):
    async def execute(self, state, ctx):
        age = float(getattr(state, "age_seconds", 0) or 0)
        return {"freshness": "fresh" if age < 3600 else "stale"}
""",
        """\
import asyncio

from node import Freshness


class _S:
    age_seconds = 120


class _Ctx:
    run_id = "t"


def test_freshness():
    assert asyncio.run(Freshness().execute(_S(), _Ctx())) == {"freshness": "fresh"}
""",
    ),
]
