"""Measured terrain and building height from DSM and DTM rasters.

    building height = DSM elevation − DTM elevation

Heights are only ever computed from real rasters found in ``data/dsm`` and
``data/dtm``. When either is missing the service says so; it never derives
a height from footprint area or any other stand-in.
"""

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any

import numpy as np

from backend.config import Settings
from backend.core import runtime
from backend.services import dataset_service

REQUIRED_MESSAGE = "DSM/DTM data required for measured terrain and building height."
MAX_GRID = 192
MAX_BUILDINGS = 1500

_cache: dict[tuple, dict[str, Any]] = {}
_cache_lock = threading.Lock()


class TerrainError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _stamp(path: Path | None) -> tuple | None:
    if path is None:
        return None
    stat = path.stat()
    return (str(path), stat.st_mtime, stat.st_size)


def status(settings: Settings) -> dict[str, Any]:
    """Which elevation rasters exist and whether heights can be measured."""

    dsm = dataset_service.first_raster("dsm", settings)
    dtm = dataset_service.first_raster("dtm", settings)

    def describe(path: Path | None) -> dict[str, Any]:
        if path is None:
            return {"available": False, "message": "Dataset unavailable"}
        meta = dataset_service.raster_metadata(path)
        if not meta.get("ok"):
            return {"available": False, "name": path.name, "message": meta.get("error")}
        if not meta.get("crs"):
            return {
                "available": False,
                "name": path.name,
                "message": "The raster has no coordinate reference system.",
            }
        return {
            "available": True,
            "name": path.name,
            "crs": meta.get("crs"),
            "resolution_m": meta.get("resolution_m"),
            "extent": meta.get("bounds_lonlat"),
            "width": meta.get("width"),
            "height": meta.get("height"),
        }

    dsm_info, dtm_info = describe(dsm), describe(dtm)
    heights = dsm_info["available"] and dtm_info["available"]
    overlap = None
    if heights:
        a, b = dsm_info["extent"], dtm_info["extent"]
        if a and b:
            box = [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]
            overlap = box if box[0] < box[2] and box[1] < box[3] else None
        if overlap is None:
            heights = False

    if heights:
        message = None
    elif dsm_info["available"] and dtm_info["available"]:
        message = "The DSM and DTM do not overlap, so heights cannot be measured."
    else:
        message = REQUIRED_MESSAGE

    return {
        "dsm": dsm_info,
        "dtm": dtm_info,
        "terrain_available": dsm_info["available"] or dtm_info["available"],
        "heights_available": bool(heights),
        "overlap_extent": overlap,
        "message": message,
        "method": "Building height = DSM elevation − DTM elevation, sampled inside each footprint.",
    }


def _read_grid(path: Path, bounds_lonlat, size: int) -> dict[str, Any]:
    """Resample a raster to a lon/lat grid of at most ``size`` cells a side."""

    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT
    from rasterio.windows import from_bounds

    west, south, east, north = bounds_lonlat
    with rasterio.open(path) as src:
        with WarpedVRT(src, crs="EPSG:4326", resampling=Resampling.bilinear) as vrt:
            west = max(west, vrt.bounds.left)
            east = min(east, vrt.bounds.right)
            south = max(south, vrt.bounds.bottom)
            north = min(north, vrt.bounds.top)
            if west >= east or south >= north:
                raise TerrainError("The elevation raster does not cover the requested area.", 404)

            window = from_bounds(west, south, east, north, vrt.transform)
            aspect = (east - west) * math.cos(math.radians((south + north) / 2.0)) / (north - south)
            if aspect >= 1:
                cols, rows = size, max(2, int(round(size / aspect)))
            else:
                cols, rows = max(2, int(round(size * aspect))), size
            cols = min(cols, max(2, int(math.ceil(window.width))))
            rows = min(rows, max(2, int(math.ceil(window.height))))

            data = vrt.read(
                1, window=window, out_shape=(rows, cols), resampling=Resampling.bilinear, masked=True
            )

    values = np.ma.filled(data.astype(np.float64), np.nan)
    finite = np.isfinite(values)
    if not finite.any():
        raise TerrainError("The elevation raster has no valid data in the requested area.", 404)
    return {
        "bounds": [west, south, east, north],
        "rows": int(rows),
        "cols": int(cols),
        "min": float(np.nanmin(values)),
        "max": float(np.nanmax(values)),
        # Row 0 is the northern edge. Missing cells are null.
        "values": [
            [None if not math.isfinite(v) else round(float(v), 3) for v in row] for row in values
        ],
    }


