"""xView3 scene tiling and YOLO OBB label conversion.

Iterates xView3 scene directories (each containing VH_dB.tif,
VV_dB.tif, etc.), tiles into 640x640 patches with configurable
overlap, converts CSV point labels to YOLO OBB format (synthesised
from vessel_length_m + 1:4 w:l ratio), and generates a ``data.yaml``
config for Ultralytics OBB training.

Train/val split uses the natural xView3 convention: scene IDs ending
in ``t`` are training, ``v`` are validation.

Usage::

    uv run python -m demos.sentinel_dark_watch.scripts.prepare_dataset \
        --imagery-dir demos/sentinel_dark_watch/data/xview3/imagery \
        --train-csv demos/sentinel_dark_watch/data/xview3/labels/train.csv \
        --val-csv demos/sentinel_dark_watch/data/xview3/labels/validation.csv \
        --output-dir demos/sentinel_dark_watch/data/prepared
"""

from __future__ import annotations

import argparse
import csv
import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import rasterio  # type: ignore[import-untyped]
import rasterio.windows
from PIL import Image  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

DEFAULT_PATCH_SIZE = 640
DEFAULT_OVERLAP = 0.1
DEFAULT_VESSEL_LENGTH_M = 20.0
WIDTH_TO_LENGTH_RATIO = 0.25
VESSEL_CLASS_ID = 0


def _synthesise_obb(
    cx_px: float,
    cy_px: float,
    length_m: float,
    pixel_res: float,
    angle_rad: float = 0.0,
) -> list[tuple[float, float]]:
    """Return four OBB corners in pixel coords."""
    length_px = length_m / pixel_res if pixel_res > 0 else length_m
    width_px = length_px * WIDTH_TO_LENGTH_RATIO
    half_l, half_w = length_px / 2, width_px / 2

    corners = [
        (-half_l, -half_w),
        (half_l, -half_w),
        (half_l, half_w),
        (-half_l, half_w),
    ]
    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
    return [
        (dx * cos_a - dy * sin_a + cx_px, dx * sin_a + dy * cos_a + cy_px) for dx, dy in corners
    ]


def _obb_to_yolo_line(class_id: int, corners: list[tuple[float, float]], patch_size: int) -> str:
    """Format a YOLO OBB annotation line with normalised+clamped coords."""
    parts = []
    for x, y in corners:
        parts.append(f"{max(0.0, min(1.0, x / patch_size)):.6f}")
        parts.append(f"{max(0.0, min(1.0, y / patch_size)):.6f}")
    return f"{class_id} {' '.join(parts)}"


def _compute_patch_origins(
    scene_h: int, scene_w: int, patch_size: int, overlap: float
) -> list[tuple[int, int]]:
    """Return (row, col) origins for tiling a scene."""
    stride = max(1, int(patch_size * (1 - overlap)))
    origins: list[tuple[int, int]] = []
    for row in range(0, scene_h, stride):
        if row + patch_size > scene_h:
            row = max(0, scene_h - patch_size)
        for col in range(0, scene_w, stride):
            if col + patch_size > scene_w:
                col = max(0, scene_w - patch_size)
            if (row, col) not in origins:
                origins.append((row, col))
    return origins


def _load_scene_labels(csv_path: Path, scene_id: str) -> list[dict]:
    """Load labels for a specific scene_id from an xView3 CSV."""
    labels: list[dict] = []
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("scene_id") != scene_id:
                continue
            is_vessel = str(row.get("is_vessel", "")).strip().lower()
            if is_vessel in ("false", "0", "no"):
                continue
            try:
                labels.append(
                    {
                        "row": float(row["detect_scene_row"]),
                        "col": float(row["detect_scene_column"]),
                        "length_m": float(row.get("vessel_length_m") or DEFAULT_VESSEL_LENGTH_M),
                    }
                )
            except (KeyError, ValueError) as exc:
                logger.debug("Skipping malformed label: %s", exc)
    return labels


def _labels_in_patch(
    labels: list[dict], origin_row: int, origin_col: int, patch_size: int
) -> list[dict]:
    """Filter labels that fall within a patch."""
    result = []
    for lb in labels:
        lr, lc = lb["row"] - origin_row, lb["col"] - origin_col
        if 0 <= lr < patch_size and 0 <= lc < patch_size:
            result.append({**lb, "local_row": lr, "local_col": lc})
    return result


def _read_band(scene_dir: Path, band_name: str, window: rasterio.windows.Window) -> np.ndarray:
    """Read a single band from a scene directory, return (H, W) float32."""
    path = scene_dir / f"{band_name}.tif"
    if not path.exists():
        raise FileNotFoundError(f"Missing {band_name}.tif in {scene_dir}")
    with rasterio.open(path) as src:
        data = src.read(1, window=window).astype(np.float32)
    return data


def _normalise_to_uint8(arr: np.ndarray) -> np.ndarray:
    """Per-channel min-max normalisation to uint8."""
    mn, mx = np.nanmin(arr), np.nanmax(arr)
    if mx > mn:
        arr = (arr - mn) / (mx - mn) * 255
    else:
        arr = np.zeros_like(arr)
    return np.nan_to_num(arr, nan=0).astype(np.uint8)


