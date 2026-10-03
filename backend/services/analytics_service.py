"""Analytics computed from the indexed layers, reviews and jobs.

Every number here is counted or summed from stored data. When an input is
missing the value is ``None`` and the UI shows "Data unavailable".
"""

from __future__ import annotations

from typing import Any

from backend.ai.classes import CLASS_KEYS
from backend.config import Settings
from backend.core import runtime
from backend.core.store import Store
from backend.gis.geometry import bbox_polygon, polygon_metrics
from backend.services import review_service
from backend.services.assignment_service import SurveyorContext


def _class(stats: dict[str, Any] | None, key: str) -> dict[str, Any] | None:
    if not stats:
        return None
    return stats["classes"].get(key)


def processing_status(store: Store, context: SurveyorContext) -> dict[str, Any]:
    """Summary of this surveyor's processing jobs."""

    jobs = store.list_jobs(surveyor_id=context.surveyor_id, limit=200)
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job["status"]] = counts.get(job["status"], 0) + 1

    active = next((job for job in jobs if job["status"] in ("QUEUED", "PROCESSING")), None)
    latest = jobs[0] if jobs else None

    if active:
        label, state = f"Processing · {active['stage'] or 'queued'}", "PROCESSING"
    elif latest is None:
        label, state = "No imagery processed", "IDLE"
    elif latest["status"] == "COMPLETED":
        label, state = "Ready for review", "COMPLETED"
    elif latest["status"] == "FAILED":
        label, state = "Last job failed", "FAILED"
    else:
        label, state = "Awaiting start", latest["status"]

    return {
        "state": state,
        "label": label,
        "counts": counts,
        "total_jobs": len(jobs),
        "active_job_id": active["job_id"] if active else None,
        "active_progress": active["progress"] if active else None,
        "latest_job_id": latest["job_id"] if latest else None,
        "latest_job_status": latest["status"] if latest else None,
        "latest_job_at": latest["updated_at"] if latest else None,
    }


