# SPDX-License-Identifier: Apache-2.0
"""Offline tests for the SaaS tool packs (slack/github/s3/email/postgres).

Network tools (slack/github) mock the shared ``_http.build_client`` seam;
s3 mocks ``s3._client.build_client``; postgres mocks ``postgres._conn
.connect``; email fakes ``smtplib.SMTP`` / ``imaplib.IMAP4_SSL``. Every
write tool proves the SaaS safety boundaries: dry-run with no env flag,
dedupe-key required, live path dispatches. One representative gate test
proves default-deny through the real ``execute_tool`` pipeline (the
seeding test asserts every SaaS tool carries a capability).
"""

from __future__ import annotations

from typing import Any, ClassVar, cast

import httpx
import pytest

from stargraph.errors import CapabilityError, StargraphRuntimeError
from stargraph.runtime.tool_exec import RunContext, execute_tool
from stargraph.tools import _http
from stargraph.tools.email.fetch import fetch_email
from stargraph.tools.email.send import send_email
from stargraph.tools.github.create_issue import create_issue
from stargraph.tools.github.list_issues import list_issues
from stargraph.tools.github.read_file import read_file
from stargraph.tools.postgres import _conn
from stargraph.tools.postgres.execute import pg_execute
from stargraph.tools.postgres.query import pg_query
from stargraph.tools.s3 import _client
from stargraph.tools.s3.get_object import get_object
from stargraph.tools.s3.put_object import put_object
from stargraph.tools.slack.post_message import post_message
from stargraph.tools.slack.read_messages import read_messages

pytestmark = pytest.mark.unit

_Handler = Any  # Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def _no_live_flags(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """Dry-run default must hold regardless of the developer's shell env."""
    for ns in ("SLACK", "GITHUB", "S3", "EMAIL", "POSTGRES"):
        monkeypatch.delenv(f"STARGRAPH_{ns}_LIVE", raising=False)


