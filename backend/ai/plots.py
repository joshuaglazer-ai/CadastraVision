"""Candidate plots: morphological tessellation around detected buildings.

Inside a settlement, candidate parcels (contiguous open land) say nothing
about plots. Where no cadastre exists, morphological tessellation
(Fleischmann et al., 2020, "Morphological tessellation as a way of
partitioning space", Computers, Environment and Urban Systems 80) is a
published proxy: every piece of land goes to its nearest building
footprint, within a distance limit.

This runs after a job has completed, on its outputs. It never touches the
segmentation, the polygons or their measurements; it writes one more layer,
``candidate_plots.geojson``.

How it divides the land
-----------------------
* Seeds: the job's Building features of at least ``min_building_m2``.
* Land: valid image pixels (not NoData) that are not Road or Water.
* On a grid of about ``grid_m`` (10 cm by default) every seed grows outward
  one cell at a time through land only, alternating 4- and 8-neighbour steps
  (an octagonal approximation of Euclidean distance; a diagonal step is
  allowed only when both cells it passes between are land, so a barrier one
  cell wide cannot be slipped through). Each cell goes to the first seed that
  reaches it. Roads and water are therefore walls even where the detected
  network does not close into blocks.
* Each region is polygonised, measured in the local UTM zone, cut to the
  exact ``limit_m`` buffer of its own building, and has the road and water
  polygons subtracted exactly. Regions are disjoint by construction.

The boundaries are a geometric proposal, not something the model saw: every
plot starts as review required, with that reason.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

DELINEATION_METHOD = "morphological_tessellation"
PLOT_LABEL = "CANDIDATE PLOT / AI GENERATED / PRELIMINARY"
PLOT_REVIEW_REASON = (
    "Boundary proposed by geometric subdivision around a detected building; not observed in imagery"
)
LOW_COVERAGE = 0.05  # building area / plot area below this is flagged
LOW_COVERAGE_REASON = "Building covers under 5% of this plot; the building or the plot may not be real"
NODATA_CLASS = 255
ROAD_CLASS_ID, WATER_CLASS_ID, BUILDING_CLASS_ID = 3, 4, 2
PLOTS_FILE = "candidate_plots.geojson"
SUMMARY_FILE = "plots_summary.json"


class PlotError(RuntimeError):
    pass


# ------------------------------------------------------------------ helpers
def _to_crs(geometries, source: str, target) -> list:
    """Reproject shapely geometries between CRSs (vectorised)."""

    import shapely
    from pyproj import Transformer

    transformer = Transformer.from_crs(source, target, always_xy=True)

    def apply(coords):
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])

    return [shapely.transform(g, apply) for g in geometries]


def _slices(dy: int, dx: int, h: int, w: int):
    """(target, source) slices so that ``target`` cells receive the cell
    ``(dy, dx)`` away from them, i.e. ``out[y, x] <- in[y - dy, x - dx]``."""

    target = (slice(max(dy, 0), h + min(dy, 0)), slice(max(dx, 0), w + min(dx, 0)))
    source = (slice(max(-dy, 0), h + min(-dy, 0)), slice(max(-dx, 0), w + min(-dx, 0)))
    return target, source


def grow_regions(labels: np.ndarray, land: np.ndarray, steps: int) -> np.ndarray:
    """Grow every labelled seed through ``land`` for ``steps`` iterations.

    ``labels`` holds seed ids (> 0) and 0 elsewhere; it is not modified.
    Iterations alternate 4-neighbour and 8-neighbour moves. A diagonal move
    needs both orthogonal cells it passes between to be land. Works on
    slice views, so memory stays a few times the grid size.
    """

    labels = labels.copy()
    land = land | (labels > 0)
    h, w = labels.shape
    orthogonal = [(0, 1), (0, -1), (1, 0), (-1, 0)]
    diagonal = [(1, 1), (1, -1), (-1, 1), (-1, -1)]
    for step in range(steps):
        before = labels.copy()
        free = (labels == 0) & land
        if not free.any():
            break
        changed = False
        for dy, dx in orthogonal + (diagonal if step % 2 else []):
            target, source = _slices(dy, dx, h, w)
            incoming = before[source]
            take = free[target] & (incoming > 0)
            if dy and dx:
                # No corner cutting: the two cells passed between must be land.
                t_vertical, s_vertical = _slices(dy, 0, h, w)
                t_horizontal, s_horizontal = _slices(0, dx, h, w)
                via_vertical = np.zeros_like(land)
                via_vertical[t_vertical] = land[s_vertical]
                via_horizontal = np.zeros_like(land)
                via_horizontal[t_horizontal] = land[s_horizontal]
                take &= via_vertical[target] & via_horizontal[target]
                del via_vertical, via_horizontal
            if take.any():
                labels[target][take] = incoming[take]
                free[target] &= ~take
                changed = True
        del before
        if not changed:
            break
    return labels


# --------------------------------------------------------------- main step
def build_plots(
    output_dir: Path | str,
    *,
    limit_m: float = 25.0,
    grid_m: float = 0.10,
    min_building_m2: float = 5.0,
    road_access_distance_m: float = 5.0,
    sliver_area_m2: float = 1.0,
    job_id: str | None = None,
    model: dict[str, Any] | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Build ``candidate_plots.geojson`` for a completed job's outputs.

    Returns a summary (also written to ``plots_summary.json``).
    """

    import rasterio
    import shapely
    from affine import Affine
    from rasterio.enums import Resampling
    from rasterio.features import rasterize, shapes
    from shapely.geometry import mapping, shape

    from backend.ai.pipeline import geometry_to_geojson
    from backend.ai.topology import run_topology_validation
    from backend.gis.metric import choose_metric_crs, crs_label, metric_properties

    started = time.time()
    say = progress or (lambda fraction, detail: None)
    out = Path(output_dir)
    prediction_path = out / "prediction.tif"
    features_path = out / "ai_features.geojson"
    if not prediction_path.exists() or not features_path.exists():
        raise PlotError("The job's outputs are incomplete (prediction raster or features missing).")

    say(0.0, "Reading the job's features")
    collection = json.loads(features_path.read_text(encoding="utf-8"))
    from backend.gis.geometry import analyse_geometry

    buildings, barriers = [], []
    for feature in collection.get("features") or []:
        props = feature.get("properties") or {}
        class_id = props.get("class_id")
        if class_id == BUILDING_CLASS_ID:
            # Measured here (UTM), not read from an attribute.
            analysis = analyse_geometry(feature.get("geometry"))
            if not analysis["fatal"] and analysis["metrics"]["area_m2"] >= min_building_m2:
                buildings.append(feature)
        elif class_id in (ROAD_CLASS_ID, WATER_CLASS_ID):
            barriers.append(feature)

    with rasterio.open(prediction_path) as src:
        raster_crs = src.crs
        ground = _ground_pixel(src)
        factor = max(1, int(round(grid_m / ground)))
        width, height = max(1, src.width // factor), max(1, src.height // factor)
        transform = src.transform @ Affine.scale(src.width / width, src.height / height)
        say(0.05, f"Reading the class raster on a {ground * factor * 100:.1f} cm grid")
        classes = src.read(1, out_shape=(height, width), resampling=Resampling.nearest)
    cell_m = ground * factor
    valid = classes != NODATA_CLASS
    del classes

    summary: dict[str, Any] = {
        "job_id": job_id,
        "delineation_method": DELINEATION_METHOD,
        "limit_m": limit_m,
        "grid_m": round(cell_m, 4),
        "min_building_m2": min_building_m2,
        "seed_buildings": len(buildings),
        "plots": 0,
        "label": "AI GENERATED / PRELIMINARY",
        "review_reason": PLOT_REVIEW_REASON,
        "model": model,
    }

    metric_crs = None
    plots_out: list[dict[str, Any]] = []
    if buildings:
        to_raster = lambda geoms: _to_crs(geoms, "EPSG:4326", raster_crs)  # noqa: E731
        building_geoms = to_raster([shape(f["geometry"]) for f in buildings])
        barrier_geoms = to_raster([shape(f["geometry"]) for f in barriers])

        # ---- crop to the seeds plus the limit, to bound the work -------
        margin = int(math.ceil(limit_m / cell_m)) + 2
        inv = ~transform
        xs, ys = [], []
        for geom in building_geoms:
            minx, miny, maxx, maxy = geom.bounds
            for x, y in ((minx, miny), (maxx, maxy)):
                col, row = inv @ (x, y)
                xs.append(col)
                ys.append(row)
        c0 = max(0, int(min(xs)) - margin)
        c1 = min(width, int(math.ceil(max(xs))) + margin)
        r0 = max(0, int(min(ys)) - margin)
        r1 = min(height, int(math.ceil(max(ys))) + margin)
        window_transform = transform @ Affine.translation(c0, r0)
        shape_ = (r1 - r0, c1 - c0)
        land = valid[r0:r1, c0:c1].copy()
        del valid

        if barrier_geoms:
            blocked = rasterize(
                ((g, 1) for g in barrier_geoms), out_shape=shape_, transform=window_transform,
                fill=0, dtype="uint8", all_touched=True,
            )
            land &= blocked == 0
            del blocked
        seeds = rasterize(
            ((g, i + 1) for i, g in enumerate(building_geoms)), out_shape=shape_,
            transform=window_transform, fill=0, dtype="uint16" if len(building_geoms) < 65535 else "int32",
        )

        # ---- grow -------------------------------------------------------
        steps = int(math.ceil(limit_m * 1.1 / cell_m))
        say(0.15, f"Assigning land to {len(buildings):,} buildings ({shape_[1]:,} x {shape_[0]:,} cells)")
        regions = grow_regions(seeds, land, steps)
        del seeds, land

        say(0.6, "Tracing plot outlines")
        pieces: dict[int, list] = {}
        for geom_json, value in shapes(regions, mask=regions > 0, connectivity=4, transform=window_transform):
            pieces.setdefault(int(value), []).append(shape(geom_json))
        del regions

        # ---- measure in UTM, cut to the exact limit, remove barriers ----
        bounds = shapely.total_bounds(np.array(building_geoms, dtype=object))
        metric_crs = choose_metric_crs(raster_crs, tuple(bounds))
        to_metric = lambda geoms: _to_crs(geoms, raster_crs, metric_crs)  # noqa: E731
        buildings_m = to_metric(building_geoms)
        barriers_m = to_metric(barrier_geoms)
        barrier_union = shapely.union_all(np.array(barriers_m, dtype=object)) if barriers_m else None
        all_buildings_m = to_metric(
            to_raster([shape(f["geometry"]) for f in collection["features"]
                       if (f.get("properties") or {}).get("class_id") == BUILDING_CLASS_ID])
        )
        all_buildings_area = [g.area for g in all_buildings_m]
        building_tree = shapely.STRtree(np.array(all_buildings_m, dtype=object))
        roads_m = [
            g for f, g in zip(barriers, barriers_m) if (f.get("properties") or {}).get("class_id") == ROAD_CLASS_ID
        ]
        road_tree = shapely.STRtree(np.array(roads_m, dtype=object)) if roads_m else None

        say(0.75, f"Measuring {len(pieces):,} plots")
        candidates = []
        for label, parts in pieces.items():
            index = label - 1
            region = to_metric([shapely.union_all(np.array(parts, dtype=object))])[0]
            region = region.intersection(buildings_m[index].buffer(limit_m))
            if barrier_union is not None:
                region = region.difference(barrier_union)
            region = _polygonal(region)
            if region is None:
                continue
            polygons = [p for p in getattr(region, "geoms", [region]) if p.area >= sliver_area_m2]
            if not polygons:
                continue
            region = shapely.union_all(np.array(polygons, dtype=object))
            candidates.append((index, region))

        if candidates:
            import geopandas as gpd

            frame = gpd.GeoDataFrame(
                {"seed": [i for i, _ in candidates]},
                geometry=[g for _, g in candidates], crs=metric_crs,
            )
            frame["area_m2"] = frame.geometry.area
            frame, topology = run_topology_validation(
                frame, sliver_area_threshold=sliver_area_m2, area_column="area_m2",
                min_overlap_area=0.01 * cell_m * cell_m,
            )
            summary["topology"] = {k: v for k, v in topology.items() if k != "removed"}

            frame = frame.sort_values("area_m2", ascending=False).reset_index(drop=True)
            lonlat = frame.to_crs("EPSG:4326")
            for number, (row, geo) in enumerate(zip(frame.itertuples(), lonlat.geometry), start=1):
                geom = row.geometry
                seed = buildings[row.seed]
                seed_props = seed.get("properties") or {}
                inside = building_tree.query(geom, predicate="intersects")
                building_area = float(sum(geom.intersection(all_buildings_m[i]).area for i in inside))
                # Counted two ways: every detected building feature, and only
                # those of at least min_building_m2 (the rule that seeds a plot).
                contained = [i for i in inside if geom.contains(all_buildings_m[i].representative_point())]
                buildings_inside_all = len(contained)
                buildings_inside_seed_rule = sum(1 for i in contained if all_buildings_area[i] >= min_building_m2)
                distance = None
                if road_tree is not None:
                    _, nearest = road_tree.query_nearest(geom, return_distance=True, all_matches=False)
                    distance = float(nearest[0]) if len(nearest) else None
                measures = metric_properties(geom)
                props = {
                    "plot_id": f"PLOT-{number:06d}",
                    "uid": f"PLOT-{number:06d}",
                    "class_name": "Candidate plot",
                    "class_key": "plot",
                    "label": PLOT_LABEL,
                    "building_feature_id": _feature_uid(seed_props.get("feature_id"), "BLD"),
                    "area_m2": round(measures["area_m2"], 3),
                    "perimeter_m": round(measures["perimeter_m"], 3),
                    "length_m": round(measures["length_m"], 3),
                    "width_m": round(measures["width_m"], 3),
                    "compactness": round(measures["compactness"], 4),
                    "building_area_m2": round(building_area, 3),
                    "coverage_ratio": round(building_area / measures["area_m2"], 4) if measures["area_m2"] else None,
                    "buildings_inside_all": buildings_inside_all,
                    "buildings_inside_seed_rule": buildings_inside_seed_rule,
                    "nearest_road_distance_m": round(distance, 3) if distance is not None else None,
                    "road_access_candidate": (distance <= road_access_distance_m) if distance is not None else None,
                    "building_confidence": seed_props.get("confidence"),
                    "building_entropy": seed_props.get("entropy"),
                    "parts": len(getattr(geom, "geoms", [geom])),
                    "geometry_status": row.geometry_status,
                    "delineation_method": DELINEATION_METHOD,
                    "delineation_limit_m": limit_m,
                    "delineation_grid_m": round(cell_m, 4),
                    "review_reason": PLOT_REVIEW_REASON,
                    "metric_crs": crs_label(metric_crs),
                    "processing_job_id": job_id,
                    "model_id": (model or {}).get("id"),
                    "model_file": (model or {}).get("file"),
                    "model_hash": (model or {}).get("hash"),
                }
                plots_out.append({"type": "Feature", "properties": props, "geometry": geometry_to_geojson(geo)})

    say(0.95, f"Writing {len(plots_out):,} candidate plots")
    areas = np.array([p["properties"]["area_m2"] for p in plots_out]) if plots_out else np.array([])
    summary.update(
        {
            "plots": len(plots_out),
            "metric_crs": crs_label(metric_crs) if metric_crs is not None else None,
            "area_m2": _distribution(areas),
            **one_building_shares([p["properties"] for p in plots_out]),
            "elapsed_seconds": round(time.time() - started, 1),
            "outputs": {"plots": PLOTS_FILE},
        }
    )
    document = {
        "type": "FeatureCollection",
        "name": f"candidate_plots_{job_id or 'job'}",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "metadata": {k: v for k, v in summary.items() if k != "area_m2"},
        "features": plots_out,
    }
    (out / PLOTS_FILE).write_text(json.dumps(document, default=str), encoding="utf-8")
    (out / SUMMARY_FILE).write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    say(1.0, f"{len(plots_out):,} candidate plots")
    return summary


def one_building_shares(plots: list[dict[str, Any]]) -> dict[str, Any]:
    """Plots holding exactly one building, counted two ways."""

    total = len(plots)
    seeds = sum(1 for p in plots if p.get("buildings_inside_seed_rule") == 1)
    every = sum(1 for p in plots if p.get("buildings_inside_all") == 1)
    return {
        "one_building_seed_rule": seeds,
        "one_building_share_seed_rule": round(seeds / total, 4) if total else None,
        "one_building_all": every,
        "one_building_share_all": round(every / total, 4) if total else None,
    }


def _feature_uid(value, prefix: str) -> str | None:
    """The id the map shows for a feature (``BLD-000004``)."""

    if value in (None, ""):
        return None
    if isinstance(value, str) and "-" in value:
        return value
    try:
        return f"{prefix}-{int(value):06d}"
    except (TypeError, ValueError):
        return str(value)


def _polygonal(geometry):
    if geometry is None or geometry.is_empty:
        return None
    if geometry.geom_type in ("Polygon", "MultiPolygon"):
        return geometry
    if geometry.geom_type == "GeometryCollection":
        import shapely

        polygons = [g for g in geometry.geoms if g.geom_type in ("Polygon", "MultiPolygon") and not g.is_empty]
        return shapely.union_all(np.array(polygons, dtype=object)) if polygons else None
    return None


def _ground_pixel(src) -> float:
    """Ground size of one pixel in metres (UTM-measured for Mercator / geographic)."""

    from backend.gis.metric import pixel_ground_size

    size = pixel_ground_size(src.crs, src.transform, src.width, src.height)
    return math.sqrt(size["area_m2"])


def _distribution(values: np.ndarray) -> dict[str, Any] | None:
    if not len(values):
        return None
    q = np.percentile(values, [10, 25, 50, 75, 90])
    return {
        "count": int(len(values)),
        "total": round(float(values.sum()), 1),
        "min": round(float(values.min()), 1),
        "p10": round(float(q[0]), 1),
        "p25": round(float(q[1]), 1),
        "median": round(float(q[2]), 1),
        "p75": round(float(q[3]), 1),
        "p90": round(float(q[4]), 1),
        "max": round(float(values.max()), 1),
    }
