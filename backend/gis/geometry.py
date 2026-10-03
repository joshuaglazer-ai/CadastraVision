"""Geometry helpers that need nothing beyond NumPy.

These run on the hot path (ingesting and serving the vector layers) so they
are kept free of GEOS: the map can be served on a machine where only the
light dependencies are installed, and the functions are easy to test.

Coordinates are GeoJSON order: ``[x, y]`` = ``[longitude, latitude]``.
Metric quantities are computed in the WGS 84 / UTM zone that contains the
geometry, never from raw degrees.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np

# WGS 84
_A = 6378137.0
_F = 1.0 / 298.257223563
_K0 = 0.9996
_N = _F / (2.0 - _F)
_A_BAR = _A / (1.0 + _N) * (1.0 + _N**2 / 4.0 + _N**4 / 64.0)
_ALPHA = (
    _N / 2.0 - 2.0 * _N**2 / 3.0 + 5.0 * _N**3 / 16.0,
    13.0 * _N**2 / 48.0 - 3.0 * _N**3 / 5.0,
    61.0 * _N**3 / 240.0,
)
_BETA = (
    _N / 2.0 - 2.0 * _N**2 / 3.0 + 37.0 * _N**3 / 96.0,
    _N**2 / 48.0 + _N**3 / 15.0,
    17.0 * _N**3 / 480.0,
)
_DELTA = (
    2.0 * _N - 2.0 * _N**2 / 3.0 - 2.0 * _N**3,
    7.0 * _N**2 / 3.0 - 8.0 * _N**3 / 5.0,
    56.0 * _N**3 / 15.0,
)

POLYGON_TYPES = ("Polygon", "MultiPolygon")


# --------------------------------------------------------------------------- UTM
def utm_zone(lon: float, lat: float) -> tuple[int, bool]:
    """Return ``(zone, northern)`` for a longitude/latitude."""

    zone = int(math.floor((float(lon) + 180.0) / 6.0)) + 1
    zone = min(max(zone, 1), 60)
    return zone, float(lat) >= 0.0


def utm_epsg(zone: int, northern: bool) -> int:
    return (32600 if northern else 32700) + int(zone)


def lonlat_to_utm(lon, lat, zone: int, northern: bool = True):
    """Forward transverse Mercator (Krüger series, sub-millimetre in-zone)."""

    lon = np.asarray(lon, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    lon0 = math.radians(zone * 6.0 - 183.0)
    phi = np.radians(lat)
    lam = np.radians(lon) - lon0

    c = 2.0 * math.sqrt(_N) / (1.0 + _N)
    t = np.sinh(np.arctanh(np.sin(phi)) - c * np.arctanh(c * np.sin(phi)))
    xi = np.arctan2(t, np.cos(lam))
    eta = np.arctanh(np.sin(lam) / np.sqrt(1.0 + t * t))

    easting = eta.copy()
    northing = xi.copy()
    for j, alpha in enumerate(_ALPHA, start=1):
        easting = easting + alpha * np.cos(2 * j * xi) * np.sinh(2 * j * eta)
        northing = northing + alpha * np.sin(2 * j * xi) * np.cosh(2 * j * eta)

    easting = 500000.0 + _K0 * _A_BAR * easting
    northing = _K0 * _A_BAR * northing
    if not northern:
        northing = northing + 10000000.0
    return easting, northing


def utm_to_lonlat(easting, northing, zone: int, northern: bool = True):
    """Inverse transverse Mercator (Krüger series)."""

    easting = np.asarray(easting, dtype=np.float64)
    northing = np.asarray(northing, dtype=np.float64)
    if not northern:
        northing = northing - 10000000.0

    xi = northing / (_K0 * _A_BAR)
    eta = (easting - 500000.0) / (_K0 * _A_BAR)

    xi_p = xi.copy()
    eta_p = eta.copy()
    for j, beta in enumerate(_BETA, start=1):
        xi_p = xi_p - beta * np.sin(2 * j * xi) * np.cosh(2 * j * eta)
        eta_p = eta_p - beta * np.cos(2 * j * xi) * np.sinh(2 * j * eta)

    chi = np.arcsin(np.sin(xi_p) / np.cosh(eta_p))
    phi = chi.copy()
    for j, delta in enumerate(_DELTA, start=1):
        phi = phi + delta * np.sin(2 * j * chi)

    lam = math.radians(zone * 6.0 - 183.0) + np.arctan2(np.sinh(eta_p), np.cos(xi_p))
    return np.degrees(lam), np.degrees(phi)


def web_mercator_to_lonlat(x, y):
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    lon = np.degrees(x / _A)
    lat = np.degrees(2.0 * np.arctan(np.exp(y / _A)) - math.pi / 2.0)
    return lon, lat


# ------------------------------------------------------------------- structure
def iter_polygons(geometry: dict[str, Any]) -> Iterable[list]:
    """Yield each polygon (list of rings) of a Polygon / MultiPolygon."""

    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "Polygon":
        yield coords
    elif kind == "MultiPolygon":
        for polygon in coords:
            yield polygon


def _ring_array(ring) -> np.ndarray:
    array = np.asarray(ring, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] < 2:
        raise ValueError("ring is not a list of [x, y] positions")
    return array[:, :2]


def _shoelace(x: np.ndarray, y: np.ndarray) -> float:
    return 0.5 * float(np.sum(x[:-1] * y[1:] - x[1:] * y[:-1]))


def _ring_length(x: np.ndarray, y: np.ndarray) -> float:
    return float(np.sum(np.hypot(np.diff(x), np.diff(y))))


# Problems that make a geometry unusable (as opposed to merely suspect).
FATAL_PROBLEMS = frozenset(
    {
        "geometry is missing",
        "geometry has no coordinates",
        "geometry is empty",
        "ring is malformed",
        "polygon has no rings",
        "ring contains non-finite coordinates",
        "coordinates fall outside longitude/latitude range",
        "exterior ring has fewer than 4 positions",
    }
)


def _douglas_peucker(x: np.ndarray, y: np.ndarray, tolerance: float) -> np.ndarray:
    """Indices kept by Douglas-Peucker on an open polyline (iterative)."""

    count = len(x)
    keep = np.zeros(count, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, count - 1)]
    while stack:
        start, end = stack.pop()
        if end - start < 2:
            continue
        x0, y0 = x[start], y[start]
        dx, dy = x[end] - x0, y[end] - y0
        px = x[start + 1 : end] - x0
        py = y[start + 1 : end] - y0
        norm = math.hypot(dx, dy)
        if norm == 0.0:
            distance = np.hypot(px, py)
        else:
            distance = np.abs(dx * py - dy * px) / norm
        index = int(np.argmax(distance))
        if distance[index] > tolerance:
            split = start + 1 + index
            keep[split] = True
            stack.append((start, split))
            stack.append((split, end))
    return np.nonzero(keep)[0]


def _simplify_closed_ring(x: np.ndarray, y: np.ndarray, tolerance: float) -> np.ndarray:
    """Indices kept for a closed ring (first == last)."""

    count = len(x)
    if count <= 5 or tolerance <= 0:
        return np.arange(count)
    # Split the ring at the vertex farthest from the start so that the two
    # halves are open polylines with distinct end points.
    far = int(np.argmax(np.hypot(x - x[0], y - y[0])))
    if far in (0, count - 1):
        return np.arange(count)
    first = _douglas_peucker(x[: far + 1], y[: far + 1], tolerance)
    second = _douglas_peucker(x[far:], y[far:], tolerance) + far
    return np.concatenate([first, second[1:]])


def analyse_geometry(
    geometry: Any,
    levels: Iterable[tuple[float, float, float]] = (),
    decimals: int = 7,
) -> dict[str, Any]:
    """Validate, measure and generalise a lon/lat polygon in one pass.

    ``levels`` is a sequence of ``(tolerance_m, min_hole_area_m2,
    min_part_area_m2)`` ordered from finest to coarsest; one simplified
    geometry (or ``None``) is returned per level. Each coarser level is
    derived from the vertices the previous level kept.

    Returns ``{"problems", "fatal", "bbox", "metrics", "simplified"}``.
    Structural problems are reported, never raised. A full OGC validity
    test (self-intersection) is left to Shapely in the processing pipeline.
    """

    levels = list(levels)
    result: dict[str, Any] = {
        "problems": [],
        "fatal": True,
        "bbox": None,
        "metrics": None,
        "simplified": [None] * len(levels),
    }
    problems: list[str] = result["problems"]

    if not isinstance(geometry, dict):
        problems.append("geometry is missing")
        return result
    kind = geometry.get("type")
    if kind not in POLYGON_TYPES:
        problems.append(f"unsupported geometry type '{kind}'")
        return result
    coords = geometry.get("coordinates")
    if not isinstance(coords, list) or not coords:
        problems.append("geometry has no coordinates")
        return result

    def note(problem: str) -> None:
        if problem not in problems:
            problems.append(problem)

    # ---- pass 1: structure and extent --------------------------------
    polygons: list[list[np.ndarray]] = []
    minx = miny = math.inf
    maxx = maxy = -math.inf
    for polygon in iter_polygons(geometry):
        if not polygon:
            note("polygon has no rings")
            continue
        rings: list[np.ndarray] = []
        for index, ring in enumerate(polygon):
            try:
                array = _ring_array(ring)
            except (ValueError, TypeError):
                note("ring is malformed")
                continue
            if len(array) < 4:
                note(
                    "exterior ring has fewer than 4 positions"
                    if index == 0
                    else "interior ring has fewer than 4 positions"
                )
                if index == 0:
                    rings = []
                    break
                continue
            if not np.all(np.isfinite(array)):
                note("ring contains non-finite coordinates")
                continue
            if array[0, 0] != array[-1, 0] or array[0, 1] != array[-1, 1]:
                note("ring is not closed")
                array = np.vstack([array, array[:1]])
            rings.append(array)
            if index == 0:
                minx = min(minx, float(array[:, 0].min()))
                maxx = max(maxx, float(array[:, 0].max()))
                miny = min(miny, float(array[:, 1].min()))
                maxy = max(maxy, float(array[:, 1].max()))
        if rings:
            polygons.append(rings)

    if not polygons:
        note("geometry is empty")
    if not math.isfinite(minx):
        return result
    if abs(minx) > 180.0 or abs(maxx) > 180.0 or abs(miny) > 90.0 or abs(maxy) > 90.0:
        note("coordinates fall outside longitude/latitude range")
    if any(problem in FATAL_PROBLEMS for problem in problems) and not polygons:
        return result
    if "coordinates fall outside longitude/latitude range" in problems:
        return result

    result["bbox"] = (minx, miny, maxx, maxy)
    zone, northern = utm_zone((minx + maxx) / 2.0, (miny + maxy) / 2.0)

    # ---- pass 2: measure and generalise in metres ---------------------
    area = perimeter = 0.0
    east_min = north_min = math.inf
    east_max = north_max = -math.inf
    vertices = ring_count = holes = 0
    outputs: list[list[list]] = [[] for _ in levels]

    for rings in polygons:
        parts: list[list | None] = [[] for _ in levels]
        for index, array in enumerate(rings):
            ring_count += 1
            vertices += len(array)
            east, north = lonlat_to_utm(array[:, 0], array[:, 1], zone, northern)
            ring_area = abs(_shoelace(east - east[0], north - north[0]))
            perimeter += _ring_length(east, north)
            if index == 0:
                area += ring_area
                if ring_area == 0.0:
                    note("exterior ring has zero area")
                east_min = min(east_min, float(east.min()))
                east_max = max(east_max, float(east.max()))
                north_min = min(north_min, float(north.min()))
                north_max = max(north_max, float(north.max()))
            else:
                area -= ring_area
                holes += 1

            kept = np.arange(len(array))
            for level, (tolerance, min_hole, min_part) in enumerate(levels):
                part = parts[level]
                if part is None:
                    continue
                if index == 0 and ring_area < min_part:
                    parts[level] = None
                    continue
                if index > 0 and ring_area < min_hole:
                    continue
                sub = _simplify_closed_ring(east[kept], north[kept], tolerance)
                kept = kept[sub]
                reduced = array[kept]
                if len(reduced) < 4:
                    if index > 0:
                        continue
                    reduced = array
                    kept = np.arange(len(array))
                ring_coords = np.round(reduced, decimals).tolist()
                if ring_coords[0] != ring_coords[-1]:
                    ring_coords.append(ring_coords[0])
                part.append(ring_coords)

        for level, part in enumerate(parts):
            if part:
                outputs[level].append(part)

    area = max(area, 0.0)
    compactness = (4.0 * math.pi * area / (perimeter * perimeter)) if perimeter > 0 else 0.0
    result["metrics"] = {
        "area_m2": area,
        "perimeter_m": perimeter,
        "width_m": max(east_max - east_min, 0.0),
        "length_m": max(north_max - north_min, 0.0),
        "compactness": min(compactness, 1.0),
        "vertices": vertices,
        "rings": ring_count,
        "holes": holes,
        "metric_crs": f"EPSG:{utm_epsg(zone, northern)}",
    }
    for level, polygons_out in enumerate(outputs):
        if not polygons_out:
            continue
        if len(polygons_out) == 1:
            result["simplified"][level] = {"type": "Polygon", "coordinates": polygons_out[0]}
        else:
            result["simplified"][level] = {"type": "MultiPolygon", "coordinates": polygons_out}

    result["fatal"] = False
    return result


def validate_geometry(geometry: Any) -> list[str]:
    """Structural problems of a lon/lat polygon (empty list when sound)."""

    return list(analyse_geometry(geometry)["problems"])


def geometry_bbox(geometry: dict[str, Any]) -> tuple[float, float, float, float]:
    minx = miny = math.inf
    maxx = maxy = -math.inf
    for polygon in iter_polygons(geometry):
        for ring in polygon:
            array = _ring_array(ring)
            minx = min(minx, float(array[:, 0].min()))
            maxx = max(maxx, float(array[:, 0].max()))
            miny = min(miny, float(array[:, 1].min()))
            maxy = max(maxy, float(array[:, 1].max()))
    if not math.isfinite(minx):
        raise ValueError("geometry has no coordinates")
    return minx, miny, maxx, maxy


def polygon_metrics(geometry: dict[str, Any]) -> dict[str, Any]:
    """Metric measurements of a longitude/latitude polygon.

    The geometry is projected to the UTM zone of its bounding-box centre.
    ``width_m`` is the east-west extent and ``length_m`` the north-south
    extent of the exterior, the same convention as the attributes shipped
    with the existing layers.
    """

    analysis = analyse_geometry(geometry)
    if analysis["metrics"] is None:
        raise ValueError("; ".join(analysis["problems"]) or "geometry cannot be measured")
    return analysis["metrics"]


def simplify_geometry(
    geometry: dict[str, Any],
    tolerance_m: float,
    min_hole_area_m2: float = 0.0,
    min_part_area_m2: float = 0.0,
    decimals: int = 7,
) -> dict[str, Any] | None:
    """Simplify a longitude/latitude polygon for display.

    Returns ``None`` when nothing of the geometry survives. The result is
    for drawing only; measurements and exports use the full geometry.
    """

    analysis = analyse_geometry(
        geometry, [(tolerance_m, min_hole_area_m2, min_part_area_m2)], decimals
    )
    return analysis["simplified"][0]


def geometry_center(geometry: dict[str, Any]) -> tuple[float, float]:
    """Centre of the bounding box, ``(lon, lat)``. Cheap and always defined."""

    minx, miny, maxx, maxy = geometry_bbox(geometry)
    return (minx + maxx) / 2.0, (miny + maxy) / 2.0


def bbox_intersects(a, b) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def count_vertices(geometry: dict[str, Any]) -> tuple[int, int]:
    """Return ``(vertices, rings)``."""

    vertices = rings = 0
    for polygon in iter_polygons(geometry):
        for ring in polygon:
            rings += 1
            vertices += len(ring)
    return vertices, rings


# ----------------------------------------------------------------- containment
def point_in_ring(px: float, py: float, ring) -> bool:
    """Even-odd rule point-in-polygon test for a single ring."""

    array = _ring_array(ring)
    x, y = array[:, 0], array[:, 1]
    x1, y1 = x[:-1], y[:-1]
    x2, y2 = x[1:], y[1:]
    crosses = (y1 > py) != (y2 > py)
    with np.errstate(divide="ignore", invalid="ignore"):
        at = (x2 - x1) * (py - y1) / (y2 - y1) + x1
    return bool(np.count_nonzero(crosses & (px < at)) % 2)


def point_in_geometry(px: float, py: float, geometry: dict[str, Any]) -> bool:
    for polygon in iter_polygons(geometry):
        if not polygon:
            continue
        if point_in_ring(px, py, polygon[0]) and not any(
            point_in_ring(px, py, hole) for hole in polygon[1:]
        ):
            return True
    return False


def bbox_polygon(minx: float, miny: float, maxx: float, maxy: float) -> dict[str, Any]:
    return {
        "type": "Polygon",
        "coordinates": [
            [[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]
        ],
    }
