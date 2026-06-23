# SPDX-License-Identifier: Apache-2.0
"""Drive a manifest item's smith to a landed artifact — one generic driver.

Every smith exposes the identical lifecycle (``TriageGate`` → ``Recall`` →
``Build`` → ``RecordBuild``) over its own ``State``, so a single driver runs any
of them: map the manifest ``kind`` to the smith package, import its four no-arg
lifecycle nodes + ``State``, and thread the brief through them exactly as the
smith's own tests do. ``default_executor`` is the live implementation the
``execute`` node uses when no executor is injected; tests inject a deterministic
one that stubs the per-smith generator while still running the real gate + land.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from stargraph.nodes.base import ExecutionContext, NodeBase
    from stargraph.skills.foundry.manifest import ManifestItem

__all__ = ["SMITHS", "Executor", "default_executor"]

# manifest kind → smith package. Every package exposes
# ``nodes.{triage,recall,build,record}`` (TriageGate / Recall / Build /
# RecordBuild, all no-arg) and ``state:State``.
SMITHS: dict[str, str] = {
    "graph": "stargraph.skills.graphsmith",
    "node": "stargraph.skills.nodesmith",
    "tool": "stargraph.skills.toolsmith",
    "store": "stargraph.skills.storesmith",
    "trigger": "stargraph.skills.triggersmith",
    "adapter": "stargraph.skills.adaptersmith",
    "ml": "stargraph.skills.mlsmith",
    "pack": "stargraph.skills.packsmith",
    "skill": "stargraph.skills.skillsmith",
    "plugin": "stargraph.skills.pluginsmith",
}

_CTX = cast("ExecutionContext", SimpleNamespace(run_id="foundry-build"))


class Executor(Protocol):
    """The ``execute`` node's seam: build one manifest item, return its record."""

    async def __call__(
        self, item: ManifestItem, *, output_dir: str, model_id: str
    ) -> dict[str, Any]: ...


def _lifecycle(kind: str) -> tuple[list[NodeBase], type[Any]]:
    """Import a smith's four lifecycle nodes (constructed) + its State class."""
    pkg = SMITHS[kind]
    triage = importlib.import_module(f"{pkg}.nodes.triage").TriageGate
    recall = importlib.import_module(f"{pkg}.nodes.recall").Recall
    build = importlib.import_module(f"{pkg}.nodes.build").Build
    record = importlib.import_module(f"{pkg}.nodes.record").RecordBuild
    state_cls = importlib.import_module(f"{pkg}.state").State
    return [triage(), recall(), build(), record()], state_cls


def _record(
    item: ManifestItem, *, landed: str, fixture: dict[str, Any], out_dir: str
) -> dict[str, Any]:
    return {
        "kind": item.kind,
        "name": item.name,
        "landed_path": landed,
        "out_dir": out_dir,
        "ok": bool(landed),
        "fixture": fixture,
    }


async def default_executor(item: ManifestItem, *, output_dir: str, model_id: str) -> dict[str, Any]:
    """Drive ``item``'s smith end-to-end (real LM, real gate); return its record."""
    if item.kind not in SMITHS:
        return {
            **_record(item, landed="", fixture={}, out_dir=output_dir),
            "reason": f"no smith registered for kind {item.kind!r}",
        }
    nodes, state_cls = _lifecycle(item.kind)
    state = state_cls(brief=item.brief, model_id=model_id, output_dir=output_dir)
    for node in nodes:
        out = await node.execute(state, _CTX)
        state = state.model_copy(update=out)
    landed = str(getattr(state, "landed_path", "") or "")
    fixture = dict(getattr(state, "fixture", {}) or {})
    return _record(item, landed=landed, fixture=fixture, out_dir=output_dir)