def overview(
    context: SurveyorContext, settings: Settings, store: Store, source: str
) -> dict[str, Any]:
    """Everything the dashboard KPIs and the analytics page need."""

    layers = runtime.get_layers()
    indexed = runtime.ensure_source(source)
    area = context.bbox

    parcel_stats = layers.stats(source, "parcels", bbox=area) if indexed.get("parcels") else None
    landcover_stats = layers.stats(source, "landcover", bbox=area) if indexed.get("landcover") else None

    # ---- extent --------------------------------------------------------
    data_bbox = None
    for record in indexed.values():
        if record and record.get("bbox"):
            box = record["bbox"]
            data_bbox = (
                list(box)
                if data_bbox is None
                else [
                    min(data_bbox[0], box[0]),
                    min(data_bbox[1], box[1]),
                    max(data_bbox[2], box[2]),
                    max(data_bbox[3], box[3]),
                ]
            )
    extent_area = None
    if data_bbox:
        extent_area = round(polygon_metrics(bbox_polygon(*data_bbox))["area_m2"], 1)

    mapped_area = landcover_stats["totals"]["area_m2"] if landcover_stats else None
    assignment_area = context.assignment.get("area_m2") if context.assignment else None

    # ---- land cover ----------------------------------------------------
    classes = []
    if landcover_stats:
        for key in [CLASS_KEYS[i] for i in sorted(CLASS_KEYS)] + sorted(
            set(landcover_stats["classes"]) - set(CLASS_KEYS.values())
        ):
            entry = landcover_stats["classes"].get(key)
            if not entry:
                continue
            classes.append(
                {
                    **entry,
                    "share_of_mapped": (
                        round(entry["area_m2"] / mapped_area, 4) if mapped_area else None
                    ),
                    "features_excluding_fragments": entry["count"] - entry["fragments"],
                }
            )

    buildings = _class(landcover_stats, "building")
    roads = _class(landcover_stats, "road")
    water = _class(landcover_stats, "water")
    fields = _class(landcover_stats, "field")
    other = _class(landcover_stats, "other")

    # ---- parcels -------------------------------------------------------
    parcels: dict[str, Any] | None = None
    if parcel_stats:
        total = parcel_stats["totals"]["features"]
        _, plausible = layers.query(
            source, "parcels", bbox=area, min_area=settings.min_candidate_parcel_m2,
            geometry="none", limit=0,
        )
        road_access = no_road_access = unknown_access = 0
        offset = 0
        while True:
            page, _ = layers.query(
                source, "parcels", bbox=area, min_area=settings.min_candidate_parcel_m2,
                geometry="none", order="uid", limit=1000, offset=offset,
            )
            if not page:
                break
            for feature in page:
                value = feature["properties"].get("road_access_candidate")
                if value is True:
                    road_access += 1
                elif value is False:
                    no_road_access += 1
                else:
                    unknown_access += 1
            offset += len(page)
        parcels = {
            "total": total,
            "area_m2": parcel_stats["totals"]["area_m2"],
            "at_or_above_minimum": plausible,
            "minimum_area_m2": settings.min_candidate_parcel_m2,
            "fragments": parcel_stats["totals"]["fragments"],
            "fragment_threshold_m2": settings.sliver_area_m2,
            "road_access": road_access,
            "no_road_access": no_road_access,
            "road_access_unknown": unknown_access,
            "road_access_basis": (
                f"Candidate parcels of at least {settings.min_candidate_parcel_m2:g} m² "
                f"with a road within {settings.road_access_distance_m:g} m"
            ),
            "high": parcel_stats["totals"]["high"],
            "medium": parcel_stats["totals"]["medium"],
        }

    # ---- review workload -----------------------------------------------
    review_queue = review_service.queue(
        context, settings, store, source=source, group="high", limit=0
    )
    review_stats = store.review_stats(source)

    with_confidence = landcover_stats["totals"]["with_confidence"] if landcover_stats else 0
    total_features = landcover_stats["totals"]["features"] if landcover_stats else None
    confidence_values = [
        (c["mean_confidence"], c["with_confidence"])
        for c in (landcover_stats or {"classes": {}})["classes"].values()
        if c["mean_confidence"] is not None
    ]
    entropy_values = [
        (c["mean_entropy"], c["with_confidence"])
        for c in (landcover_stats or {"classes": {}})["classes"].values()
        if c["mean_entropy"] is not None
    ]

    def weighted(values: list[tuple[float, int]]) -> float | None:
        weight = sum(w for _, w in values)
        return round(sum(v * w for v, w in values) / weight, 4) if weight else None

    return {
        "source": source,
        "ai_features": total_features,
        "ai_features_excluding_fragments": (
            total_features - landcover_stats["totals"]["fragments"] if landcover_stats else None
        ),
        "fragments": landcover_stats["totals"]["fragments"] if landcover_stats else None,
        "fragment_threshold_m2": settings.sliver_area_m2,
        "candidate_parcels": parcels["total"] if parcels else None,
        "parcels": parcels,
        "buildings": {
            "count": buildings["count"] if buildings else (0 if landcover_stats else None),
            "area_m2": buildings["area_m2"] if buildings else (0.0 if landcover_stats else None),
            "fragments": buildings["fragments"] if buildings else (0 if landcover_stats else None),
        },
        "roads": {
            "count": roads["count"] if roads else (0 if landcover_stats else None),
            "area_m2": roads["area_m2"] if roads else (0.0 if landcover_stats else None),
            "coverage_of_mapped": (
                round(roads["area_m2"] / mapped_area, 4) if roads and mapped_area else None
            ),
        },
        "water": {
            "count": water["count"] if water else (0 if landcover_stats else None),
            "area_m2": water["area_m2"] if water else (0.0 if landcover_stats else None),
        },
        "fields": {
            "count": fields["count"] if fields else (0 if landcover_stats else None),
            "area_m2": fields["area_m2"] if fields else (0.0 if landcover_stats else None),
        },
        "other": {
            "count": other["count"] if other else (0 if landcover_stats else None),
            "area_m2": other["area_m2"] if other else (0.0 if landcover_stats else None),
        },
        "classes": classes,
        "mapped_area_m2": mapped_area,
        "data_extent_area_m2": extent_area,
        "data_bbox": data_bbox,
        "mapped_share_of_extent": (
            round(mapped_area / extent_area, 4) if mapped_area and extent_area else None
        ),
        "assignment_area_m2": assignment_area,
        "data_share_of_assignment": (
            round(extent_area / assignment_area, 4) if extent_area and assignment_area else None
        ),
        "review": {
            "counts": review_queue["counts"],
            "review_required": review_queue["review_required"],
            "fragments_hidden": review_queue["fragments_hidden"],
            "verified": review_stats["verified_count"] + review_stats["edited_count"],
            "flagged": review_stats["flagged_count"] + review_stats["rejected_count"],
            "total_reviews": review_stats["total_reviews"],
            "reviewed_features": review_stats["reviewed_features"],
            "ground_truth": review_stats["ground_truth_count"],
            "status_counts": review_stats["status_counts"],
            "action_counts": review_stats["action_counts"],
        },
        "uncertainty": {
            "available": with_confidence > 0,
            "features_with_confidence": with_confidence,
            "mean_confidence": weighted(confidence_values),
            "mean_entropy": weighted(entropy_values),
            "message": (
                None
                if with_confidence
                else "Model confidence and entropy were not recorded when this layer was generated. "
                "They are produced for every feature of a new processing job."
            ),
        },
        "processing": processing_status(store, context),
        "label": "AI GENERATED / PRELIMINARY",
    }


def legacy_stats(source: str = runtime.EXISTING_SOURCE) -> dict[str, Any]:
    """The original ``/stats`` response shape, from the indexed layer."""

    layers = runtime.get_layers()
    indexed = runtime.ensure_source(source)
    if not indexed.get("parcels"):
        raise FileNotFoundError("Candidate parcel layer not found.")
    stats = layers.stats(source, "parcels")
    total = stats["totals"]["features"]
    area = stats["totals"]["area_m2"]
    perimeter = sum(c["perimeter_m"] for c in stats["classes"].values())

    road_access = 0
    offset = 0
    while True:
        page, _ = layers.query(source, "parcels", geometry="none", order="uid", limit=1000, offset=offset)
        if not page:
            break
        road_access += sum(1 for f in page if f["properties"].get("road_access_candidate") is True)
        offset += len(page)

    return {
        "total_parcels": total,
        "total_area_m2": round(area, 4),
        "average_area_m2": round(area / total, 4) if total else 0,
        "total_perimeter_m": round(perimeter, 4),
        "road_access_candidates": road_access,
    }
