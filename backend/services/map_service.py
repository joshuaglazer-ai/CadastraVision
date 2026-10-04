"""What the map draws: layer catalogue and spatially filtered features.

The browser never receives a whole layer file. Each request returns only
the features inside the assigned area and the current view, generalised to
the zoom level, with the attributes the map needs to style them. Full
attributes are fetched for one feature when it is selected.
"""

from __future__ import annotations

import math
from typing import Any

from backend.ai import qa
from backend.ai.classes import CLASS_KEYS, CLASS_NAMES, KEY_TO_ID
from backend.config import Settings, display_path
from backend.core import runtime
from backend.core.store import Store
from backend.gis.geometry import point_in_geometry
from backend.services import dataset_service
from backend.services.assignment_service import SurveyorContext

LANDCOVER_CLASSES = ["building", "road", "field", "water", "other"]

# Attributes sent with every map feature (kept small on purpose).
MAP_PROPERTIES = (
    "uid",
    "layer",
    "class_key",
    "class_name",
    "area_m2",
    "confidence",
    "entropy",
    "review_priority",
    "qa_flags",
    "geometry_status",
    "road_access_candidate",
)

# From this zoom a screen pixel is under 7 cm, so the 5 cm geometry is used.
DETAIL_ZOOM = 21


def metres_per_pixel(zoom: float, latitude: float) -> float:
    return 156543.03392 * math.cos(math.radians(latitude)) / (2.0**zoom)


def intersect_bbox(a, b):
    """Intersection of two boxes, or ``None`` when they do not meet."""

    if a is None:
        return b
    if b is None:
        return a
    box = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    if box[0] > box[2] or box[1] > box[3]:
        return None
    return box


def parse_bbox(text: str | None):
    """``"minx,miny,maxx,maxy"`` -> tuple, or ``None``. Raises ``ValueError``."""

    if text in (None, ""):
        return None
    parts = [float(part) for part in str(text).split(",")]
    if len(parts) != 4 or not all(math.isfinite(p) for p in parts):
        raise ValueError("bbox must be 'min_lon,min_lat,max_lon,max_lat'")
    minx, miny, maxx, maxy = parts
    if minx > maxx or miny > maxy:
        raise ValueError("bbox minimum exceeds maximum")
    return (minx, miny, maxx, maxy)


def _boundary_is_box(context: SurveyorContext) -> bool:
    boundary = context.boundary or {}
    if boundary.get("type") != "Polygon":
        return False
    rings = boundary.get("coordinates") or []
    if len(rings) != 1 or len(rings[0]) != 5:
        return False
    xs = {round(float(p[0]), 9) for p in rings[0]}
    ys = {round(float(p[1]), 9) for p in rings[0]}
    return len(xs) == 2 and len(ys) == 2


def status_for(properties: dict[str, Any], state: dict[str, Any] | None) -> str:
    if state:
        return state["verification_status"]
    return qa.default_status(properties.get("review_priority") or "Low")


def decorate(feature: dict[str, Any], state: dict[str, Any] | None, slim: bool) -> dict[str, Any]:
    """Attach the effective review state; optionally slim the attributes."""

    properties = feature["properties"]
    status = status_for(properties, state)
    if slim:
        properties = {key: properties.get(key) for key in MAP_PROPERTIES if key in properties}
    else:
        properties = dict(properties)
    properties["verification_status"] = status
    properties["review_count"] = state["review_count"] if state else 0
    geometry = feature["geometry"]
    if state and state.get("edited_geometry") and status == "EDITED":
        geometry = state["edited_geometry"]
        properties["geometry_source"] = "SURVEYOR_EDIT"
    return {
        "type": "Feature",
        "id": feature["id"],
        "bbox": feature.get("bbox"),
        "properties": properties,
        "geometry": geometry,
    }


