"""xView3 scene tiling and YOLO OBB label conversion.

Loads xView3 GeoTIFF scenes via rasterio, tiles into 640x640 patches
with configurable overlap, converts CSV point labels to YOLO OBB format
(synthesised from vessel_length_m + 1:4 w:l ratio), and generates a
``data.yaml`` config for Ultralytics training.
"""

from __future__ import annotations

import argparse
import csv
import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

try:
    import rasterio  # type: ignore[import-untyped]
except ImportError:
    rasterio = None  # graceful degradation for POC

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PATCH_SIZE = 640
DEFAULT_OVERLAP = 0.1
DEFAULT_OUTPUT_DIR = "data"
TRAIN_RATIO = 0.8
DEFAULT_VESSEL_LENGTH_M = 20.0
WIDTH_TO_LENGTH_RATIO = 0.25  # 1:4
VESSEL_CLASS_ID = 0


# ---------------------------------------------------------------------------
# Label helpers
# ---------------------------------------------------------------------------


def _synthesise_obb(
    cx_px: float,
    cy_px: float,
    length_m: float,
    pixel_res: float,
    angle_rad: float = 0.0,
) -> list[tuple[float, float]]:
    """Return four OBB corners in pixel coords.

    Parameters
    ----------
    cx_px, cy_px:
        Centre of the vessel in pixel coordinates (within the patch).
    length_m:
        Vessel length in metres.
    pixel_res:
        Ground sampling distance (m/px).
    angle_rad:
        Orientation angle in radians (default 0 = horizontal).

    Returns
    -------
    List of four (x, y) corner tuples, clockwise from top-left.
    """
    length_px = length_m / pixel_res if pixel_res > 0 else length_m
    width_px = length_px * WIDTH_TO_LENGTH_RATIO

    half_l = length_px / 2
    half_w = width_px / 2

    # Corners before rotation (centred at origin)
    corners = [
        (-half_l, -half_w),
        (half_l, -half_w),
        (half_l, half_w),
        (-half_l, half_w),
    ]

    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    rotated = []
    for dx, dy in corners:
        rx = dx * cos_a - dy * sin_a + cx_px
        ry = dx * sin_a + dy * cos_a + cy_px
        rotated.append((rx, ry))

    return rotated


def _normalise_corners(
    corners: list[tuple[float, float]], patch_size: int
) -> list[tuple[float, float]]:
    """Normalise pixel corners to [0, 1] range."""
    return [(x / patch_size, y / patch_size) for x, y in corners]


def _obb_to_yolo_line(class_id: int, corners: list[tuple[float, float]]) -> str:
    """Format a single YOLO OBB annotation line.

    Format: ``class x1 y1 x2 y2 x3 y3 x4 y4``
    """
    coords = " ".join(f"{x:.6f} {y:.6f}" for x, y in corners)
    return f"{class_id} {coords}"


# ---------------------------------------------------------------------------
# Scene loading + tiling
# ---------------------------------------------------------------------------


def _compute_patch_origins(
    scene_h: int, scene_w: int, patch_size: int, overlap: float
) -> list[tuple[int, int]]:
    """Return top-left (row, col) origins for tiling a scene."""
    stride = int(patch_size * (1 - overlap))
    origins: list[tuple[int, int]] = []
    row = 0
    while row < scene_h:
        col = 0
        while col < scene_w:
            origins.append((row, col))
            col += stride
            if col >= scene_w and col - stride + patch_size < scene_w:
                # Ensure we cover the right edge
                col = scene_w - patch_size
                if (col, row) != origins[-1]:
                    origins.append((row, col))
                break
        row += stride
        if row >= scene_h and row - stride + patch_size < scene_h:
            row = scene_h - patch_size
            # Re-tile last row
            col = 0
            while col < scene_w:
                origins.append((row, col))
                col += stride
            break
    return origins


