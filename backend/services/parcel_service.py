"""Parcel reasoning: what the AI features say about one parcel.

Two situations are supported, and the response says which one applies.

``existing_gis``  The parcel comes from an existing GIS / land-records layer.
                  AI features are overlaid on it (spatial overlay).
``candidate``     No authoritative parcel layer exists. The parcel is a
                  candidate derived from AI segmentation.

In both, the result describes the land cover inside the parcel's outer
boundary: buildings, roads, water and other features, and road access. It
never states ownership or a legal boundary.

With Shapely installed the overlay is an exact polygon intersection. On a
machine without it, a containment test on the same data is used and the
response says so.
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np

from backend.ai.classes import CLASS_NAMES, KEY_TO_ID
from backend.config import Settings
from backend.core import runtime
from backend.gis.geometry import (
    iter_polygons,
    lonlat_to_utm,
    polygon_metrics,
    simplify_geometry,
    utm_zone,
)

_cache: dict[tuple, dict[str, Any]] = {}
_cache_lock = threading.Lock()
_CACHE_LIMIT = 512


def _envelope(geometry: dict[str, Any]) -> dict[str, Any]:
    """The parcel without its interior holes (outer boundaries only)."""

    exteriors = [[polygon[0]] for polygon in iter_polygons(geometry) if polygon]
    if len(exteriors) == 1:
        return {"type": "Polygon", "coordinates": exteriors[0]}
    return {"type": "MultiPolygon", "coordinates": exteriors}


def _overlay_shapely(envelope, candidates, zone, northern):
    """Exact intersection areas in the parcel's UTM zone."""

    from shapely.geometry import shape
    from shapely.ops import transform as shapely_transform

    def project(x, y, z=None):
        return lonlat_to_utm(np.asarray(x), np.asarray(y), zone, northern)

    parcel = shapely_transform(project, shape(envelope))
    if not parcel.is_valid:
        parcel = parcel.buffer(0)

    results = []
    for feature in candidates:
        geometry = shapely_transform(project, shape(feature["geometry"]))
        if not geometry.is_valid:
            geometry = geometry.buffer(0)
        if geometry.is_empty or not parcel.intersects(geometry):
            continue
        inside = float(parcel.intersection(geometry).area)
        if inside <= 0.0:
            continue
        results.append((feature, inside, float(geometry.area)))
    return results, "Polygon overlay (Shapely), measured in the parcel's UTM zone"


def _points_in_ring(points: np.ndarray, ring: np.ndarray) -> np.ndarray:
    """Even-odd test of several points against one ring, vectorised."""

    x1, y1 = ring[:-1, 0], ring[:-1, 1]
    x2, y2 = ring[1:, 0], ring[1:, 1]
    px = points[:, 0][:, None]
    py = points[:, 1][:, None]
    crosses = (y1 > py) != (y2 > py)
    with np.errstate(divide="ignore", invalid="ignore"):
        at = (x2 - x1) * (py - y1) / (y2 - y1) + x1
    return (np.count_nonzero(crosses & (px < at), axis=1) % 2).astype(bool)


