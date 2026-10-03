"""Raster inspection and validation.

Uploads are checked before any processing starts so that an unsuitable file
is rejected with a message the surveyor can act on.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any


class RasterError(ValueError):
    """The raster cannot be used; the message says why."""


# Ground sample distance beyond which the SVAMITVA-trained model is being
# used well outside the imagery it was trained on.
COARSE_GSD_M = 0.30
MIN_DIMENSION = 64


def _require_rasterio():
    try:
        import rasterio  # noqa: F401
    except ImportError as exc:
        raise RasterError(
            "Raster support is unavailable: the 'rasterio' package is not installed."
        ) from exc
    import rasterio

    return rasterio


def _lonlat_bounds(crs, bounds) -> list[float] | None:
    from rasterio.warp import transform_bounds

    try:
        west, south, east, north = transform_bounds(crs, "EPSG:4326", *bounds, densify_pts=21)
    except Exception:
        return None
    values = [west, south, east, north]
    if not all(math.isfinite(v) for v in values):
        return None
    return [round(v, 8) for v in values]


def _metres_per_unit(crs, latitude: float | None) -> tuple[float | None, float | None]:
    """Approximate metres per CRS unit in x and y."""

    if crs is None:
        return None, None
    if crs.is_geographic:
        if latitude is None:
            return None, None
        return 111320.0 * math.cos(math.radians(latitude)), 110574.0
    try:
        factor = float(crs.linear_units_factor[1])
    except Exception:
        factor = 1.0
    return factor, factor


def inspect_raster(path: Path | str) -> dict[str, Any]:
    """Read georeferencing and band metadata without loading pixels."""

    rasterio = _require_rasterio()
    from rasterio.enums import ColorInterp

    path = Path(path)
    if not path.exists():
        raise RasterError(f"File not found: {path.name}")

    try:
        dataset = rasterio.open(path)
    except Exception as exc:
        raise RasterError(f"{path.name} could not be opened as a raster: {exc}") from exc

    with dataset as src:
        crs = src.crs
        bounds = list(src.bounds)
        transform = src.transform
        colorinterp = [ci.name for ci in src.colorinterp]

        rgb_bands = None
        try:
            indexes = {ci: i + 1 for i, ci in enumerate(src.colorinterp)}
            if all(c in indexes for c in (ColorInterp.red, ColorInterp.green, ColorInterp.blue)):
                rgb_bands = [
                    indexes[ColorInterp.red],
                    indexes[ColorInterp.green],
                    indexes[ColorInterp.blue],
                ]
        except Exception:
            rgb_bands = None
        if rgb_bands is None and src.count >= 3:
            rgb_bands = [1, 2, 3]

        alpha_band = None
        for i, ci in enumerate(src.colorinterp):
            if ci == ColorInterp.alpha:
                alpha_band = i + 1
                break

        lonlat = _lonlat_bounds(crs, src.bounds) if crs else None
        latitude = (lonlat[1] + lonlat[3]) / 2.0 if lonlat else None
        mx, my = _metres_per_unit(crs, latitude)
        res_x, res_y = abs(transform.a), abs(transform.e)

        epsg = None
        if crs:
            try:
                epsg = crs.to_epsg()
            except Exception:
                epsg = None

        meta: dict[str, Any] = {
            "file_name": path.name,
            "size_bytes": path.stat().st_size,
            "driver": src.driver,
            "width": int(src.width),
            "height": int(src.height),
            "band_count": int(src.count),
            "dtypes": list(src.dtypes),
            "color_interpretation": colorinterp,
            "rgb_bands": rgb_bands,
            "alpha_band": alpha_band,
            "nodata": src.nodata,
            "crs": (f"EPSG:{epsg}" if epsg else crs.to_string()) if crs else None,
            "crs_wkt": crs.to_wkt() if crs else None,
            "crs_is_geographic": bool(crs.is_geographic) if crs else None,
            "crs_units": (crs.linear_units if crs and crs.is_projected else ("degree" if crs else None)),
            "transform": [transform.a, transform.b, transform.c, transform.d, transform.e, transform.f],
            "is_north_up": bool(transform.b == 0 and transform.d == 0),
            "bounds": [float(v) for v in bounds],
            "bounds_lonlat": lonlat,
            "resolution": [res_x, res_y],
            "resolution_m": (
                [round(res_x * mx, 5), round(res_y * my, 5)] if mx is not None and my is not None else None
            ),
            "block_shapes": [list(shape) for shape in src.block_shapes[:1]],
            "pixels": int(src.width) * int(src.height),
        }
    return meta


def validate_for_inference(meta: dict[str, Any]) -> dict[str, list[str]]:
    """Return ``{"errors": [...], "warnings": [...]}`` for a raster's metadata."""

    errors: list[str] = []
    warnings: list[str] = []

    if not meta.get("crs"):
        errors.append(
            "The raster has no coordinate reference system. Georeference it "
            "(for example with gdal_translate -a_srs) before processing."
        )
    transform = meta.get("transform") or []
    if transform and transform[0] == 1.0 and transform[4] in (1.0, -1.0) and transform[2] == 0.0:
        errors.append("The raster has no georeferencing transform (pixel coordinates only).")
    if not meta.get("is_north_up", True):
        errors.append(
            "The raster is rotated (non north-up transform). Warp it to a north-up grid first."
        )

    if meta.get("band_count", 0) < 3 or not meta.get("rgb_bands"):
        errors.append(
            f"RGB imagery is required: found {meta.get('band_count', 0)} band(s)."
        )
    else:
        rgb_types = {meta["dtypes"][index - 1] for index in meta["rgb_bands"]}
        if rgb_types != {"uint8"}:
            errors.append(
                "The model expects 8-bit RGB. This raster is "
                + ", ".join(sorted(rgb_types))
                + ". Convert it first (for example gdal_translate -ot Byte -scale)."
            )

    width, height = meta.get("width", 0), meta.get("height", 0)
    if width < MIN_DIMENSION or height < MIN_DIMENSION:
        errors.append(f"The raster is too small ({width} x {height} px); at least {MIN_DIMENSION} px per side is needed.")

    resolution = meta.get("resolution_m")
    if resolution:
        gsd = max(resolution)
        if gsd > COARSE_GSD_M:
            warnings.append(
                f"Ground sample distance is about {gsd:.2f} m. The model was trained on "
                "centimetre-level drone orthoimagery, so results on coarser imagery are "
                "likely to degrade. Treat the output with extra caution."
            )
    else:
        warnings.append("Pixel size in metres could not be determined.")

    if meta.get("nodata") is None and not meta.get("alpha_band"):
        warnings.append(
            "No NoData value or alpha band is declared; pure black pixels (0, 0, 0) "
            "will be treated as NoData."
        )
    if meta.get("crs_is_geographic"):
        warnings.append(
            "The raster uses a geographic CRS; areas and lengths are computed after "
            "projecting to the local UTM zone."
        )

    return {"errors": errors, "warnings": warnings}
