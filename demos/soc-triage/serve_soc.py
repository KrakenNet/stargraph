# SPDX-License-Identifier: Apache-2.0
"""``stargraph serve`` wrapper for the SOC Triage++ demo.

Thin argparse wrapper around :func:`stargraph.serve.api.create_app` modelled on
:mod:`demos.sentinel_dark_watch.serve_sdw`. At boot it:

* inserts ``demos/soc-triage`` on ``sys.path`` so the IR's
  ``graph.nodes:IngestAlert`` / ``graph.state:RunState`` ``module:Class`` refs
  resolve (the demo dir is hyphenated → not importable as a package; ``graph/``
  is the top-level package — see task 1.28);
* loads ``graph/stargraph.yaml`` and injects the absolute ``file://`` URI of
  ``models/severity_classifier.onnx`` into the ``risk_score`` MLNode config
  (the committed IR keeps ``file_uri: null`` so it validates portably; the
  ``expected_sha256`` pin is verified on load by ``stargraph.ml.loaders`` — task
  1.29);
* builds the node registry (loads + pins the ONNX model) and a :class:`Graph`;
* verifies the signed demo-local ``soc-policy`` Bosun pack with
  :func:`stargraph.bosun.signing.verify_pack` against the committed dev pubkey
  under :class:`ClearedProfile` and logs the result (the pack's CLIPS rules are
  declared inline in the IR governance section; serve does not auto-load
  demo-local packs — task 1.31);
* wires ``deps`` (SQLite checkpointer — required or replay/counterfactual POSTs
  503, filesystem artifact store for ``write_artifact``, the engine-side
  ``runs:respond`` capability the HITL ``analyst_gate`` needs) and hands the
  app to ``uvicorn``.

The ``triage_decide`` ``dspy`` node's LM is configured from ``LLM_BASE_URL`` /
``LLM_MODEL`` (CLI ``--lm-url`` / ``--lm-model`` override). When neither is set
the server still boots — the LM is only needed at run time. graph_viewer
attaches via its ``--upstream http://127.0.0.1:9020`` flag (proxies
``/api/runs*`` → ``/v1/runs*``).

Usage::

    LLM_BASE_URL=http://localhost:41001 LLM_MODEL=qwen2.5 \
        uv run --no-project python demos/soc-triage/serve_soc.py \
        --host 0.0.0.0 --port 9020
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("serve_soc")

# Demo package root (…/demos/soc-triage). Inserted on sys.path below so the
# IR's ``graph.nodes:IngestAlert`` / ``graph.state:RunState`` refs import.
_DEMO_ROOT = Path(__file__).resolve().parent

# Signed demo-local Bosun pack verified at boot (committed dev key — task 1.31).
_SOC_POLICY_PACK = _DEMO_ROOT / "bosun-packs" / "soc-policy"
_SOC_POLICY_KEY_ID = "dev-soc-1cdb9c59"

# ONNX severity classifier the ``risk_score`` MLNode loads (sha256-pinned in
# the IR; this absolute file:// URI is injected into the node config at boot).
_ONNX_MODEL = _DEMO_ROOT / "models" / "severity_classifier.onnx"


def _verify_soc_policy_pack() -> None:
    """Verify the signed ``soc-policy`` Bosun pack and log the outcome.

    Verification is advisory (the pack's rules live inline in the IR
    governance section); a failure logs an error but does not block boot so
    the demo stays runnable even if the committed signature is stale.
    """
    from stargraph.bosun.signing import StaticTrustStore, verify_pack
    from stargraph.serve.profiles import ClearedProfile

    pubkey = _SOC_POLICY_PACK / f"{_SOC_POLICY_KEY_ID}.pub.pem"
    manifest = _SOC_POLICY_PACK / "manifest.jwt"
    if not (pubkey.exists() and manifest.exists()):
        logger.warning("soc-policy pack incomplete (missing pubkey/manifest) — skipping verify")
        return
    try:
        trust = StaticTrustStore({_SOC_POLICY_KEY_ID: pubkey.read_bytes()})
        result = verify_pack(
            _SOC_POLICY_PACK,
            manifest.read_text(encoding="utf-8"),
            trust,
            ClearedProfile(),
        )
        logger.info(
            "soc-policy pack verify: verified=%s key_id=%s",
            result.verified,
            result.key_id,
        )
    except Exception:  # advisory check, never fatal at boot
        logger.exception("soc-policy pack verification raised — continuing")


def _build_soc_capabilities() -> object:
    """Engine-side default-deny capability profile for soc-triage++.

    Grants only ``runs:respond`` — the capability the HITL ``analyst_gate``
    interrupt requires so an analyst can ``POST /v1/runs/{id}/respond`` to
    approve/deny a disposition. The graph references no ``@tool`` callables.
    """
    from stargraph.security import Capabilities, CapabilityClaim

    granted = {CapabilityClaim(name="runs", scope="respond")}
    return Capabilities(default_deny=True, granted=granted)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="serve_soc",
        description="stargraph serve for the SOC Triage++ demo (ONNX risk + HITL).",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9020)
    parser.add_argument(
        "--profile",
        default="oss-default",
        choices=("oss-default", "cleared"),
    )
    parser.add_argument(
        "--graph",
        default=str(_DEMO_ROOT / "graph" / "stargraph.yaml"),
        help="IR YAML graph to load and register at boot.",
    )
    parser.add_argument(
        "--lm-url",
        default=None,
        help="DSPy LM base URL (default: LLM_BASE_URL env).",
    )
    parser.add_argument(
        "--lm-model",
        default=None,
        help="DSPy LM model (default: LLM_MODEL env).",
    )
    args = parser.parse_args(argv)

    import os

    import uvicorn

    from stargraph.serve.api import create_app
    from stargraph.serve.profiles import select_profile

    # The IR's ``module:Class`` node/state refs resolve against demos/soc-triage.
    if str(_DEMO_ROOT) not in sys.path:
        sys.path.insert(0, str(_DEMO_ROOT))

    os.environ["STARGRAPH_PROFILE"] = args.profile
    selected = select_profile()

    # ---- DSPy LM (triage_decide) from env / CLI ------------------------
    from stargraph.cli.run import _configure_lm

    lm_url = args.lm_url or os.environ.get("LLM_BASE_URL") or None
    lm_model = args.lm_model or os.environ.get("LLM_MODEL") or None
    lm_key = os.environ.get("LLM_API_KEY", "no-key")
    lm_timeout = int(os.environ.get("LLM_TIMEOUT_SECONDS", "180"))
    if lm_url and lm_model:
        _configure_lm(lm_url, lm_model, lm_key, lm_timeout)
        logger.info("dspy.LM → %s @ %s", lm_model, lm_url)
    else:
        logger.info(
            "no LLM_BASE_URL/LLM_MODEL set — triage_decide LM unconfigured "
            "(server boots; LM only needed at run time)"
        )

    # ---- Load graph + inject ONNX file:// URI --------------------------
    import yaml as _yaml

    from stargraph.cli.run import _build_node_registry
    from stargraph.graph.definition import Graph
    from stargraph.ir._models import IRDocument

    graph_path = Path(args.graph)
    ir_doc = IRDocument.model_validate(_yaml.safe_load(graph_path.read_text(encoding="utf-8")))
    model_uri = _ONNX_MODEL.resolve().as_uri()
    for node in ir_doc.nodes:
        if node.id == "risk_score":
            node.config["file_uri"] = model_uri
    graph_obj = Graph(ir=ir_doc)
    node_registry = _build_node_registry(ir_doc.nodes, ir_dir=graph_path.parent.resolve())
    logger.info(
        "loaded graph %r (nodes=%d, hash=%s, onnx=%s)",
        ir_doc.id,
        len(ir_doc.nodes),
        graph_obj.graph_hash[:12],
        model_uri,
    )

    # ---- Verify the signed soc-policy Bosun pack -----------------------
    _verify_soc_policy_pack()

    # ---- Wire deps -----------------------------------------------------
    import tempfile

    from stargraph.artifacts.fs import FilesystemArtifactStore
    from stargraph.checkpoint.sqlite import SQLiteCheckpointer
    from stargraph.errors import StargraphRuntimeError
    from stargraph.registry import StoreRegistry, ToolRegistry
    from stargraph.serve.history import RunHistory
    from stargraph.serve.lifecycle import broker_lifespan
    from stargraph.serve.scheduler import Scheduler

    tmpdir = Path(tempfile.mkdtemp(prefix="soc-serve-"))
    checkpointer = SQLiteCheckpointer(tmpdir / "checkpoint.sqlite")
    artifact_store = FilesystemArtifactStore(tmpdir / "artifacts")
    scheduler = Scheduler()

    from typing import Any

    deps: dict[str, Any] = {
        "scheduler": scheduler,
        "runs": {},
        "broadcasters": {},
        "run_history": None,
        "checkpointer": checkpointer,
        "artifact_store": artifact_store,
        "graphs": {ir_doc.id: graph_obj},
        "node_registry": {ir_doc.id: node_registry},
        "registry": {
            "tools": ToolRegistry(),
            "stores": StoreRegistry(),
        },
        "capabilities": _build_soc_capabilities(),
        "fathom": None,
    }

    from collections.abc import AsyncGenerator  # noqa: TC003
    from contextlib import asynccontextmanager

    from fastapi import FastAPI  # noqa: TC002

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        await checkpointer.bootstrap()
        db = checkpointer._db  # pyright: ignore[reportPrivateUsage]
        if db is None:
            raise StargraphRuntimeError("checkpointer bootstrap failed")
        run_history = RunHistory(db)
        await run_history.bootstrap()
        deps["run_history"] = run_history
        await artifact_store.bootstrap()
        scheduler.set_deps(deps)
        scheduler.set_run_history(run_history)
        await scheduler.start()
        try:
            async with broker_lifespan():
                yield
        finally:
            await scheduler.stop()
            await checkpointer.close()

    app = create_app(selected, deps=deps, lifespan=_lifespan)

    @app.get("/health")
    async def _health() -> dict[str, str]:
        return {"status": "ok"}

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
