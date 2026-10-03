"""Geometry validation and repair for generated features.

Every geometry ends with an explicit ``geometry_status``:

``VALID``     passed the OGC validity test untouched
``REPAIRED``  was invalid and was fixed with ``make_valid``
``INVALID``   is still invalid after repair (kept, flagged for review)
``EMPTY``     has no polygonal area left (reported, removed from the layer)

Nothing is dropped without being counted in the report.
"""

from __future__ import annotations

import json
from typing import Any

POLYGONAL = ("Polygon", "MultiPolygon")


def _polygonal_part(geometry):
    """Keep the polygonal parts of a geometry (``make_valid`` can return a
    collection that also contains lines or points)."""

    from shapely.ops import unary_union

    if geometry is None or geometry.is_empty:
        return None
    if geometry.geom_type in POLYGONAL:
        return geometry
    if hasattr(geometry, "geoms"):
        parts = [g for g in geometry.geoms if g.geom_type in POLYGONAL and not g.is_empty]
        if parts:
            return unary_union(parts)
    return None


def repair_geometry(geometry):
    """Repair an invalid geometry where possible.

    Returns ``(geometry_or_None, status)``.
    """

    if geometry is None or geometry.is_empty:
        return None, "EMPTY"
    if geometry.is_valid:
        return geometry, "VALID"

    repaired = None
    try:
        from shapely.validation import make_valid

        repaired = _polygonal_part(make_valid(geometry))
    except Exception:
        repaired = None

    if repaired is None or repaired.is_empty or not repaired.is_valid:
        try:
            repaired = _polygonal_part(geometry.buffer(0))
        except Exception:
            repaired = None

    if repaired is None or repaired.is_empty:
        return None, "EMPTY"
    if not repaired.is_valid:
        return geometry, "INVALID"
    return repaired, "REPAIRED"


def validate_geometries(gdf):
    """Repair geometries; add ``geometry_status``; return ``(gdf, report)``."""

    gdf = gdf.copy()
    statuses: list[str] = []
    geometries = []
    for geometry in gdf.geometry:
        fixed, status = repair_geometry(geometry)
        geometries.append(fixed)
        statuses.append(status)

    gdf["geometry"] = geometries
    gdf["geometry_status"] = statuses

    counts = {status: statuses.count(status) for status in ("VALID", "REPAIRED", "INVALID", "EMPTY")}
    return gdf, {
        "original_invalid": counts["REPAIRED"] + counts["INVALID"] + counts["EMPTY"],
        "repaired": counts["REPAIRED"],
        "remaining_invalid": counts["INVALID"],
        "empty_geometries": counts["EMPTY"],
    }


def detect_overlaps(gdf, min_overlap_area: float = 0.0) -> list[dict[str, Any]]:
    """Pairs of features whose interiors overlap.

    Returns ``[{"index_a", "index_b", "overlap_area"}]`` using the frame's
    index labels. Areas are in the frame's CRS units squared.
    """

    import numpy as np
    import shapely

    if gdf is None or gdf.empty:
        return []
    working = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    if len(working) < 2:
        return []

    geometries = np.asarray(working.geometry.values)
    labels = list(working.index)
    # Shapely's own tree: the array form of the query behaves the same on
    # every Shapely 2.x release, unlike the GeoPandas wrapper.
    left, right = shapely.STRtree(geometries).query(geometries, predicate="intersects")
    keep = left < right
    left, right = left[keep], right[keep]
    if len(left) == 0:
        return []

    areas = shapely.area(shapely.intersection(geometries[left], geometries[right]))
    overlaps = []
    for a, b, area in zip(left, right, areas):
        if area > min_overlap_area:
            overlaps.append(
                {"index_a": labels[int(a)], "index_b": labels[int(b)], "overlap_area": float(area)}
            )
    return overlaps


def flag_slivers(gdf, area_threshold: float = 1.0, area_column: str | None = None):
    """Flag very small polygons.

    ``area_threshold`` is in the units of ``area_column`` (square metres when
    that column holds metric areas) or, without one, CRS units squared.
    """

    gdf = gdf.copy()
    areas = gdf[area_column] if area_column and area_column in gdf.columns else gdf.geometry.area
    gdf["is_sliver"] = areas < area_threshold
    return gdf


def run_topology_validation(gdf, sliver_area_threshold: float = 1.0, area_column: str | None = None):
    """Complete geometry / topology validation.

    Returns ``(validated_gdf, report)``. Features whose geometry is EMPTY
    after repair are removed from the frame and listed in
    ``report["removed"]``.
    """

    if gdf is None:
        raise ValueError("GeoDataFrame cannot be None.")

    gdf, geometry_report = validate_geometries(gdf)

    empty = gdf["geometry_status"] == "EMPTY"
    removed = []
    if empty.any():
        id_column = "feature_id" if "feature_id" in gdf.columns else None
        for label, row in gdf[empty].iterrows():
            removed.append(
                {
                    "feature": str(row[id_column]) if id_column else str(label),
                    "class_name": row.get("class_name"),
                    "reason": "no polygonal area left after geometry repair",
                }
            )
        gdf = gdf[~empty].copy()

    gdf = flag_slivers(gdf, area_threshold=sliver_area_threshold, area_column=area_column)

    overlap_records = detect_overlaps(gdf)
    gdf["has_overlap"] = False
    for record in overlap_records:
        for label in (record["index_a"], record["index_b"]):
            if label in gdf.index:
                gdf.loc[label, "has_overlap"] = True

    gdf["topology_status"] = "PASS"
    gdf.loc[gdf["is_sliver"], "topology_status"] = "REVIEW"
    gdf.loc[gdf["has_overlap"], "topology_status"] = "REVIEW"
    gdf.loc[gdf["geometry_status"].isin(["REPAIRED", "INVALID"]), "topology_status"] = "REVIEW"

    report = {
        "total_features": int(len(gdf)),
        "original_invalid": geometry_report["original_invalid"],
        "repaired": geometry_report["repaired"],
        "remaining_invalid": geometry_report["remaining_invalid"],
        "empty_geometries": geometry_report["empty_geometries"],
        "removed": removed,
        "overlap_pairs": int(len(overlap_records)),
        "features_with_overlap": int(gdf["has_overlap"].sum()),
        "sliver_features": int(gdf["is_sliver"].sum()),
        "review_features": int((gdf["topology_status"] == "REVIEW").sum()),
    }
    return gdf, report


def save_topology_report(report: dict[str, Any], output_path) -> str:
    """Save the topology report as JSON."""

    output_path = str(output_path)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)
    return output_path
