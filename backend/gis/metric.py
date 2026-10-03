"""Metric measurements and reprojection for generated features.

Areas and lengths are never taken from geographic coordinates. A raster in
a projected CRS with metre units is measured in that CRS; anything else is
projected to the WGS 84 / UTM zone that contains it.

Requires pyproj and Shapely (both come with GeoPandas / Rasterio).
"""

from __future__ import annotations

import math
from typing import Any, Callable

from backend.gis.geometry import utm_epsg, utm_zone


def _pyproj_crs(crs):
    from pyproj import CRS

    if crs is None:
        raise ValueError("A coordinate reference system is required.")
    if hasattr(crs, "to_wkt"):
        return CRS.from_wkt(crs.to_wkt())
    return CRS.from_user_input(crs)


def choose_metric_crs(crs, bounds) -> Any:
    """Return a pyproj CRS suitable for measuring ``bounds`` (in ``crs``)."""

    from pyproj import CRS, Transformer

    source = _pyproj_crs(crs)
    if source.is_projected:
        unit = (source.axis_info[0].unit_name or "").lower()
        if unit in ("metre", "meter"):
            return source

    to_lonlat = Transformer.from_crs(source, "EPSG:4326", always_xy=True)
    centre_x = (bounds[0] + bounds[2]) / 2.0
    centre_y = (bounds[1] + bounds[3]) / 2.0
    lon, lat = to_lonlat.transform(centre_x, centre_y)
    if not (math.isfinite(lon) and math.isfinite(lat)):
        raise ValueError("The raster extent could not be located on the globe.")
    zone, northern = utm_zone(lon, lat)
    return CRS.from_epsg(utm_epsg(zone, northern))


def transformer(source, target) -> Callable:
    """A function for ``shapely.ops.transform`` from ``source`` to ``target``."""

    from pyproj import Transformer

    source = _pyproj_crs(source)
    target = _pyproj_crs(target)
    if source == target:
        return lambda x, y, z=None: (x, y)
    return Transformer.from_crs(source, target, always_xy=True).transform


def crs_label(crs) -> str:
    crs = _pyproj_crs(crs)
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg else crs.name


def metric_properties(geometry) -> dict[str, Any]:
    """Measurements of a polygon already expressed in a metric CRS.

    ``width_m`` is the east-west and ``length_m`` the north-south extent,
    the convention used by the layers that ship with the project.
    """

    minx, miny, maxx, maxy = geometry.bounds
    area = float(geometry.area)
    perimeter = float(geometry.length)

    polygons = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
    rings = sum(1 + len(polygon.interiors) for polygon in polygons)
    vertices = sum(
        len(polygon.exterior.coords) + sum(len(ring.coords) for ring in polygon.interiors)
        for polygon in polygons
    )
    compactness = (4.0 * math.pi * area / (perimeter * perimeter)) if perimeter > 0 else 0.0
    return {
        "area_m2": area,
        "perimeter_m": perimeter,
        "width_m": float(maxx - minx),
        "length_m": float(maxy - miny),
        "compactness": min(compactness, 1.0),
        "ring_count": rings,
        "hole_count": rings - len(polygons),
        "vertex_count": vertices,
    }