def _overlay_numpy(envelope, candidates):
    """Containment test: a feature counts as inside when most of the sampled
    vertices of its outer ring lie within the parcel's outer boundary."""

    simplified = simplify_geometry(envelope, 0.4) or envelope
    exteriors = [
        np.asarray(polygon[0], dtype=np.float64)[:, :2]
        for polygon in iter_polygons(simplified)
        if polygon
    ]
    results = []
    for feature in candidates:
        rings = [polygon[0] for polygon in iter_polygons(feature["geometry"]) if polygon]
        if not rings:
            continue
        vertices = np.asarray(rings[0], dtype=np.float64)[:-1, :2]
        if len(vertices) > 8:
            vertices = vertices[:: max(1, len(vertices) // 8)][:8]
        inside = np.zeros(len(vertices), dtype=bool)
        for exterior in exteriors:
            inside |= _points_in_ring(vertices, exterior)
        if int(inside.sum()) * 2 > len(vertices):
            area = float(feature["properties"].get("area_m2") or 0.0)
            results.append((feature, area, area))
    return results, "Containment test (NumPy); install Shapely for exact overlay areas"


def reason(
    parcel: dict[str, Any],
    *,
    source: str,
    settings: Settings,
    scenario: str = "candidate",
) -> dict[str, Any]:
    """Describe the AI land cover inside ``parcel`` (a GeoJSON feature with a
    lon/lat geometry). Results are cached per parcel."""

    layers = runtime.get_layers()
    record = layers.layer(source, "landcover")
    uid = str(parcel.get("id") or (parcel.get("properties") or {}).get("uid") or "")
    key = (source, uid, scenario, (record or {}).get("ingested_at"))
    with _cache_lock:
        if uid and key in _cache:
            return _cache[key]

    base: dict[str, Any] = {
        "scenario": scenario,
        "scenario_label": (
            "Existing GIS parcel with AI features overlaid"
            if scenario == "existing_gis"
            else "Candidate parcel derived from AI segmentation (no authoritative parcel layer)"
        ),
        "available": False,
        "message": None,
        "disclaimer": (
            "Spatial reasoning over AI-generated features. Preliminary; not a "
            "legal cadastral ownership record."
        ),
    }

    # Reason on the 5 cm geometry of the parcel, whatever level the caller
    # happened to load for display.
    stored = layers.get(source, uid, kind="parcels", geometry="detail") if uid else None
    geometry = (stored or {}).get("geometry") or parcel.get("geometry")
    if stored and not parcel.get("bbox"):
        parcel = {**parcel, "bbox": stored.get("bbox")}
    if record is None or not geometry:
        base["message"] = "Land-cover layer unavailable for this source."
        return base

    try:
        envelope = _envelope(geometry)
        envelope_metrics = polygon_metrics(envelope)
        box = parcel.get("bbox")
        if not box:
            from backend.gis.geometry import geometry_bbox

            box = geometry_bbox(envelope)

        # Everything inside the outer boundary is described, including the
        # parcel's own Field region.
        candidates, _total = layers.query(
            source,
            "landcover",
            bbox=tuple(box),
            geometry="detail",
            order="area_desc",
            limit=20000,
        )
        zone, northern = utm_zone((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)

        try:
            overlaps, method = _overlay_shapely(envelope, candidates, zone, northern)
        except ImportError:
            overlaps, method = _overlay_numpy(envelope, candidates)

        composition: dict[str, dict[str, Any]] = {}
        buildings = []
        fragments = 0

        # The parcel's own region is counted once, from its own attributes.
        # Its twin in the land-cover layer (same extent and area) is skipped.
        parcel_properties = parcel.get("properties") or {}
        own_area = float(parcel_properties.get("area_m2") or 0.0)
        own_class = parcel_properties.get("class_key") or "field"
        if own_area > 0:
            composition[own_class] = {
                "class_key": own_class,
                "class_name": CLASS_NAMES.get(KEY_TO_ID.get(own_class, -1), own_class.title()),
                "features": 1,
                "area_m2": own_area,
            }

        def is_twin(feature: dict[str, Any]) -> bool:
            properties = feature["properties"]
            if properties.get("class_key") != own_class or own_area <= 0:
                return False
            other_box = feature.get("bbox") or []
            if len(other_box) != 4 or any(abs(a - b) > 1e-7 for a, b in zip(other_box, box)):
                return False
            return abs(float(properties.get("area_m2") or 0.0) - own_area) <= 0.001 * own_area

        for feature, inside_area, _full_area in overlaps:
            if is_twin(feature):
                continue
            properties = feature["properties"]
            class_key = properties.get("class_key") or "unknown"
            entry = composition.setdefault(
                class_key,
                {
                    "class_key": class_key,
                    "class_name": CLASS_NAMES.get(KEY_TO_ID.get(class_key, -1), class_key.title()),
                    "features": 0,
                    "area_m2": 0.0,
                },
            )
            entry["features"] += 1
            entry["area_m2"] += inside_area
            if class_key == "building":
                if (properties.get("area_m2") or 0.0) < settings.sliver_area_m2:
                    fragments += 1
                else:
                    buildings.append(
                        {
                            "uid": properties.get("uid"),
                            "area_m2": round(float(properties.get("area_m2") or 0.0), 2),
                            "area_inside_m2": round(inside_area, 2),
                            "confidence": properties.get("confidence"),
                        }
                    )

        envelope_area = envelope_metrics["area_m2"]
        for entry in composition.values():
            entry["area_m2"] = round(entry["area_m2"], 2)
            entry["share"] = round(entry["area_m2"] / envelope_area, 4) if envelope_area > 0 else None

        buildings.sort(key=lambda b: -b["area_m2"])
        ordered = sorted(composition.values(), key=lambda e: -e["area_m2"])

        base.update(
            {
                "available": True,
                "method": method,
                "geometry_basis": "5 cm generalised geometry of the indexed layers",
                "envelope_area_m2": round(envelope_area, 2),
                "interior_hole_count": (parcel.get("properties") or {}).get("hole_count"),
                "composition": ordered,
                "building_count": len(buildings),
                "building_fragment_count": fragments,
                "building_area_m2": round(sum(b["area_inside_m2"] for b in buildings), 2),
                "buildings": buildings[:25],
                "road_feature_count": composition.get("road", {}).get("features", 0),
                "road_area_m2": composition.get("road", {}).get("area_m2", 0.0),
                "water_area_m2": composition.get("water", {}).get("area_m2", 0.0),
            }
        )
    except Exception as exc:  # reasoning must never take the panel down
        base["message"] = f"Spatial reasoning could not be completed: {exc}"
        return base

    with _cache_lock:
        if len(_cache) >= _CACHE_LIMIT:
            _cache.clear()
        if uid:
            _cache[key] = base
    return base


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
