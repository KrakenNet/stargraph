# SPDX-License-Identifier: Apache-2.0
"""Assemble the built artifacts into one runnable Stargraph dir.

The graph spine (a graphsmith bundle: ``graph.yaml`` + ``state.py`` + ``nodes.py``)
becomes the runnable directory; each built capability is copied beside it under
``capabilities/<name>/`` and recorded — together with the spine — in an
``assembly.yaml`` manifest (the durable record of what the build produced and
mounted). Deterministic and correct-by-construction: nothing here generates code,
it only places gate-passing artifacts.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml

from stargraph.errors import StargraphRuntimeError
from stargraph.skills._smith.nodes import snake

__all__ = ["AssemblyError", "assemble"]


class AssemblyError(StargraphRuntimeError):
    """No runnable spine was built, so there is nothing to assemble."""


def _copy_files(src_dir: Path, dest_dir: Path) -> None:
    """Copy the immediate files of ``src_dir`` into ``dest_dir`` (non-recursive)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    for f in src_dir.iterdir():
        if f.is_file():
            shutil.copy2(f, dest_dir / f.name)


def assemble(built: list[dict[str, Any]], *, output_dir: str) -> dict[str, Any]:
    """Place the spine + capabilities under ``output_dir/assembled/``.

    Returns ``{graph_path, assembled_dir, built}`` where ``built`` is annotated
    with a ``mounted`` flag per capability. Raises :class:`AssemblyError` if no
    ``graph`` artifact landed.
    """
    spine = next((b for b in built if b.get("kind") == "graph" and b.get("ok")), None)
    if spine is None:
        raise AssemblyError("no runnable graph spine was built; cannot assemble")

    assembled = Path(output_dir) / "assembled"
    # The spine's landed_path is the bundle's graph.yaml; its siblings (state.py,
    # nodes.py, test) are the runnable graph — copy them to the assembled root.
    spine_bundle = Path(str(spine["landed_path"])).parent
    _copy_files(spine_bundle, assembled)
    graph_path = assembled / Path(str(spine["landed_path"])).name

    caps_root = assembled / "capabilities"
    annotated: list[dict[str, Any]] = [spine]
    for b in built:
        if b is spine:
            continue
        if not b.get("ok"):
            annotated.append({**b, "mounted": False})
            continue
        dest = caps_root / snake(str(b["name"]))
        shutil.copytree(Path(str(b["out_dir"])), dest, dirs_exist_ok=True)
        annotated.append({**b, "mounted": True, "mount_path": str(dest)})

    _write_assembly_yaml(assembled, spine, annotated)
    return {
        "graph_path": str(graph_path),
        "assembled_dir": str(assembled),
        "built": annotated,
    }


def _write_assembly_yaml(
    assembled: Path, spine: dict[str, Any], annotated: list[dict[str, Any]]
) -> None:
    doc = {
        "spine": {"name": spine["name"], "graph": Path(str(spine["landed_path"])).name},
        "capabilities": [
            {
                "kind": b["kind"],
                "name": b["name"],
                "mounted": bool(b.get("mounted")),
                "path": b.get("mount_path", ""),
            }
            for b in annotated
            if b is not spine
        ],
    }
    (assembled / "assembly.yaml").write_text(
        "# SPDX-License-Identifier: Apache-2.0\n" + yaml.safe_dump(doc, sort_keys=False),
        encoding="utf-8",
    )
