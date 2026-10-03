"""Turn the class raster into polygons, with per-polygon uncertainty.

The raster is read in chunks so a large mosaic never has to fit in memory.
For every chunk (in pixel coordinates, so vertices are exact integers):

1. small specks are removed with a sieve filter (optional),
2. connected regions of one class are vectorised,
3. the same regions are rasterised back as labels, and one ``bincount`` over
   the chunk yields the pixel count and the confidence / entropy sums of
   every region at once (cost proportional to the chunk, not to
   polygons x raster as before).

Regions cut by a chunk border are stitched back together afterwards.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from backend.ai.classes import CLASS_NAMES, NODATA_CLASS

# Background is not exported as a feature polygon unless asked for.
EXPORT_CLASSES = {1, 2, 3, 4, 5}


@dataclass
class Region:
    """One connected region of a single class, in the raster's CRS."""

    geometry: Any  # shapely Polygon / MultiPolygon
    class_id: int
    pixels: int
    confidence_sum: float
    entropy_sum: float
    touches_seam: bool = False
    merged_from: int = 1

    @property
    def mean_confidence(self) -> float | None:
        return self.confidence_sum / self.pixels if self.pixels else None

    @property
    def mean_entropy(self) -> float | None:
        return self.entropy_sum / self.pixels if self.pixels else None


def calculate_review_priority(mean_entropy, mean_confidence) -> str:
    """Legacy helper kept for callers of the original module.

    These are model-derived indicators, not calibrated probabilities. The
    full assessment, including geometry signals, lives in ``backend.ai.qa``.
    """

    if mean_confidence < 0.60 or mean_entropy > 1.20:
        return "High"
    if mean_confidence < 0.80 or mean_entropy > 0.70:
        return "Medium"
    return "Low"


def chunk_windows(width: int, height: int, chunk: int):
    """Yield ``rasterio.windows.Window`` objects covering the raster."""

    from rasterio.windows import Window

    for row in range(0, height, chunk):
        for col in range(0, width, chunk):
            yield Window(col, row, min(chunk, width - col), min(chunk, height - row))


def polygonize_chunk(
    prediction: np.ndarray,
    confidence: np.ndarray,
    entropy: np.ndarray,
    *,
    classes: set[int],
    col_off: int = 0,
    row_off: int = 0,
    sieve_min_pixels: int = 0,
    seam_sides: tuple[bool, bool, bool, bool] = (False, False, False, False),
) -> list[Region]:
    """Vectorise one chunk into regions expressed in *pixel* coordinates
    (x = column, y = row) of the whole raster.

    Working in pixel space keeps every vertex an exact integer, so regions
    cut by a chunk border meet exactly and can be stitched reliably; the
    caller applies the raster's affine transform afterwards.

    ``seam_sides`` is ``(left, top, right, bottom)``: True where the chunk
    has a neighbour, i.e. where a region may continue in the next chunk.
    """

    from affine import Affine
    from rasterio.features import rasterize, shapes, sieve
    from shapely.geometry import shape

    prediction = np.ascontiguousarray(prediction, dtype=np.uint8)
    if prediction.ndim != 2:
        raise ValueError("prediction must be a 2D array.")
    if confidence.shape != prediction.shape or entropy.shape != prediction.shape:
        raise ValueError("confidence / entropy and prediction shapes do not match.")

    valid = prediction != NODATA_CLASS
    if not valid.any():
        return []

    if sieve_min_pixels and sieve_min_pixels > 1:
        prediction = sieve(
            prediction, size=int(sieve_min_pixels), mask=valid.astype(np.uint8), connectivity=4
        )

    wanted = valid & np.isin(prediction, list(classes))
    if not wanted.any():
        return []

    pixel_transform = Affine.translation(col_off, row_off)

    geometries: list[dict] = []
    class_ids: list[int] = []
    for geom_json, value in shapes(
        prediction, mask=wanted, connectivity=4, transform=pixel_transform
    ):
        geometries.append(geom_json)
        class_ids.append(int(value))
    if not geometries:
        return []

    labels = rasterize(
        ((geom, index + 1) for index, geom in enumerate(geometries)),
        out_shape=prediction.shape,
        transform=pixel_transform,
        fill=0,
        dtype="int32",
    )
    flat = labels.ravel()
    size = len(geometries) + 1
    pixels = np.bincount(flat, minlength=size)
    confidence_sum = np.bincount(flat, weights=confidence.ravel().astype(np.float64), minlength=size)
    entropy_sum = np.bincount(flat, weights=entropy.ravel().astype(np.float64), minlength=size)

    height, width = prediction.shape
    left, top, right, bottom = seam_sides
    x_min, x_max = col_off, col_off + width
    y_min, y_max = row_off, row_off + height

    regions: list[Region] = []
    for index, (geom_json, class_id) in enumerate(zip(geometries, class_ids), start=1):
        geometry = shape(geom_json)
        if geometry.is_empty:
            continue
        gx_min, gy_min, gx_max, gy_max = geometry.bounds
        touches = (
            (left and gx_min <= x_min)
            or (right and gx_max >= x_max)
            or (top and gy_min <= y_min)
            or (bottom and gy_max >= y_max)
        )
        regions.append(
            Region(
                geometry=geometry,
                class_id=class_id,
                pixels=int(pixels[index]),
                confidence_sum=float(confidence_sum[index]),
                entropy_sum=float(entropy_sum[index]),
                touches_seam=bool(touches),
            )
        )
    return regions


