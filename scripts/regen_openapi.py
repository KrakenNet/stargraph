# SPDX-License-Identifier: Apache-2.0
"""Regenerate ``docs/reference/openapi.json`` from stargraph.serve OpenAPI spec.

Idempotent: running twice produces byte-identical output. CI gates on
``git diff --exit-code docs/reference/openapi.json`` to catch drift between
the on-disk reference spec and the actual FastAPI route surface.

Spec ref: stargraph-serve-and-bosun §5.3, §16, §14.3 (FR-12, AC-7.4).
"""

from __future__ import annotations

import json
from pathlib import Path

from stargraph.serve.api import create_app
from stargraph.serve.openapi import regen_openapi_spec
from stargraph.serve.profiles import OssDefaultProfile


def main() -> None:
    """Render the augmented OpenAPI 3.1 spec to ``docs/reference/openapi.json``.

    Uses :class:`OssDefaultProfile` with an empty deps dict; route definitions
    are profile-agnostic for OpenAPI generation purposes (the spec describes
    the request/response shape, not the runtime dependency wiring). The
    cleared profile would emit the same routes — only the gate semantics
    differ at handler dispatch time.

    JSON dump uses ``sort_keys=True`` to guarantee byte-stable ordering
    across Pydantic discriminated-union schema serializations (FastAPI does
    not pin a deterministic order otherwise) and ``ensure_ascii=False`` so
    UTF-8 strings survive round-trip without ``\\uXXXX`` escaping. A
    trailing newline keeps the file POSIX-compliant for git diff hygiene.
    """
    app = create_app(OssDefaultProfile(), {})
    spec = regen_openapi_spec(app)
    out = Path("docs/reference/openapi.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