def _save_patch(vh: np.ndarray, vv: np.ndarray, out_path: Path, patch_size: int) -> None:
    """Save VH+VV as a 3-channel PNG (VH, VV, VH-VV ratio)."""

    def pad(arr: np.ndarray) -> np.ndarray:
        if arr.shape[0] < patch_size or arr.shape[1] < patch_size:
            padded = np.full((patch_size, patch_size), np.nan, dtype=np.float32)
            padded[: arr.shape[0], : arr.shape[1]] = arr
            return padded
        return arr

    vh, vv = pad(vh), pad(vv)
    ratio = vh - vv  # dB difference
    stack = np.stack(
        [
            _normalise_to_uint8(vh),
            _normalise_to_uint8(vv),
            _normalise_to_uint8(ratio),
        ],
        axis=-1,
    )
    Image.fromarray(stack, mode="RGB").save(out_path)


def prepare_dataset(
    imagery_dir: Path,
    train_csv: Path,
    val_csv: Path,
    output_dir: Path,
    patch_size: int = DEFAULT_PATCH_SIZE,
    overlap: float = DEFAULT_OVERLAP,
) -> Path:
    """Tile xView3 scenes and convert labels to YOLO OBB format."""
    tiles_dir = output_dir / "tiles"
    for split in ("train", "val"):
        (tiles_dir / split / "images").mkdir(parents=True, exist_ok=True)
        (tiles_dir / split / "labels").mkdir(parents=True, exist_ok=True)

    scene_dirs = sorted(
        p for p in imagery_dir.iterdir() if p.is_dir() and (p / "VH_dB.tif").exists()
    )
    if not scene_dirs:
        raise FileNotFoundError(f"No scene directories with VH_dB.tif found in {imagery_dir}")

    logger.info("Found %d scenes in %s", len(scene_dirs), imagery_dir)
    total_patches = 0
    total_labelled = 0

    for scene_dir in scene_dirs:
        scene_id = scene_dir.name
        split = "train" if scene_id.endswith("t") else "val"
        labels_csv = train_csv if split == "train" else val_csv

        labels = _load_scene_labels(labels_csv, scene_id)
        logger.info("Scene %s (%s): %d labels", scene_id, split, len(labels))

        with rasterio.open(scene_dir / "VH_dB.tif") as src:
            scene_h, scene_w = src.height, src.width
            pixel_res = abs(src.res[0]) if src.res else 10.0

        origins = _compute_patch_origins(scene_h, scene_w, patch_size, overlap)
        logger.info("  %dx%d → %d patches (%.0fm/px)", scene_w, scene_h, len(origins), pixel_res)

        for idx, (row_off, col_off) in enumerate(origins):
            window = rasterio.windows.Window(
                col_off=col_off,
                row_off=row_off,
                width=min(patch_size, scene_w - col_off),
                height=min(patch_size, scene_h - row_off),
            )

            patch_labels = _labels_in_patch(labels, row_off, col_off, patch_size)

            ann_lines = []
            for lb in patch_labels:
                corners = _synthesise_obb(
                    cx_px=lb["local_col"],
                    cy_px=lb["local_row"],
                    length_m=lb["length_m"],
                    pixel_res=pixel_res,
                )
                ann_lines.append(_obb_to_yolo_line(VESSEL_CLASS_ID, corners, patch_size))

            patch_id = f"{scene_id}_p{idx:05d}"
            img_path = tiles_dir / split / "images" / f"{patch_id}.png"
            lbl_path = tiles_dir / split / "labels" / f"{patch_id}.txt"

            vh = _read_band(scene_dir, "VH_dB", window)
            vv = _read_band(scene_dir, "VV_dB", window)
            _save_patch(vh, vv, img_path, patch_size)

            with open(lbl_path, "w") as f:
                f.write("\n".join(ann_lines))

            total_patches += 1
            if ann_lines:
                total_labelled += 1

            if idx % 500 == 0 and idx > 0:
                logger.info("  ... %d/%d patches", idx, len(origins))

    logger.info(
        "Done: %d patches (%d with labels, %.1f%%)",
        total_patches,
        total_labelled,
        100.0 * total_labelled / total_patches if total_patches else 0,
    )
    return _write_data_yaml(output_dir, tiles_dir)


def _write_data_yaml(output_dir: Path, tiles_dir: Path) -> Path:
    yaml_path = output_dir / "data.yaml"
    content = (
        f"train: {(tiles_dir / 'train').resolve()}\n"
        f"val: {(tiles_dir / 'val').resolve()}\n"
        f"names:\n  0: vessel\n"
    )
    yaml_path.write_text(content)
    logger.info("Wrote %s", yaml_path)
    return yaml_path


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Tile xView3 scenes → YOLO OBB format.")
    parser.add_argument(
        "--imagery-dir",
        type=Path,
        required=True,
        help="Directory containing xView3 scene subdirectories.",
    )
    parser.add_argument(
        "--train-csv", type=Path, required=True, help="Path to xView3 train.csv labels."
    )
    parser.add_argument(
        "--val-csv", type=Path, required=True, help="Path to xView3 validation.csv labels."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/prepared"),
        help="Root output directory (default: data/prepared).",
    )
    parser.add_argument("--patch-size", type=int, default=DEFAULT_PATCH_SIZE)
    parser.add_argument("--overlap", type=float, default=DEFAULT_OVERLAP)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    prepare_dataset(
        imagery_dir=args.imagery_dir,
        train_csv=args.train_csv,
        val_csv=args.val_csv,
        output_dir=args.output_dir,
        patch_size=args.patch_size,
        overlap=args.overlap,
    )


if __name__ == "__main__":
    main()
