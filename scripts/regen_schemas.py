# SPDX-License-Identifier: Apache-2.0
"""Regenerate JSON Schemas (Draft 2020-12) from Pydantic IR models.

Emits three schema files under ``src/stargraph/schemas/``:

* ``ir-v1.json``        -- :class:`stargraph.ir.IRDocument`
* ``tool-v1.json``      -- :class:`stargraph.ir.ToolSpec`
* ``manifest-v1.json``  -- :class:`stargraph.ir.PluginManifest`

Each schema is generated with ``mode='validation'`` (per
``research-pydantic-ir.md`` -- validation- and serialization-mode schemas
diverge; we pin one). The Pydantic-emitted ``$schema`` and ``$id`` are
overridden to canonical values:

* ``$schema`` -- ``https://json-schema.org/draft/2020-12/schema``
* ``$id``     -- ``https://stargraph.dev/schemas/{ir,tool,manifest}-v1.json``

Run with ``uv run python scripts/regen_schemas.py``. The output is
deterministic (Pydantic preserves declaration-order keys, ``json.dumps``
with ``sort_keys=False`` keeps them) so re-running on an unchanged tree
produces a byte-identical diff (FR-26, FR-27, AC-10.1, AC-10.2, AC-10.3).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stargraph.ir import IRDocument, PluginManifest, ToolSpec

if TYPE_CHECKING:
    from pydantic import BaseModel

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "src" / "stargraph" / "schemas"
JSON_SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"
ID_BASE = "https://stargraph.dev/schemas"


def _build_schema(model: type[BaseModel], schema_id: str) -> dict[str, Any]:
    schema = model.model_json_schema(mode="validation")
    pinned: dict[str, Any] = {"$schema": JSON_SCHEMA_DRAFT, "$id": schema_id}
    pinned.update(schema)
    pinned["$schema"] = JSON_SCHEMA_DRAFT
    pinned["$id"] = schema_id
    return pinned


def _write_schema(model: type[BaseModel], filename: str) -> Path:
    schema_id = f"{ID_BASE}/{filename}"
    schema = _build_schema(model, schema_id)
    out_path = SCHEMAS_DIR / filename
    out_path.write_text(json.dumps(schema, indent=2, sort_keys=False) + "\n")
    return out_path


def main() -> None:
    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    for model, filename in (
        (IRDocument, "ir-v1.json"),
        (ToolSpec, "tool-v1.json"),
        (PluginManifest, "manifest-v1.json"),
    ):
        path = _write_schema(model, filename)
        print(f"wrote {path.relative_to(SCHEMAS_DIR.parents[2])}")


if __name__ == "__main__":
    main()
