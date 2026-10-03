"""Endpoints from the first version of the API, kept working.

They now read from the indexed layer store instead of loading whole GeoJSON
files into memory, and they require authentication like everything else.
The two "-geojson" endpoints return display-generalised geometry and are
capped; use /api/map/* for the map and /api/export/* for full-resolution
data.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.ai.classes import class_key
from backend.config import settings
from backend.core import runtime
from backend.core.deps import current_context, store_dep
from backend.core.store import Store
from backend.services import analytics_service, export_service, map_service
from backend.services.assignment_service import SurveyorContext

router = APIRouter(tags=["Legacy"])

SOURCE = runtime.EXISTING_SOURCE


def _require(kind: str):
    indexed = runtime.ensure_source(SOURCE)
    if not indexed.get(kind):
        raise HTTPException(status_code=404, detail=f"Data unavailable: the {kind} layer was not found.")
    return runtime.get_layers()


@router.get("/stats")
def stats(context: SurveyorContext = Depends(current_context)):
    try:
        return analytics_service.legacy_stats(SOURCE)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Data unavailable: {exc}")


@router.get("/parcels")
def parcels(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    context: SurveyorContext = Depends(current_context),
):
    layers = _require("parcels")
    found, total = layers.query(SOURCE, "parcels", geometry="none", order="uid", limit=limit, offset=offset)
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "count": len(found),
        "parcels": [feature["properties"] for feature in found],
    }


@router.get("/parcels/{parcel_id}")
def parcel(parcel_id: str, context: SurveyorContext = Depends(current_context)):
    layers = _require("parcels")
    feature = layers.get(SOURCE, parcel_id, kind="parcels", geometry="detail")
    if feature is None:
        raise HTTPException(status_code=404, detail=f"Parcel '{parcel_id}' not found")
    return {"type": "Feature", "properties": feature["properties"], "geometry": feature["geometry"]}


@router.get("/parcels-geojson")
def parcels_geojson(
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    return map_service.features(context, settings, store, source=SOURCE, layer="parcels", zoom=19)


@router.get("/landcover")
def landcover(
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    class_name: Optional[str] = None,
    context: SurveyorContext = Depends(current_context),
):
    layers = _require("landcover")
    classes = [class_key(class_name)] if class_name else None
    found, total = layers.query(
        SOURCE, "landcover", classes=classes, geometry="detail", order="uid", limit=limit, offset=offset
    )
    return {"total": total, "offset": offset, "limit": limit, "count": len(found), "features": found}


@router.get("/landcover-geojson")
def landcover_geojson(
    class_name: Optional[str] = None,
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    classes = [class_key(class_name)] if class_name else None
    return map_service.features(
        context, settings, store, source=SOURCE, layer="landcover", classes=classes, zoom=19
    )


@router.get("/landcover-summary")
def landcover_summary(context: SurveyorContext = Depends(current_context)):
    layers = _require("landcover")
    summary = layers.stats(SOURCE, "landcover")
    return {
        "total_features": summary["totals"]["features"],
        "class_counts": {c["class_name"]: c["count"] for c in summary["classes"].values()},
        "class_areas": {c["class_name"]: round(c["area_m2"], 4) for c in summary["classes"].values()},
    }


@router.get("/analytics")
def analytics(
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    return analytics_service.overview(context, settings, store, SOURCE)


@router.get("/export/geojson")
def export_geojson(
    layer: str = Query("parcels"),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    try:
        stream = export_service.geojson_stream(context, settings, store, source=SOURCE, layer=layer)
    except export_service.ExportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return StreamingResponse(
        stream,
        media_type="application/geo+json",
        headers={"Content-Disposition": f'attachment; filename="cadastra_vision_{layer}.geojson"'},
    )
