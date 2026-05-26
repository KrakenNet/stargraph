# SPDX-License-Identifier: Apache-2.0
"""Fine-tune YOLO11-OBB on prepared SAR data, export to ONNX, register in ModelRegistry.

Usage::

    uv run --no-project python -m demos.sentinel_dark_watch.scripts.train_detector \
        --data data/data.yaml --epochs 50

Requires ``ultralytics`` (optional dep). When absent the script logs a
warning and exits cleanly so the rest of the demo remains importable.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)


def _sha256(path: Path) -> str:
    """Return hex SHA-256 of *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fine-tune YOLO11-OBB → ONNX export → ModelRegistry",
    )
    p.add_argument("--data", type=Path, required=True, help="Path to data.yaml")
    p.add_argument("--epochs", type=int, default=50, help="Training epochs (default 50)")
    p.add_argument("--model", default="yolo11s-obb.pt", help="Base model (default yolo11s-obb.pt)")
    p.add_argument(
        "--registry-db",
        type=Path,
        default=Path("data/model_registry.db"),
        help="SQLite path for ModelRegistry",
    )
    return p.parse_args(argv)


async def _register(
    db_path: Path,
    onnx_path: Path,
    content_hash: str,
    version: str,
) -> None:
    """Register trained model and set ``production`` alias."""
    from harbor.ml.registry import ModelRegistry

    registry = ModelRegistry(db_path)
    await registry.bootstrap()
    try:
        await registry.register(
            model_id="sdw-detector",
            version=version,
            runtime="onnx",
            file_uri=f"file://{onnx_path.resolve()}",  # noqa: ASYNC240
            content_hash=content_hash,
            framework="ultralytics-yolo11",
        )
        await registry.alias(
            model_id="sdw-detector",
            alias="production",
            version=version,
        )
        log.info("Registered sdw-detector %s → production", version)
    finally:
        await registry.close()


def main(argv: list[str] | None = None) -> None:
    """Entry point."""
    args = _parse_args(argv)

    try:
        from ultralytics import YOLO
    except ImportError:
        log.warning("ultralytics not installed — cannot train. pip install ultralytics")
        sys.exit(1)

    log.info("Training %s on %s for %d epochs", args.model, args.data, args.epochs)
    model = YOLO(args.model)
    model.train(data=str(args.data), epochs=args.epochs, imgsz=640, batch=16)

    # Export to ONNX — returns the path to the exported file
    onnx_path_str: str = model.export(format="onnx")
    onnx_path = Path(onnx_path_str)
    log.info("Exported ONNX → %s", onnx_path)

    content_hash = _sha256(onnx_path)
    version = f"v{args.epochs}_{int(time.time())}"
    log.info("SHA-256: %s  version: %s", content_hash, version)

    asyncio.run(_register(args.registry_db, onnx_path, content_hash, version))
    log.info("Done.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