def grid(settings: Settings, bounds_lonlat, surface: str = "dtm", size: int = 128) -> dict[str, Any]:
    """Elevation grid for the 3D view (``surface`` is ``dtm`` or ``dsm``)."""

    if surface not in ("dtm", "dsm"):
        raise TerrainError("surface must be 'dtm' or 'dsm'.")
    path = dataset_service.first_raster(surface, settings)
    if path is None:
        raise TerrainError(f"{surface.upper()} dataset unavailable.", 404)
    size = max(8, min(int(size), MAX_GRID))

    key = ("grid", surface, _stamp(path), tuple(round(v, 7) for v in bounds_lonlat), size)
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    try:
        result = _read_grid(path, bounds_lonlat, size)
    except ImportError as exc:
        raise TerrainError("Raster support (rasterio) is not installed.", 501) from exc
    result.update(surface=surface, dataset=path.name, unit="m", measured=True)
    with _cache_lock:
        _cache[key] = result
    return result


def _zonal(src, geometry, transform_to_raster) -> np.ndarray | None:
    """Pixel values of ``src`` inside a lon/lat geometry (None if no overlap)."""

    from rasterio.features import geometry_mask
    from rasterio.windows import Window, from_bounds
    from shapely.geometry import mapping, shape
    from shapely.ops import transform as shapely_transform

    footprint = shapely_transform(transform_to_raster, shape(geometry))
    if footprint.is_empty:
        return None
    minx, miny, maxx, maxy = footprint.bounds
    try:
        window = from_bounds(minx, miny, maxx, maxy, src.transform).round_offsets().round_lengths()
        window = window.intersection(Window(0, 0, src.width, src.height))
    except Exception:  # rasterio raises WindowError when there is no overlap
        return None
    if window.width < 1 or window.height < 1:
        return None

    data = src.read(1, window=window, masked=True)
    inside = geometry_mask(
        [mapping(footprint)],
        out_shape=data.shape,
        transform=src.window_transform(window),
        invert=True,
        all_touched=False,
    )
    values = np.ma.masked_array(data, mask=np.ma.getmaskarray(data) | ~inside).compressed()
    return values if values.size else None


def building_heights(settings: Settings, source: str, bounds_lonlat=None) -> dict[str, Any]:
    """Measured height of every building footprint: DSM − DTM."""

    state = status(settings)
    if not state["heights_available"]:
        return {"available": False, "message": state["message"], "buildings": []}

    dsm_path = dataset_service.first_raster("dsm", settings)
    dtm_path = dataset_service.first_raster("dtm", settings)
    layers = runtime.get_layers()
    runtime.ensure_source(source)
    record = layers.layer(source, "landcover")
    if record is None:
        return {"available": False, "message": "No building footprints: land-cover layer unavailable.", "buildings": []}

    key = (
        "heights",
        source,
        record.get("ingested_at"),
        _stamp(dsm_path),
        _stamp(dtm_path),
        tuple(round(v, 7) for v in bounds_lonlat) if bounds_lonlat else None,
    )
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    try:
        import rasterio
        from pyproj import Transformer
    except ImportError as exc:
        raise TerrainError("Raster support (rasterio) is not installed.", 501) from exc

    footprints, total = layers.query(
        source,
        "landcover",
        classes=["building"],
        bbox=bounds_lonlat,
        min_area=settings.sliver_area_m2,
        geometry="detail",
        order="area_desc",
        limit=MAX_BUILDINGS,
    )

    buildings = []
    without_data = 0
    with rasterio.open(dsm_path) as dsm, rasterio.open(dtm_path) as dtm:
        to_dsm = Transformer.from_crs("EPSG:4326", dsm.crs.to_wkt(), always_xy=True).transform
        to_dtm = Transformer.from_crs("EPSG:4326", dtm.crs.to_wkt(), always_xy=True).transform
        for feature in footprints:
            surface = _zonal(dsm, feature["geometry"], to_dsm)
            ground = _zonal(dtm, feature["geometry"], to_dtm)
            if surface is None or ground is None:
                without_data += 1
                continue
            ground_elevation = float(np.median(ground))
            roof = float(np.percentile(surface, 90))
            height = roof - ground_elevation
            buildings.append(
                {
                    "uid": feature["id"],
                    "area_m2": feature["properties"].get("area_m2"),
                    "height_m": round(height, 2),
                    "mean_height_m": round(float(np.mean(surface)) - ground_elevation, 2),
                    "ground_elevation_m": round(ground_elevation, 2),
                    "roof_elevation_m": round(roof, 2),
                    "dsm_samples": int(surface.size),
                    "dtm_samples": int(ground.size),
                    "geometry": feature["geometry"],
                }
            )

    result = {
        "available": True,
        "message": None,
        "method": (
            "height = 90th percentile of DSM inside the footprint − median of DTM inside the footprint"
        ),
        "dsm": dsm_path.name,
        "dtm": dtm_path.name,
        "measured": len(buildings),
        "without_elevation_samples": without_data,
        "footprints_considered": len(footprints),
        "footprints_total": total,
        "buildings": buildings,
    }
    with _cache_lock:
        if len(_cache) > 64:
            _cache.clear()
        _cache[key] = result
    return result
