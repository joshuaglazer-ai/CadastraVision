"""Candidate plots: building them for a job, their statistics, and the
comparison with a reference layer.

Plots are made by ``backend.ai.plots`` from a completed job's outputs; this
module runs that step, indexes the result as the job's ``plots`` layer and
records the outcome on the job. Nothing about the job's other layers changes.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.ai import plots as plot_builder
from backend.config import Settings
from backend.core import runtime
from backend.core.store import Store, utc_now
from backend.services import dataset_service
from backend.services.assignment_service import SurveyorContext

log = logging.getLogger("cadastra")


# ------------------------------------------------------------------ building
def build_for_job(job_id: str, settings: Settings, store: Store, actor: str = "system") -> dict[str, Any]:
    """Build (or rebuild) a completed job's candidate plots. Never raises:
    a failure is recorded on the job and returned."""

    job = store.get_job(job_id)
    if job is None or job["status"] != "COMPLETED":
        return {"status": "FAILED", "error": "Candidate plots are built only for completed jobs."}

    def record(state: dict[str, Any]) -> None:
        current = store.get_job(job_id) or job
        summary = dict(current.get("summary") or {})
        summary["plots"] = state
        store.update_job(job_id, summary=summary)

    record({"status": "RUNNING", "started_at": utc_now()})
    try:
        result = plot_builder.build_plots(
            runtime.job_output_dir(job_id),
            limit_m=settings.plot_limit_m,
            grid_m=settings.plot_grid_m,
            min_building_m2=settings.plot_min_building_m2,
            road_access_distance_m=settings.road_access_distance_m,
            sliver_area_m2=settings.sliver_area_m2,
            job_id=job_id,
            model=job.get("model"),
        )
        runtime.ensure_source(runtime.job_source(job_id))
        state = {**result, "status": "COMPLETED", "finished_at": utc_now()}
    except Exception as exc:  # recorded, never takes the job down
        log.exception("Candidate plots for %s failed", job_id)
        state = {"status": "FAILED", "finished_at": utc_now(), "error": f"{type(exc).__name__}: {exc}"}
    record(state)
    store.add_audit(
        actor_id=actor,
        actor_email=None,
        action="plots.build",
        entity_type="job",
        entity_id=job_id,
        after={k: state.get(k) for k in ("status", "plots", "seed_buildings", "limit_m", "grid_m", "error")},
    )
    return state


# ---------------------------------------------------------------- statistics
def overview(context: SurveyorContext, settings: Settings, source: str) -> dict[str, Any] | None:
    """Plot counts and sizes in the current area, and the reference check."""

    layers = runtime.get_layers()
    runtime.ensure_source(source)
    if layers.layer(source, "plots") is None:
        return None
    area = context.bbox
    features = [
        feature for feature in layers.iter_full(source, "plots")
        if _in_box(feature.get("bbox"), area)
    ]
    sizes = sorted(float(f["properties"].get("area_m2") or 0.0) for f in features)
    share = plot_builder.one_building_share([f["properties"] for f in features])
    road = sum(1 for f in features if f["properties"].get("road_access_candidate") is True)
    return {
        "count": len(features),
        "area_m2": round(sum(sizes), 1),
        "median_area_m2": round(_quantile(sizes, 0.5), 1) if sizes else None,
        "p10_area_m2": round(_quantile(sizes, 0.1), 1) if sizes else None,
        "p90_area_m2": round(_quantile(sizes, 0.9), 1) if sizes else None,
        **share,
        "low_coverage": sum(
            1 for f in features if (f["properties"].get("coverage_ratio") or 0) < plot_builder.LOW_COVERAGE
        ),
        "road_access": road,
        "delineation_method": plot_builder.DELINEATION_METHOD,
        "review_reason": plot_builder.PLOT_REVIEW_REASON,
        "reference_check": reference_check(features, settings, area),
    }


def reference_check(plots: list[dict[str, Any]], settings: Settings, area) -> dict[str, Any] | None:
    """Compare plots with the reference features of the existing GIS layers.

    A reference feature is placed by its representative point. Reports how
    many plots hold exactly one, several or no reference features, and how
    many reference features have a plot to themselves.
    """

    import numpy as np
    import shapely
    from shapely.geometry import shape

    datasets = _reference_datasets(settings)
    if not datasets or not plots:
        return None

    points, names = [], []
    for dataset in datasets:
        try:
            features = dataset_service._reference_features(dataset, settings)
        except dataset_service.DatasetError:
            continue
        for feature in features:
            geom = shape(feature["geometry"])
            if geom.is_empty:
                continue
            point = geom.representative_point()
            if area is None or (area[0] <= point.x <= area[2] and area[1] <= point.y <= area[3]):
                points.append(point)
        names.append(dataset["name"])
    if not points:
        return {"datasets": names, "reference_features": 0, "message": "No reference features lie in this area."}

    plot_geoms = np.array([shape(p["geometry"]) for p in plots], dtype=object)
    tree = shapely.STRtree(plot_geoms)
    point_index, plot_index = tree.query(np.array(points, dtype=object), predicate="within")
    per_plot = np.bincount(plot_index, minlength=len(plots))
    alone = int(sum(1 for p in plot_index if per_plot[p] == 1))
    return {
        "datasets": names,
        "reference_features": len(points),
        "plots": len(plots),
        "plots_with_one": int((per_plot == 1).sum()),
        "plots_with_several": int((per_plot > 1).sum()),
        "plots_with_none": int((per_plot == 0).sum()),
        "reference_with_own_plot": alone,
        "reference_outside_plots": len(points) - len(set(point_index.tolist())),
        "basis": "Each reference feature is placed by its representative point.",
    }


def _reference_datasets(settings: Settings) -> list[dict[str, Any]]:
    from backend.core.store import get_store

    found = dataset_service.discover(settings, get_store())
    by_key = {category["key"]: category for category in found["categories"]}
    datasets = []
    for key in ("land_records", "gis", "survey_of_india"):
        for dataset in by_key.get(key, {}).get("datasets", []):
            path = str(dataset.get("relative_path") or "").lower()
            if dataset.get("kind") in ("vector", "mixed") and path.endswith((".geojson", ".json")) \
                    and dataset.get("status") != "REQUIRES REVIEW":
                datasets.append(dataset)
    return datasets


def _in_box(bbox, area) -> bool:
    if area is None or not bbox:
        return True
    return not (bbox[2] < area[0] or bbox[0] > area[2] or bbox[3] < area[1] or bbox[1] > area[3])


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    position = (len(values) - 1) * q
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)