def features(
    context: SurveyorContext,
    settings: Settings,
    store: Store,
    *,
    source: str,
    layer: str,
    classes: list[str] | None = None,
    bbox=None,
    zoom: float | None = None,
    limit: int | None = None,
    priorities: list[str] | None = None,
) -> dict[str, Any]:
    """Features of ``layer`` ("parcels" or "landcover") for the map."""

    layers = runtime.get_layers()
    runtime.ensure_source(source)
    record = layers.layer(source, layer)
    if record is None:
        return {
            "type": "FeatureCollection",
            "features": [],
            "available": False,
            "message": runtime.unavailable_reason(layer, source),
            "source": source,
            "layer": layer,
        }

    area = context.bbox
    query_bbox = intersect_bbox(area, bbox)
    if area is not None and bbox is not None and query_bbox is None:
        # The view is entirely outside the assigned area.
        return {
            "type": "FeatureCollection",
            "features": [],
            "available": True,
            "total_matching": 0,
            "returned": 0,
            "truncated": False,
            "source": source,
            "layer": layer,
            "detail": "none",
        }

    latitude = 0.0
    if record.get("bbox"):
        latitude = (record["bbox"][1] + record["bbox"][3]) / 2.0

    geometry = "overview"
    min_area = None
    if zoom is not None:
        if zoom >= DETAIL_ZOOM:
            geometry = "detail"
        # Features smaller than about two screen pixels cannot be seen.
        pixel = metres_per_pixel(zoom, latitude)
        min_area = (2.0 * pixel) ** 2 if geometry == "overview" else None

    limit = min(int(limit or settings.map_feature_limit), settings.map_feature_limit)
    found, total = layers.query(
        source,
        layer,
        classes=classes,
        bbox=query_bbox,
        min_area=min_area,
        priorities=priorities,
        geometry=geometry,
        order="area_desc",
        limit=limit,
    )

    if context.boundary and not _boundary_is_box(context):
        kept = []
        for feature in found:
            box = feature["bbox"]
            centre = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
            if point_in_geometry(centre[0], centre[1], context.boundary):
                kept.append(feature)
        found = kept

    states = store.feature_states(source)
    decorated = [decorate(f, states.get((source, f["id"])), slim=True) for f in found]
    return {
        "type": "FeatureCollection",
        "features": decorated,
        "available": True,
        "total_matching": total,
        "returned": len(decorated),
        "truncated": total > len(found),
        "source": source,
        "layer": layer,
        "detail": geometry,
        "min_area_m2": min_area,
    }


def feature_detail(
    store: Store,
    *,
    source: str,
    uid: str,
    geometry: str = "overview",
) -> dict[str, Any] | None:
    """One feature with every attribute, its review state and history.

    ``geometry`` is ``overview`` (default, light) or ``detail`` (5 cm, used
    when the surveyor edits the outline).
    """

    layers = runtime.get_layers()
    runtime.ensure_source(source)
    level = "detail" if geometry == "detail" else "overview"
    feature = layers.get(source, uid, geometry=level)
    if feature is None:
        return None
    if feature["geometry"] is None:  # too small to have an overview geometry
        feature = layers.get(source, uid, geometry="detail")
        level = "detail"

    state = store.feature_states(source).get((source, uid))
    detail = decorate(feature, state, slim=False)
    properties = detail["properties"]
    properties["uncertainty"] = qa.uncertainty_band(
        properties.get("confidence"), properties.get("entropy")
    )
    properties["uncertainty_note"] = (
        "Model-derived uncertainty. It ranks features for review; it is not proof that a feature is wrong."
    )
    properties["label"] = (
        "SURVEYOR VERIFIED"
        if properties["verification_status"] == "SURVEYOR_VERIFIED"
        else "AI GENERATED / PRELIMINARY"
    )
    properties["source"] = source
    detail["geometry_level"] = level
    edited = detail["properties"].get("geometry_source") == "SURVEYOR_EDIT"
    # The AI geometry is repeated only when the surveyor has replaced it.
    detail["ai_geometry"] = feature["geometry"] if edited else None
    detail["reviews"] = store.list_reviews(feature_id=uid, source=source, limit=50)
    detail["audit"] = store.list_audit(entity_id=f"{source}:{uid}", limit=50)
    return detail


def features_in_area(context: SurveyorContext, source: str) -> int:
    """All features of ``source`` (parcels and land cover, fragments included)
    within the current area. Zero means nothing has been processed here, which
    the interface must not present as "checked and nothing found"."""

    layers = runtime.get_layers()
    runtime.ensure_source(source)
    total = 0
    for kind in ("parcels", "landcover"):
        if layers.layer(source, kind) is None:
            continue
        _, count = layers.query(source, kind, bbox=context.bbox, geometry="none", limit=0)
        total += count
    return total


