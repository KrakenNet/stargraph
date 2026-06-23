# SPDX-License-Identifier: Apache-2.0
"""Hand-authored, gate-verified seed tools — the trainset cold start.

Each entry is a verified ``(brief → tool + test)`` pair covering a common tool
shape (summarize / classify / clamp / extract / count / split) and a spread of
output types (object / string / number / integer / array) so the derived output
schema is exercised. They give RAG retrieval and few-shot compile something to
stand on before the generator has produced anything. ``id`` is a fixed literal
so ``seed_trainset`` is idempotent across runs.

``tests/integration/toolsmith/test_seeds.py`` runs every pair through
``gate.verify_sources`` — if a seed stops passing, that test fails.
"""

from __future__ import annotations

from typing import Any


def _pair(
    seed_id: str,
    brief: str,
    tool_name: str,
    namespace: str,
    fixture: dict[str, Any],
    tool_source: str,
    test_source: str,
) -> dict[str, Any]:
    return {
        "id": seed_id,
        "brief": brief,
        "tool_name": tool_name,
        "namespace": namespace,
        "fixture": fixture,
        "tool_source": tool_source,
        "test_source": test_source,
        "attempts": 1,
        "passed": True,
        "verdict": "accept",
    }


SEEDS: list[dict[str, Any]] = [
    _pair(
        "70010000001",
        "summarize an IPv4 CIDR block: network address, broadcast, and address count",
        "cidr_summary",
        "netutils",
        {"cidr": "10.0.0.0/24"},
        """\
import ipaddress
from typing import Any

from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects


@tool(
    name="cidr_summary",
    namespace="netutils",
    version="0.1.0",
    side_effects=SideEffects.none,
    description="Summarize an IPv4 CIDR block: network, broadcast, address count.",
)
def cidr_summary(*, cidr: str) -> dict[str, Any]:
    net = ipaddress.ip_network(cidr, strict=False)
    return {
        "network": str(net.network_address),
        "broadcast": str(net.broadcast_address),
        "num_addresses": int(net.num_addresses),
    }
""",
        """\
from tool import cidr_summary


def test_cidr_summary():
    out = cidr_summary(cidr="10.0.0.0/24")
    assert out["network"] == "10.0.0.0"
    assert out["broadcast"] == "10.0.0.255"
    assert out["num_addresses"] == 256
""",
    ),
    _pair(
        "70010000002",
        "bucket a 0-10 severity score into a high/medium/low band string",
        "severity_band",
        "triage",
        {"score": 8.0},
        """\
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects


@tool(
    name="severity_band",
    namespace="triage",
    version="0.1.0",
    side_effects=SideEffects.none,
    description="Bucket a 0-10 severity score into high/medium/low.",
)
def severity_band(*, score: float) -> str:
    return "high" if score >= 7 else "medium" if score >= 4 else "low"
""",
        """\
from tool import severity_band


def test_severity_band():
    assert severity_band(score=8.0) == "high"
    assert severity_band(score=5.0) == "medium"
    assert severity_band(score=1.0) == "low"
""",
    ),
    _pair(
        "70010000003",
        "clamp a numeric value into an inclusive [low, high] range",
        "clamp",
        "mathutils",
        {"value": 12.0, "low": 0.0, "high": 10.0},
        """\
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects


@tool(
    name="clamp",
    namespace="mathutils",
    version="0.1.0",
    side_effects=SideEffects.none,
    description="Clamp a value into the inclusive [low, high] range.",
)
def clamp(*, value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
""",
        """\
from tool import clamp


def test_clamp():
    assert clamp(value=12.0, low=0.0, high=10.0) == 10.0
    assert clamp(value=-3.0, low=0.0, high=10.0) == 0.0
    assert clamp(value=5.0, low=0.0, high=10.0) == 5.0
""",
    ),
    _pair(
        "70010000004",
        "extract the lowercased domain from an email address",
        "domain_of",
        "netutils",
        {"email": "  Alice@Example.COM "},
        """\
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects


@tool(
    name="domain_of",
    namespace="netutils",
    version="0.1.0",
    side_effects=SideEffects.none,
    description="Extract the lowercased domain from an email address.",
)
def domain_of(*, email: str) -> str:
    return email.strip().rsplit("@", 1)[-1].lower()
""",
        """\
from tool import domain_of


def test_domain_of():
    assert domain_of(email="  Alice@Example.COM ") == "example.com"
""",
    ),
    _pair(
        "70010000005",
        "count whitespace-separated words in a string",
        "word_count",
        "textutils",
        {"text": "the quick brown fox"},
        """\
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects


@tool(
    name="word_count",
    namespace="textutils",
    version="0.1.0",
    side_effects=SideEffects.none,
    description="Count whitespace-separated words in a string.",
)
def word_count(*, text: str) -> int:
    return len(text.split())
""",
        """\
from tool import word_count


def test_word_count():
    assert word_count(text="the quick brown fox") == 4
    assert word_count(text="   ") == 0
""",
    ),
    _pair(
        "70010000006",
        "split a comma-separated tag string into a clean lowercased list",
        "parse_tags",
        "textutils",
        {"raw": "Prod, web ,, DB"},
        """\
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects


@tool(
    name="parse_tags",
    namespace="textutils",
    version="0.1.0",
    side_effects=SideEffects.none,
    description="Split a comma-separated tag string into a clean lowercased list.",
)
def parse_tags(*, raw: str) -> list[str]:
    return [t.strip().lower() for t in raw.split(",") if t.strip()]
""",
        """\
from tool import parse_tags


def test_parse_tags():
    assert parse_tags(raw="Prod, web ,, DB") == ["prod", "web", "db"]
""",
    ),
]
