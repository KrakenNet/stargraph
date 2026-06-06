# SPDX-License-Identifier: Apache-2.0
"""Fine-tune YOLO11-OBB on prepared SAR data, export to ONNX, register in ModelRegistry.

Usage::

    uv run python -m demos.sentinel_dark_watch.scripts.train_detector \
        --data demos/sentinel_dark_watch/data/prepared/data.yaml \
        --epochs 50
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import time
from pathlib import Path

from ultralytics import YOLO

from stargraph.ml.registry import ModelRegistry

log = logging.getLogger(__name__)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fine-tune YOLO11-OBB → ONNX → ModelRegistry")
    p.add_argument("--data", type=Path, required=True, help="Path to data.yaml")
    p.add_argument("--epochs", type=int, default=50)
    p.add_argument("--batch", type=int, default=16)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--model", default="yolo11s-obb.pt", help="Base model")
    p.add_argument("--device", default="0", help="CUDA device (0, 0,1, or cpu)")
    p.add_argument("--registry-db", type=Path, default=Path("data/model_registry.db"))
    return p.parse_args(argv)


async def _register(db_path: Path, onnx_path: Path, content_hash: str, version: str) -> None:
    registry = ModelRegistry(db_path)
    await registry.bootstrap()
    try:
        await registry.register(
            model_id="sdw-detector",
            version=version,
            runtime="onnx",
            file_uri=f"file://{onnx_path.resolve()}",
            content_hash=content_hash,
            framework="ultralytics-yolo11",
        )
        await registry.alias(model_id="sdw-detector", alias="production", version=version)
        log.info("Registered sdw-detector %s → production", version)
    finally:
        await registry.close()


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    log.info("Training %s on %s — %d epochs, batch=%d, device=%s",
             args.model, args.data, args.epochs, args.batch, args.device)

    model = YOLO(args.model)
    results = model.train(
        data=str(args.data),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        project="runs/sdw",
        name="train",
        exist_ok=True,
    )
    log.info("Training complete. Best mAP50: %.4f", results.results_dict.get("metrics/mAP50(B)", 0))

    onnx_path_str: str = model.export(format="onnx", imgsz=args.imgsz)
    onnx_path = Path(onnx_path_str)
    log.info("Exported ONNX → %s", onnx_path)

    content_hash = _sha256(onnx_path)
    version = f"v{args.epochs}ep_{int(time.time())}"
    log.info("SHA-256: %s  version: %s", content_hash, version)

    asyncio.run(_register(args.registry_db, onnx_path, content_hash, version))
    log.info("Done. Model registered as sdw-detector/%s", version)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    main()
