# SPDX-License-Identifier: Apache-2.0
"""Build a Draft-7 mirror of ``ir-v1.json`` for legacy validators.

Reads :file:`src/stargraph/schemas/ir-v1.json` (Draft 2020-12, emitted by
:mod:`scripts.regen_schemas`) and applies the minimum transforms needed
for JSON Schema Draft-7 compatibility:

* rename ``$defs`` -> ``definitions``
* drop ``prefixItems`` (Draft-7 has no equivalent)
* drop ``unevaluatedProperties`` (introduced in 2019-09)
* downgrade ``$dynamicRef`` -> ``$ref`` (Pydantic v2 emits dynamicRef
  for forward refs; Draft-7 only has ``$ref``)
* rewrite ``$ref`` / ``$dynamicRef`` pointers from ``#/$defs/...`` to
  ``#/definitions/...`` to match the renamed key
* set top-level ``$schema`` to ``http://json-schema.org/draft-07/schema#``
* mirror ``$id`` to ``https://stargraph.dev/schemas/ir-v1-draft7.json``

Output is written to :file:`src/stargraph/schemas/ir-v1-draft7.json`. Run
with ``uv run python scripts/build_draft7.py``. Output is deterministic
(``json.dumps`` with ``sort_keys=False``) so re-running on an unchanged
source produces a byte-identical diff (FR-28, AC-10.4).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "src" / "stargraph" / "schemas"
SOURCE_FILE = SCHEMAS_DIR / "ir-v1.json"
TARGET_FILE = SCHEMAS_DIR / "ir-v1-draft7.json"
DRAFT7_SCHEMA = "http://json-schema.org/draft-07/schema#"
DRAFT7_ID = "https://stargraph.dev/schemas/ir-v1-draft7.json"

DROP_KEYS = frozenset({"prefixItems", "unevaluatedProperties"})


def _transform(node: Any) -> Any:
    """Recursively apply Draft-7 transforms to a JSON-Schema node.

    Any string value beginning with ``#/$defs/`` is rewritten to
    ``#/definitions/...`` to follow the renamed top-level key. This
    catches both ``$ref`` / ``$dynamicRef`` pointers and discriminator
    ``mapping`` entries.
    """
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in DROP_KEYS:
                continue
            new_key = "definitions" if key == "$defs" else key
            if key == "$dynamicRef":
                new_key = "$ref"
            out[new_key] = _transform(value)
        return out
    if isinstance(node, list):
        return [_transform(item) for item in node]
    if isinstance(node, str) and node.startswith("#/$defs/"):
        return "#/definitions/" + node[len("#/$defs/") :]
    return node


def main() -> None:
    source = json.loads(SOURCE_FILE.read_text())
    transformed = _transform(source)
    transformed["$schema"] = DRAFT7_SCHEMA
    transformed["$id"] = DRAFT7_ID
    TARGET_FILE.write_text(json.dumps(transformed, indent=2, sort_keys=False) + "\n")
    print(f"wrote {TARGET_FILE.relative_to(SCHEMAS_DIR.parents[2])}")


if __name__ == "__main__":
    main()
