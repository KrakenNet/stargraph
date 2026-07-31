# SPDX-License-Identifier: Apache-2.0
"""``std.file_read`` / ``file_write`` / ``file_list`` -- jail semantics + IO."""

from __future__ import annotations

from pathlib import Path

import pytest

from stargraph.errors import StargraphRuntimeError
from stargraph.tools.std.fs import file_list, file_read, file_write

pytestmark = pytest.mark.unit


@pytest.fixture
def jail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("STARGRAPH_TOOLS_FS_ROOT", str(tmp_path))
    return tmp_path


def test_write_read_roundtrip(jail: Path) -> None:
    wrote = file_write(path="notes/hello.txt", content="hi there")
    assert wrote["bytes_written"] == 8
    assert Path(wrote["path"]).is_relative_to(jail)
    read = file_read(path="notes/hello.txt")
    assert read["content"] == "hi there"
    assert read["truncated"] is False


def test_write_append_mode(jail: Path) -> None:
    file_write(path="log.txt", content="a")
    file_write(path="log.txt", content="b", append=True)
    assert file_read(path="log.txt")["content"] == "ab"


def test_read_truncation(jail: Path) -> None:
    file_write(path="big.txt", content="x" * 100)
    read = file_read(path="big.txt", max_bytes=10)
    assert read["content"] == "x" * 10
    assert read["truncated"] is True


def test_read_missing_file_is_loud(jail: Path) -> None:
    with pytest.raises(StargraphRuntimeError, match="does not exist"):
        file_read(path="nope.txt")


def test_list_entries_and_pattern(jail: Path) -> None:
    file_write(path="d/a.txt", content="1")
    file_write(path="d/b.md", content="22")
    listed = file_list(path="d", pattern="*.txt")
    assert [e["name"] for e in listed["entries"]] == ["a.txt"]
    assert listed["entries"][0]["size"] == 1
    all_entries = file_list(path="d")
    assert {e["name"] for e in all_entries["entries"]} == {"a.txt", "b.md"}


def test_list_missing_dir_is_loud(jail: Path) -> None:
    with pytest.raises(StargraphRuntimeError, match="does not exist"):
        file_list(path="missing-dir")


def test_dotdot_traversal_rejected(jail: Path) -> None:
    with pytest.raises(StargraphRuntimeError, match="escapes"):
        file_read(path="../outside.txt")


def test_absolute_path_outside_jail_rejected(jail: Path) -> None:
    with pytest.raises(StargraphRuntimeError, match="escapes"):
        file_write(path="/etc/hostname-copy", content="nope")


def test_absolute_path_inside_jail_allowed(jail: Path) -> None:
    target = jail / "abs.txt"
    file_write(path=str(target), content="ok")
    assert file_read(path=str(target))["content"] == "ok"


def test_symlink_escape_rejected(jail: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_text("secret")
    (jail / "link.txt").symlink_to(outside)
    with pytest.raises(StargraphRuntimeError, match="escapes"):
        file_read(path="link.txt")