def _load_labels(csv_path: Path) -> list[dict]:
    """Load xView3 CSV labels.

    Expected columns: detect_scene_row, detect_scene_column,
    vessel_length_m (optional), is_vessel (optional).
    """
    labels: list[dict] = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Skip non-vessel rows if column present
            if "is_vessel" in row and str(row["is_vessel"]).lower() in (
                "false",
                "0",
                "no",
            ):
                continue
            try:
                label = {
                    "row": float(row["detect_scene_row"]),
                    "col": float(row["detect_scene_column"]),
                    "length_m": float(row.get("vessel_length_m") or DEFAULT_VESSEL_LENGTH_M),
                }
            except (KeyError, ValueError) as exc:
                logger.warning("Skipping malformed label row: %s (%s)", row, exc)
                continue
            labels.append(label)
    return labels


def _labels_in_patch(
    labels: list[dict],
    origin_row: int,
    origin_col: int,
    patch_size: int,
) -> list[dict]:
    """Filter labels that fall within a given patch."""
    result = []
    for lb in labels:
        local_r = lb["row"] - origin_row
        local_c = lb["col"] - origin_col
        if 0 <= local_r < patch_size and 0 <= local_c < patch_size:
            result.append({**lb, "local_row": local_r, "local_col": local_c})
    return result


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def prepare_dataset(
    scene_dir: Path,
    labels_csv: Path,
    output_dir: Path,
    patch_size: int = DEFAULT_PATCH_SIZE,
    overlap: float = DEFAULT_OVERLAP,
) -> Path:
    """Tile xView3 scenes and convert labels to YOLO OBB format.

    Parameters
    ----------
    scene_dir:
        Directory containing GeoTIFF scene files.
    labels_csv:
        Path to the xView3 CSV labels file.
    output_dir:
        Root output directory (will contain tiles/ and data.yaml).
    patch_size:
        Patch dimension in pixels (default 640).
    overlap:
        Fractional overlap between adjacent patches (default 0.1).

    Returns
    -------
    Path to the generated ``data.yaml``.
    """
    if rasterio is None:
        raise ImportError(
            "rasterio is required for scene tiling. Install with: pip install rasterio"
        )
    if np is None:
        raise ImportError("numpy is required for scene tiling. Install with: pip install numpy")

    tiles_dir = output_dir / "tiles"
    train_img = tiles_dir / "train" / "images"
    train_lbl = tiles_dir / "train" / "labels"
    val_img = tiles_dir / "val" / "images"
    val_lbl = tiles_dir / "val" / "labels"

    for d in (train_img, train_lbl, val_img, val_lbl):
        d.mkdir(parents=True, exist_ok=True)

    # Load labels
    labels = _load_labels(labels_csv)
    logger.info("Loaded %d vessel labels from %s", len(labels), labels_csv)

    # Discover scene files
    scene_files = sorted(scene_dir.glob("*.tif")) + sorted(scene_dir.glob("*.tiff"))
    if not scene_files:
        logger.warning("No GeoTIFF files found in %s", scene_dir)
        return _write_data_yaml(output_dir, tiles_dir)

    total_patches = 0
    all_patch_ids: list[str] = []

    for scene_path in scene_files:
        logger.info("Processing scene: %s", scene_path.name)

        with rasterio.open(scene_path) as src:
            scene_h = src.height
            scene_w = src.width
            pixel_res = abs(src.res[0]) if src.res else 1.0

            origins = _compute_patch_origins(scene_h, scene_w, patch_size, overlap)
            logger.info(
                "  Scene %dx%d -> %d patches (%.1fm/px)",
                scene_w,
                scene_h,
                len(origins),
                pixel_res,
            )

            for idx, (row_off, col_off) in enumerate(origins):
                # Read patch
                window = rasterio.windows.Window(
                    col_off=col_off,
                    row_off=row_off,
                    width=min(patch_size, scene_w - col_off),
                    height=min(patch_size, scene_h - row_off),
                )
                patch_data = src.read(window=window)

                # Pad if needed (edge patches)
                if patch_data.shape[1] < patch_size or patch_data.shape[2] < patch_size:
                    padded = np.zeros(
                        (patch_data.shape[0], patch_size, patch_size),
                        dtype=patch_data.dtype,
                    )
                    padded[:, : patch_data.shape[1], : patch_data.shape[2]] = patch_data
                    patch_data = padded

                patch_id = f"{scene_path.stem}_p{idx:05d}"
                all_patch_ids.append(patch_id)

                # Find labels in this patch
                patch_labels = _labels_in_patch(labels, row_off, col_off, patch_size)

                # Write annotation
                ann_lines = []
                for lb in patch_labels:
                    corners = _synthesise_obb(
                        cx_px=lb["local_col"],
                        cy_px=lb["local_row"],
                        length_m=lb["length_m"],
                        pixel_res=pixel_res,
                    )
                    norm_corners = _normalise_corners(corners, patch_size)
                    # Clamp to [0, 1]
                    norm_corners = [
                        (max(0.0, min(1.0, x)), max(0.0, min(1.0, y))) for x, y in norm_corners
                    ]
                    ann_lines.append(_obb_to_yolo_line(VESSEL_CLASS_ID, norm_corners))

                # Decide train/val split (deterministic per patch_id)
                is_train = (hash(patch_id) % 100) < (TRAIN_RATIO * 100)
                split = "train" if is_train else "val"

                img_dir = tiles_dir / split / "images"
                lbl_dir = tiles_dir / split / "labels"

                # Save patch as PNG (first band for single-channel, or 3-band)
                _save_patch_png(patch_data, img_dir / f"{patch_id}.png")

                # Save label file
                with open(lbl_dir / f"{patch_id}.txt", "w") as f:
                    f.write("\n".join(ann_lines))

                total_patches += 1

    logger.info("Total patches written: %d", total_patches)
    return _write_data_yaml(output_dir, tiles_dir)