@pytest.fixture
def mock_http(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install a request handler behind the shared ``_http.build_client``."""

    def install(handler: _Handler) -> None:
        def _build(timeout: float = 30.0) -> httpx.AsyncClient:
            del timeout
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)

        monkeypatch.setattr(_http, "build_client", _build)

    return install


# ---------------------------------------------------------------------------
# Capability gate (representative; the seeding test covers the full matrix).
# ---------------------------------------------------------------------------


async def test_saas_reads_denied_by_default() -> None:
    ctx = RunContext(run_id="r1")  # no capabilities wired
    with pytest.raises(CapabilityError):
        await execute_tool(cast("Any", read_messages), {"channel": "C1"}, run_ctx=ctx)


# ---------------------------------------------------------------------------
# slack
# ---------------------------------------------------------------------------


async def test_slack_post_message_dry_run_by_default() -> None:
    out = await post_message(channel="C1", text="hi", dedupe_key="k-1")
    assert out["status"] == "dry-run"
    body = cast("dict[str, Any]", out["request_body"])
    assert body["metadata"]["event_payload"]["dedupe_key"] == "k-1"


async def test_slack_post_message_requires_dedupe_key() -> None:
    with pytest.raises(StargraphRuntimeError, match="dedupe_key"):
        await post_message(channel="C1", text="hi", dedupe_key="  ")


async def test_slack_post_message_live(mock_http: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STARGRAPH_SLACK_LIVE", "1")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat.postMessage"
        assert request.headers["authorization"] == "Bearer xoxb-test"
        return httpx.Response(200, json={"ok": True, "channel": "C1", "ts": "1.2"})

    mock_http(handler)
    out = await post_message(channel="C1", text="hi", dedupe_key="k-1")
    assert out["status"] == "ok"
    assert out["ts"] == "1.2"


async def test_slack_post_message_live_surfaces_api_error(
    mock_http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STARGRAPH_SLACK_LIVE", "1")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})

    mock_http(handler)
    with pytest.raises(StargraphRuntimeError, match="channel_not_found"):
        await post_message(channel="C-bad", text="hi", dedupe_key="k-1")


async def test_slack_read_messages(mock_http: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/conversations.history"
        return httpx.Response(
            200,
            json={"ok": True, "messages": [{"ts": "9.9", "user": "U1", "text": "yo"}]},
        )

    mock_http(handler)
    out = await read_messages(channel="C1")
    assert out["messages"] == [{"ts": "9.9", "user": "U1", "text": "yo"}]


async def test_slack_read_messages_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    with pytest.raises(StargraphRuntimeError, match="SLACK_BOT_TOKEN"):
        await read_messages(channel="C1")


# ---------------------------------------------------------------------------
# github
# ---------------------------------------------------------------------------


async def test_github_read_file_decodes_base64(mock_http: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/o/r/contents/README.md"
        return httpx.Response(200, json={"type": "file", "content": "aGVsbG8=\n", "sha": "abc123"})

    mock_http(handler)
    out = await read_file(repo="o/r", path="README.md")
    assert out["content"] == "hello"
    assert out["sha"] == "abc123"


async def test_github_read_file_rejects_bad_repo_shape() -> None:
    with pytest.raises(StargraphRuntimeError, match="owner/name"):
        await read_file(repo="not-a-repo", path="x")


async def test_github_list_issues_filters_prs(mock_http: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "number": 1,
                    "title": "bug",
                    "state": "open",
                    "user": {"login": "a"},
                    "html_url": "u1",
                },
                {
                    "number": 2,
                    "title": "pr",
                    "state": "open",
                    "user": {"login": "b"},
                    "html_url": "u2",
                    "pull_request": {},
                },
            ],
        )

    mock_http(handler)
    out = await list_issues(repo="o/r")
    assert [i["number"] for i in out["issues"]] == [1]


async def test_github_create_issue_dry_run_stamps_marker() -> None:
    out = await create_issue(repo="o/r", title="t", body="b", dedupe_key="k-9")
    assert out["status"] == "dry-run"
    body = cast("dict[str, Any]", out["request_body"])
    assert "stargraph-dedupe:k-9" in body["body"]


async def test_github_create_issue_live_returns_existing_on_dedupe_hit(
    mock_http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STARGRAPH_GITHUB_LIVE", "1")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-test")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search/issues"  # never reaches POST
        return httpx.Response(200, json={"items": [{"number": 7, "html_url": "u7"}]})

    mock_http(handler)
    out = await create_issue(repo="o/r", title="t", body="b", dedupe_key="k-9")
    assert out == {"status": "exists", "number": 7, "url": "u7"}


async def test_github_create_issue_live_creates_when_no_hit(
    mock_http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STARGRAPH_GITHUB_LIVE", "1")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-test")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search/issues":
            return httpx.Response(200, json={"items": []})
        assert request.method == "POST"
        assert request.url.path == "/repos/o/r/issues"
        return httpx.Response(201, json={"number": 8, "html_url": "u8"})

    mock_http(handler)
    out = await create_issue(repo="o/r", title="t", body="b", dedupe_key="k-9")
    assert out["status"] == "ok"
    assert out["number"] == 8


async def test_github_create_issue_live_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STARGRAPH_GITHUB_LIVE", "1")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(StargraphRuntimeError, match="GITHUB_TOKEN"):
        await create_issue(repo="o/r", title="t", body="b", dedupe_key="k")


# ---------------------------------------------------------------------------
# s3
# ---------------------------------------------------------------------------


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, n: int) -> bytes:
        return self._data[:n]


class _FakeS3:
    def __init__(self) -> None:
        self.puts: list[dict[str, Any]] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        return {"Body": _FakeBody(b"object-content"), "ContentType": "text/plain"}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.puts.append(kwargs)
        return {}


async def test_s3_get_object(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeS3()
    monkeypatch.setattr(_client, "build_client", lambda: fake)
    out = await get_object(bucket="b", key="k")
    assert out["content"] == "object-content"
    assert out["truncated"] is False


async def test_s3_put_object_dry_run_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeS3()
    monkeypatch.setattr(_client, "build_client", lambda: fake)
    out = await put_object(bucket="b", key="k", content="data")
    assert out["status"] == "dry-run"
    assert fake.puts == []  # no client call in dry-run


async def test_s3_put_object_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STARGRAPH_S3_LIVE", "1")
    fake = _FakeS3()
    monkeypatch.setattr(_client, "build_client", lambda: fake)
    out = await put_object(bucket="b", key="k", content="data")
    assert out["status"] == "ok"
    assert fake.puts[0]["Bucket"] == "b"
    assert fake.puts[0]["Body"] == b"data"


# ---------------------------------------------------------------------------
# email
# ---------------------------------------------------------------------------


class _FakeSMTP:
    sent: ClassVar[list[Any]] = []

    def __init__(self, host: str, port: int, timeout: int = 30) -> None:
        self.host, self.port = host, port

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self) -> None:
        return None

    def login(self, user: str, password: str) -> None:
        return None

    def send_message(self, msg: Any) -> None:
        _FakeSMTP.sent.append(msg)


async def test_email_send_dry_run_by_default() -> None:
    out = await send_email(to="a@example.com", subject="s", body="b", dedupe_key="k-1")
    assert out["status"] == "dry-run"
    body = cast("dict[str, Any]", out["request_body"])
    assert body["message_id"].startswith("<stargraph-")


async def test_email_send_message_id_is_deterministic() -> None:
    one = await send_email(to="a@example.com", subject="s", body="b", dedupe_key="same")
    two = await send_email(to="a@example.com", subject="s", body="b", dedupe_key="same")
    body_one = cast("dict[str, Any]", one["request_body"])
    body_two = cast("dict[str, Any]", two["request_body"])
    assert body_one["message_id"] == body_two["message_id"]


async def test_email_send_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STARGRAPH_EMAIL_LIVE", "1")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USERNAME", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    _FakeSMTP.sent.clear()
    out = await send_email(to="a@example.com", subject="s", body="hello", dedupe_key="k-1")
    assert out["status"] == "ok"
    (msg,) = _FakeSMTP.sent
    assert msg["To"] == "a@example.com"
    assert msg["Message-ID"] == out["message_id"]


async def test_email_fetch_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IMAP_HOST", raising=False)
    with pytest.raises(StargraphRuntimeError, match="IMAP_HOST"):
        await fetch_email()


_RAW_EMAIL = (
    b"From: Ada <ada@example.com>\r\n"
    b"To: bot@example.com\r\n"
    b"Subject: =?utf-8?q?R=C3=A9sum=C3=A9_report?=\r\n"
    b"Date: Thu, 30 Jul 2026 10:00:00 +0000\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"The espresso machine is deterministic.\r\n"
)


class _FakeIMAP:
    def __init__(self, host: str, port: int) -> None:
        self.selected_readonly: bool | None = None

    def __enter__(self) -> _FakeIMAP:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        return ("OK", [])

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.selected_readonly = readonly
        return ("OK", [b"1"])

    def search(self, charset: Any, criteria: str) -> tuple[str, list[bytes]]:
        return ("OK", [b"1"])

    def fetch(self, msg_id: bytes, spec: str) -> tuple[str, list[Any]]:
        assert "PEEK" in spec  # reads must not mutate seen flags
        return ("OK", [(b"1 (BODY[] {%d})" % len(_RAW_EMAIL), _RAW_EMAIL)])


async def test_email_fetch_parses_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("IMAP_USERNAME", "bot@example.com")
    monkeypatch.setenv("IMAP_PASSWORD", "pw")
    monkeypatch.setattr("imaplib.IMAP4_SSL", _FakeIMAP)
    out = await fetch_email(limit=1)
    (msg,) = out["messages"]
    assert msg["subject"] == "Résumé report"
    assert msg["from"] == "Ada <ada@example.com>"
    assert "deterministic" in msg["snippet"]


# ---------------------------------------------------------------------------
# postgres
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]], columns: list[str]) -> None:
        self._rows = rows
        self.description = [(name,) for name in columns]
        self.rowcount = len(rows)

    def fetchmany(self, n: int) -> list[tuple[Any, ...]]:
        return self._rows[:n]


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self.cursor = cursor
        self.executed: list[tuple[str, list[Any]]] = []
        self.committed = False

    def __enter__(self) -> _FakeConn:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, query: str, params: list[Any]) -> _FakeCursor:
        self.executed.append((query, params))
        return self.cursor

    def commit(self) -> None:
        self.committed = True


async def test_postgres_query(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConn(_FakeCursor([(1, "ada")], ["id", "name"]))
    seen: dict[str, Any] = {}

    def fake_connect(*, read_only: bool, application_name: str) -> _FakeConn:
        seen["read_only"] = read_only
        return conn

    monkeypatch.setattr(_conn, "connect", fake_connect)
    out = await pg_query(query="select id, name from t")
    assert out["columns"] == ["id", "name"]
    assert out["rows"] == [[1, "ada"]]
    assert seen["read_only"] is True  # server-enforced read-only session


async def test_postgres_execute_dry_run_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_connect(**_: Any) -> Any:
        raise AssertionError("dry-run must not open a connection")

    monkeypatch.setattr(_conn, "connect", fail_connect)
    out = await pg_execute(statement="delete from t", dedupe_key="k-1")
    assert out["status"] == "dry-run"


async def test_postgres_execute_live_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STARGRAPH_POSTGRES_LIVE", "1")
    conn = _FakeConn(_FakeCursor([], []))
    conn.cursor.rowcount = 3

    def fake_connect(*, read_only: bool, application_name: str) -> _FakeConn:
        assert read_only is False
        assert application_name == "stargraph:k-1"
        return conn

    monkeypatch.setattr(_conn, "connect", fake_connect)
    out = await pg_execute(statement="update t set x=1", dedupe_key="k-1")
    assert out == {
        "status": "ok",
        "rowcount": 3,
        "__stargraph_provenance__": {
            "origin": "tool",
            "source": "postgres",
            "external_id": "k-1",
        },
    }
    assert conn.committed is True


async def test_postgres_query_requires_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STARGRAPH_POSTGRES_DSN", raising=False)
    with pytest.raises(StargraphRuntimeError, match="STARGRAPH_POSTGRES_DSN"):
        await pg_query(query="select 1")
