# SPDX-License-Identifier: Apache-2.0
"""``stargraph pack pin|revert`` -- rule-pack version pinning (W2 ops surface).

Rule packs have no runtime version store: a graph references packs via
its IR ``governance:`` :class:`~stargraph.ir.PackMount` entries, whose
optional ``version`` field is the *only* durable pin. These commands are
therefore metadata operations over an existing store -- the graph IR
YAML file itself. No new storage is introduced.

* ``stargraph pack pin <pack> <version> --graph g.yaml`` sets the
  mount's ``version`` (an explicit pin; enforced at pack-load time via
  ``check_pack_compat`` / the pack loader).
* ``stargraph pack revert <pack> --graph g.yaml`` removes the pin,
  restoring unpinned resolution (whatever version the plugin registry
  provides). Refuses when the mount is not pinned.

Both refuse when the pack id is not mounted in the graph, re-validate
the mutated document as IR before writing, preserve the file's leading
comment header (SPDX habit), and emit a ``pack_pinned`` /
``pack_unpinned`` :class:`~stargraph.runtime.events.BosunAuditEvent`
(default log: ``<graph>.audit.jsonl`` beside the graph file).

Known limitation (documented, not hidden): the YAML is rewritten via
``yaml.safe_dump``, so inline comments below the header block and
custom formatting are not preserved.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003 -- runtime use by typer.Annotated
from typing import Annotated, Any, cast

import typer
import yaml

from stargraph.ir import IRDocument

__all__ = ["pack_app"]

pack_app = typer.Typer(no_args_is_help=True, help="Rule-pack pin operations.")


def _load_graph(graph: Path) -> tuple[dict[str, Any], str]:
    """Return ``(raw_doc, comment_header)`` for the graph YAML or exit 1."""
    if not graph.exists():
        typer.echo(f"pack: graph not found: {graph}", err=True)
        raise typer.Exit(code=1)
    text = graph.read_text()
    raw_any: Any = yaml.safe_load(text)
    if not isinstance(raw_any, dict):
        typer.echo(f"pack: {graph} is not a YAML mapping", err=True)
        raise typer.Exit(code=1)
    header_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            header_lines.append(line)
        else:
            break
    header = "\n".join(header_lines) + "\n" if header_lines else ""
    return cast("dict[str, Any]", raw_any), header


def _find_mount(raw: dict[str, Any], pack: str, graph: Path) -> dict[str, Any]:
    """Return the ``governance:`` mount entry for ``pack`` or exit 1."""
    governance_any: Any = raw.get("governance", [])
    mounts: list[Any] = (
        cast("list[Any]", governance_any) if isinstance(governance_any, list) else []
    )
    for entry_any in mounts:
        if isinstance(entry_any, dict):
            entry = cast("dict[str, Any]", entry_any)
            if entry.get("id") == pack:
                return entry
    mounted = [cast("dict[str, Any]", e).get("id") for e in mounts if isinstance(e, dict)]
    typer.echo(
        f"pack: {pack!r} is not mounted in {graph} (governance mounts: {mounted or 'none'})",
        err=True,
    )
    raise typer.Exit(code=1)


def _write_graph(graph: Path, raw: dict[str, Any], header: str) -> None:
    """Validate the mutated doc as IR, then write it back (header preserved)."""
    try:
        IRDocument.model_validate(raw)
    except Exception as exc:
        typer.echo(f"pack: mutated document is no longer valid IR: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    graph.write_text(header + yaml.safe_dump(raw, sort_keys=False))


def _emit_pack_audit(
    graph: Path,
    audit_log: Path | None,
    *,
    kind: str,
    pack: str,
    from_version: str | None,
    to_version: str | None,
) -> None:
    """Append a ``pack_pinned``/``pack_unpinned`` BosunAuditEvent."""
    from stargraph.cli.run import _build_audit_sink  # pyright: ignore[reportPrivateUsage]
    from stargraph.runtime.events import BosunAuditEvent

    log_path = audit_log if audit_log is not None else graph.with_name(graph.name + ".audit.jsonl")
    now = datetime.now(UTC)
    provenance: dict[str, Any] = {
        "origin": "system",
        "source": "stargraph.cli.pack",
        "run_id": "",
        "step": 0,
        "confidence": 1.0,
        "timestamp": now.isoformat(),
    }
    event = BosunAuditEvent(
        run_id="",
        step=0,
        ts=now,
        pack_id="stargraph.cli",
        pack_version="1.0",
        fact={
            "kind": kind,
            "pack": pack,
            "graph": str(graph),
            "from_version": from_version or "",
            "to_version": to_version or "",
        },
        provenance=provenance,
    )

    async def _write() -> None:
        sink = _build_audit_sink(log_path)
        try:
            await sink.write(event)
        finally:
            await sink.close()

    asyncio.run(_write())


@pack_app.command("pin")
def pin(
    pack: Annotated[str, typer.Argument(help="Pack id (e.g. sdw.routing).")],
    version: Annotated[str, typer.Argument(help="Version to pin the mount to.")],
    graph: Annotated[
        Path,
        typer.Option("--graph", help="Graph IR YAML whose governance mount to pin."),
    ],
    audit_log: Annotated[
        Path | None,
        typer.Option(
            "--audit-log",
            help="JSONL audit log for the pin event (default: <graph>.audit.jsonl).",
        ),
    ] = None,
) -> None:
    """Pin a mounted rule pack to an explicit version in the graph IR."""
    raw, header = _load_graph(graph)
    mount = _find_mount(raw, pack, graph)
    old_any: Any = mount.get("version")
    old = str(old_any) if old_any is not None else None
    mount["version"] = version
    _write_graph(graph, raw, header)
    _emit_pack_audit(
        graph,
        audit_log,
        kind="pack_pinned",
        pack=pack,
        from_version=old,
        to_version=version,
    )
    typer.echo(f"pinned pack {pack!r} in {graph}: {old or '(unpinned)'} -> {version}")


@pack_app.command("revert")
def revert(
    pack: Annotated[str, typer.Argument(help="Pack id (e.g. sdw.routing).")],
    graph: Annotated[
        Path,
        typer.Option("--graph", help="Graph IR YAML whose governance mount to un-pin."),
    ],
    audit_log: Annotated[
        Path | None,
        typer.Option(
            "--audit-log",
            help="JSONL audit log for the revert event (default: <graph>.audit.jsonl).",
        ),
    ] = None,
) -> None:
    """Remove a pack's version pin, restoring unpinned (registry) resolution."""
    raw, header = _load_graph(graph)
    mount = _find_mount(raw, pack, graph)
    old_any: Any = mount.get("version")
    if old_any is None:
        typer.echo(
            f"pack: refusing -- {pack!r} in {graph} is not pinned (no version to revert)",
            err=True,
        )
        raise typer.Exit(code=1)
    old = str(old_any)
    del mount["version"]
    _write_graph(graph, raw, header)
    _emit_pack_audit(
        graph,
        audit_log,
        kind="pack_unpinned",
        pack=pack,
        from_version=old,
        to_version=None,
    )
    typer.echo(f"reverted pack {pack!r} in {graph}: {old} -> (unpinned)")