def _save_patch_png(patch_data, out_path: Path) -> None:
    """Save a numpy array patch as a PNG file."""
    try:
        from PIL import Image  # type: ignore[import-untyped]
    except ImportError:
        # Fallback: write raw numpy (not viewable but functional)
        np.save(str(out_path).replace(".png", ".npy"), patch_data)
        return

    # Squeeze to HWC for PIL
    if patch_data.ndim == 3:
        if patch_data.shape[0] == 1:
            arr = patch_data[0]
        elif patch_data.shape[0] == 3:
            arr = np.transpose(patch_data, (1, 2, 0))
        else:
            arr = patch_data[0]  # take first band
    else:
        arr = patch_data

    # Normalise to uint8
    if arr.dtype != np.uint8:
        mn, mx = arr.min(), arr.max()
        if mx > mn:
            arr = ((arr - mn) / (mx - mn) * 255).astype(np.uint8)
        else:
            arr = np.zeros_like(arr, dtype=np.uint8)

    Image.fromarray(arr).save(out_path)


def _write_data_yaml(output_dir: Path, tiles_dir: Path) -> Path:
    """Generate Ultralytics data.yaml."""
    yaml_path = output_dir / "data.yaml"
    content = f"train: {tiles_dir / 'train'}\nval: {tiles_dir / 'val'}\nnames:\n  0: vessel\n"
    yaml_path.write_text(content)
    logger.info("Wrote %s", yaml_path)
    return yaml_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Tile xView3 scenes and convert labels to YOLO OBB format."
    )
    parser.add_argument(
        "--scene-dir",
        type=Path,
        required=True,
        help="Directory containing xView3 GeoTIFF scenes.",
    )
    parser.add_argument(
        "--labels-csv",
        type=Path,
        required=True,
        help="Path to xView3 CSV labels file.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(DEFAULT_OUTPUT_DIR),
        help=f"Root output directory (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--patch-size",
        type=int,
        default=DEFAULT_PATCH_SIZE,
        help=f"Patch dimension in pixels (default: {DEFAULT_PATCH_SIZE}).",
    )
    parser.add_argument(
        "--overlap",
        type=float,
        default=DEFAULT_OVERLAP,
        help=f"Fractional overlap between patches (default: {DEFAULT_OVERLAP}).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    prepare_dataset(
        scene_dir=args.scene_dir,
        labels_csv=args.labels_csv,
        output_dir=args.output_dir,
        patch_size=args.patch_size,
        overlap=args.overlap,
    )


if __name__ == "__main__":
    main()