def to_map_coordinates(geometry, transform):
    """Apply a raster affine transform to a pixel-space geometry."""

    from shapely.affinity import affine_transform

    return affine_transform(
        geometry,
        [transform.a, transform.b, transform.d, transform.e, transform.c, transform.f],
    )


def merge_seam_regions(regions: list[Region]) -> list[Region]:
    """Join regions of the same class that share an edge across a chunk seam."""

    from shapely import STRtree
    from shapely.ops import unary_union

    seam = [region for region in regions if region.touches_seam]
    if len(seam) < 2:
        return regions
    result = [region for region in regions if not region.touches_seam]

    parent = list(range(len(seam)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    geometries = [region.geometry for region in seam]
    tree = STRtree(geometries)
    for i, geometry in enumerate(geometries):
        for j in tree.query(geometry, predicate="intersects"):
            j = int(j)
            if j <= i or seam[i].class_id != seam[j].class_id:
                continue
            # 4-connectivity: a shared edge, not merely a shared corner.
            if geometry.intersection(geometries[j]).length > 0:
                root_i, root_j = find(i), find(j)
                if root_i != root_j:
                    parent[root_j] = root_i

    groups: dict[int, list[int]] = {}
    for i in range(len(seam)):
        groups.setdefault(find(i), []).append(i)

    for members in groups.values():
        if len(members) == 1:
            region = seam[members[0]]
            region.touches_seam = False
            result.append(region)
            continue
        parts = [seam[i] for i in members]
        result.append(
            Region(
                geometry=unary_union([part.geometry for part in parts]),
                class_id=parts[0].class_id,
                pixels=sum(part.pixels for part in parts),
                confidence_sum=sum(part.confidence_sum for part in parts),
                entropy_sum=sum(part.entropy_sum for part in parts),
                touches_seam=False,
                merged_from=len(parts),
            )
        )
    return result


def polygonize_rasters(
    prediction_path: Path | str,
    confidence_path: Path | str,
    entropy_path: Path | str,
    *,
    chunk: int = 4096,
    sieve_min_pixels: int = 0,
    export_background: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[list[Region], dict[str, Any]]:
    """Vectorise the prediction raster chunk by chunk.

    Returns ``(regions, info)``; region geometries are in the raster's CRS
    (stitching happens in pixel space first, see ``polygonize_chunk``).
    """

    import rasterio

    classes = set(EXPORT_CLASSES)
    if export_background:
        classes.add(0)

    from rasterio.features import sieve as sieve_filter
    from rasterio.windows import Window

    # The sieve runs on each chunk plus a margin, so a region cut by a chunk
    # border is judged on more than the sliver inside the chunk. Pixels it
    # reassigns are counted per class.
    sieve_size = int(sieve_min_pixels or 0)
    margin = 2 * int(math.ceil(math.sqrt(sieve_size))) if sieve_size > 1 else 0
    removed = np.zeros(256, dtype=np.int64)
    gained = np.zeros(256, dtype=np.int64)

    regions: list[Region] = []
    with rasterio.open(prediction_path) as pred_src, rasterio.open(
        confidence_path
    ) as conf_src, rasterio.open(entropy_path) as ent_src:
        width, height = pred_src.width, pred_src.height
        windows = list(chunk_windows(width, height, chunk))
        for done, window in enumerate(windows, start=1):
            if margin:
                c0 = max(0, int(window.col_off) - margin)
                r0 = max(0, int(window.row_off) - margin)
                c1 = min(width, int(window.col_off + window.width) + margin)
                r1 = min(height, int(window.row_off + window.height) + margin)
                padded = pred_src.read(1, window=Window(c0, r0, c1 - c0, r1 - r0))
                valid = (padded != NODATA_CLASS).astype(np.uint8)
                sieved = sieve_filter(padded, size=sieve_size, mask=valid, connectivity=4)
                rs = int(window.row_off) - r0
                cs = int(window.col_off) - c0
                core = (slice(rs, rs + int(window.height)), slice(cs, cs + int(window.width)))
                original, prediction = padded[core], np.ascontiguousarray(sieved[core])
                changed = original != prediction
                if changed.any():
                    removed += np.bincount(original[changed], minlength=256)
                    gained += np.bincount(prediction[changed], minlength=256)
            else:
                prediction = pred_src.read(1, window=window)
            confidence = conf_src.read(1, window=window)
            entropy = ent_src.read(1, window=window)
            seam_sides = (
                window.col_off > 0,
                window.row_off > 0,
                window.col_off + window.width < width,
                window.row_off + window.height < height,
            )
            regions.extend(
                polygonize_chunk(
                    prediction,
                    confidence,
                    entropy,
                    classes=classes,
                    col_off=int(window.col_off),
                    row_off=int(window.row_off),
                    sieve_min_pixels=0,  # already sieved above
                    seam_sides=seam_sides,
                )
            )
            if progress:
                progress(done, len(windows))
        crs = pred_src.crs
        transform = pred_src.transform

    before = len(regions)
    regions = merge_seam_regions(regions)
    for region in regions:
        region.geometry = to_map_coordinates(region.geometry, transform)
    info = {
        "chunks": len(windows),
        "chunk_size": chunk,
        "regions_before_seam_merge": before,
        "regions": len(regions),
        "seam_merges": sum(1 for region in regions if region.merged_from > 1),
        "sieve_min_pixels": sieve_size,
        "sieve_margin_px": margin,
        "sieve_reassigned": {
            CLASS_NAMES.get(c, str(c)): {"removed_pixels": int(removed[c]), "gained_pixels": int(gained[c])}
            for c in range(256)
            if removed[c] or gained[c]
        },
        "classes": [CLASS_NAMES[c] for c in sorted(classes)],
        "crs": crs,
        "transform": transform,
    }
    return regions, info


def polygonize_to_geodataframe(prediction, confidence, entropy, transform, crs, min_area=0.0):
    """In-memory variant kept for callers of the original module.

    Returns a GeoDataFrame in ``crs`` with ``class_id``, ``class_name``,
    ``area_crs_units2``, ``mean_entropy``, ``mean_confidence`` and
    ``review_priority``.
    """

    import geopandas as gpd

    regions = polygonize_chunk(
        np.asarray(prediction, dtype=np.uint8),
        np.asarray(confidence, dtype=np.float32),
        np.asarray(entropy, dtype=np.float32),
        classes=set(EXPORT_CLASSES),
    )
    records = []
    for region in regions:
        geometry = to_map_coordinates(region.geometry, transform)
        if not geometry.is_valid:
            geometry = geometry.buffer(0)
        if geometry.is_empty or (min_area > 0 and geometry.area < min_area):
            continue
        mean_confidence = region.mean_confidence or 0.0
        mean_entropy = region.mean_entropy or 0.0
        records.append(
            {
                "class_id": region.class_id,
                "class_name": CLASS_NAMES.get(region.class_id, "Unknown"),
                "area_crs_units2": float(geometry.area),
                "mean_entropy": round(mean_entropy, 6),
                "mean_confidence": round(mean_confidence, 6),
                "review_priority": calculate_review_priority(mean_entropy, mean_confidence),
                "geometry": geometry,
            }
        )
    columns = [
        "class_id",
        "class_name",
        "area_crs_units2",
        "mean_entropy",
        "mean_confidence",
        "review_priority",
        "geometry",
    ]
    if not records:
        return gpd.GeoDataFrame(columns=columns, geometry="geometry", crs=crs)
    return gpd.GeoDataFrame(records, geometry="geometry", crs=crs)
