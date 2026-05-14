# SPDX-License-Identifier: Apache-2.0
"""``harbor serve`` wrapper that pins the cve-rem capability profile.

Mirrors the behavior of ``python -m harbor.cli.serve serve`` but
inserts the engine-side :class:`Capabilities` produced by
:func:`demos.cve_remediation.capabilities.build_cve_rem_capabilities`
into ``deps`` before constructing the FastAPI app. Production
deployments of the CVE remediation pipeline MUST use this entry
point (or an equivalent lifespan) -- otherwise ``run.capabilities``
is ``None`` and the engine-side gate is a silent no-op (CRITERIA
fancy #13 hardening).

Usage:

    set -a; source demos/cve_remediation/.env; set +a
    uv run --no-project python -m demos.cve_remediation.serve_cve_rem \
        --host 0.0.0.0 --port 9000

This script is intentionally thin: any divergence from
``harbor.cli.serve.main`` would be a maintenance hazard.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from demos.cve_remediation.capabilities import build_cve_rem_capabilities


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="serve_cve_rem",
        description=(
            "harbor serve with the cve-rem capability profile pinned."
        ),
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument(
        "--profile",
        default="oss-default",
        choices=("oss-default", "cleared"),
    )
    args = parser.parse_args(argv)

    # Lazy-import the harbor serve plumbing so this script works even
    # if the upstream CLI surface evolves.
    from contextlib import asynccontextmanager
    from collections.abc import AsyncGenerator

    import uvicorn
    from fastapi import FastAPI

    from harbor.checkpoint.aiosqlite import AiosqliteCheckpointer
    from harbor.errors import HarborRuntimeError
    from harbor.serve.api import create_app
    from harbor.serve.profiles import select_profile
    from harbor.serve.run_history import RunHistory
    from harbor.serve.scheduler import Scheduler
    from harbor.tools.registry import ToolRegistry
    from harbor.serve.artifact_store import ArtifactStore
    from harbor.serve.broker_lifespan import broker_lifespan
    from harbor.stores.registry import StoreRegistry

    selected = select_profile(cli_override=args.profile)
    checkpointer = AiosqliteCheckpointer()
    artifact_store = ArtifactStore()
    scheduler = Scheduler()

    deps: dict[str, Any] = {
        "scheduler": scheduler,
        "runs": {},
        "broadcasters": {},
        "run_history": None,
        "checkpointer": checkpointer,
        "artifact_store": artifact_store,
        "graphs": {},
        "registry": {
            "tools": ToolRegistry(),
            "stores": StoreRegistry(),
        },
        # CRITERIA fancy #13 hardening: engine-side capability profile.
        # Default-deny + scoped grants for every @tool referenced by
        # the cve-rem graph; tightens the engine boundary so a graph
        # that imports an undeclared tool fails loud at dispatch.
        "capabilities": build_cve_rem_capabilities(),
    }

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        await checkpointer.bootstrap()
        db = checkpointer._db  # pyright: ignore[reportPrivateUsage]
        if db is None:
            raise HarborRuntimeError("checkpointer bootstrap failed")
        run_history = RunHistory(db)
        await run_history.bootstrap()
        deps["run_history"] = run_history
        await artifact_store.bootstrap()
        scheduler.set_deps(deps)
        await scheduler.start()
        try:
            async with broker_lifespan():
                yield
        finally:
            await scheduler.stop()
            await checkpointer.close()

    app = create_app(selected, deps=deps, lifespan=_lifespan)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