def layer_catalog(
    context: SurveyorContext, settings: Settings, store: Store, source: str
) -> dict[str, Any]:
    """Every map layer with whether it can be drawn, and why not if not."""

    layers = runtime.get_layers()
    indexed = runtime.ensure_source(source)
    area = context.bbox

    catalog: list[dict[str, Any]] = []

    catalog.append(
        {
            "key": "assigned_area",
            "label": "Assigned area",
            "group": "Assignment",
            "available": bool(context.boundary),
            "default_on": True,
            "message": None if context.boundary else "Requires administrator input",
            "count": 1 if context.boundary else 0,
            "demo": bool(context.assignment and context.assignment.get("is_demo")),
        }
    )

    parcels = indexed.get("parcels")
    parcel_stats = layers.stats(source, "parcels", bbox=area) if parcels else None
    catalog.append(
        {
            "key": "parcels",
            "label": "Candidate parcels",
            "group": "AI-generated",
            "available": bool(parcels),
            "default_on": True,
            "message": None if parcels else runtime.unavailable_reason("parcels", source),
            "count": parcel_stats["totals"]["features"] if parcel_stats else 0,
            "area_m2": parcel_stats["totals"]["area_m2"] if parcel_stats else None,
        }
    )

    landcover = indexed.get("landcover")
    landcover_stats = layers.stats(source, "landcover", bbox=area) if landcover else None
    landcover_reason = None if landcover else runtime.unavailable_reason("landcover", source)
    for key in LANDCOVER_CLASSES:
        entry = (landcover_stats or {}).get("classes", {}).get(key)
        name = CLASS_NAMES[KEY_TO_ID[key]]
        catalog.append(
            {
                "key": key,
                "label": {"building": "Buildings", "road": "Roads", "field": "Fields", "water": "Water", "other": "Other"}[key],
                "class_name": name,
                "group": "AI-generated",
                "available": bool(entry),
                # Field features share their geometry with candidate parcels.
                "default_on": key != "field" or not parcels,
                "message": None if entry else (landcover_reason or "No features of this class"),
                "count": entry["count"] if entry else 0,
                "area_m2": entry["area_m2"] if entry else None,
            }
        )

    discovered = dataset_service.discover(settings, store)
    by_key = {category["key"]: category for category in discovered["categories"]}

    reference = []
    for key in ("land_records", "gis", "survey_of_india"):
        for dataset in by_key[key]["datasets"]:
            if dataset["kind"] in ("vector", "mixed") and dataset["relative_path"].lower().endswith(
                (".geojson", ".json")
            ) and dataset["status"] != "REQUIRES REVIEW":
                reference.append(
                    {
                        "dataset_id": dataset["dataset_id"],
                        "name": dataset["name"],
                        "source_label": dataset["source_label"],
                        "feature_count": dataset["feature_count"],
                    }
                )
    catalog.append(
        {
            "key": "existing_gis",
            "label": "Existing GIS",
            "group": "Reference",
            "available": bool(reference),
            "default_on": False,
            "message": None if reference else (
                "Dataset unavailable. Add a parcel GeoJSON to "
                f"{display_path(settings.data_dir / 'land_records')}/ or upload it on the Datasets page."
            ),
            "count": len(reference),
            "datasets": reference,
        }
    )

    gnss = by_key["gnss"]
    catalog.append(
        {
            "key": "gnss",
            "label": "GNSS / ground truth",
            "group": "Reference",
            "available": gnss["count"] > 0,
            "default_on": gnss["count"] > 0,
            "message": None if gnss["count"] else (
                "Requires surveyor input. Add a CSV or GeoJSON of points to "
                f"{display_path(settings.data_dir / 'gnss')}/."
            ),
            "count": sum(d.get("feature_count") or 0 for d in gnss["datasets"]),
        }
    )

    for key, label in (("dsm", "DSM"), ("dtm", "DTM")):
        usable = [d for d in by_key[key]["datasets"] if d["status"] != "REQUIRES REVIEW"]
        catalog.append(
            {
                "key": key,
                "label": label,
                "group": "Elevation",
                "available": bool(usable),
                "default_on": False,
                "message": None if usable else (
                    f"{label} dataset unavailable. Add a GeoTIFF to {display_path(settings.data_dir / key)}/."
                ),
                "count": len(usable),
                "dataset": usable[0]["name"] if usable else None,
                "extent": usable[0]["extent"] if usable else None,
            }
        )

    data_bbox = None
    for record in (parcels, landcover):
        if record and record.get("bbox"):
            box = record["bbox"]
            data_bbox = (
                box
                if data_bbox is None
                else [
                    min(data_bbox[0], box[0]),
                    min(data_bbox[1], box[1]),
                    max(data_bbox[2], box[2]),
                    max(data_bbox[3], box[3]),
                ]
            )

    return {
        "source": source,
        "sources": runtime.known_sources(store),
        "layers": catalog,
        "assignment_bbox": list(area) if area else None,
        "data_bbox": data_bbox,
        "class_order": [CLASS_KEYS[i] for i in sorted(CLASS_KEYS)],
        "indexing_errors": runtime.bootstrap_errors(),
        "data_files": runtime.data_files() if source == runtime.EXISTING_SOURCE else None,
        "features_in_area": features_in_area(context, source),
    }
