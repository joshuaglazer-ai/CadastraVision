"""Map layers, spatially filtered features and feature details."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.ai.classes import class_key
from backend.config import settings
from backend.core import runtime
from backend.core.deps import current_context, source_param, store_dep
from backend.core.store import Store
from backend.services import dataset_service, map_service, parcel_service
from backend.services.assignment_service import SurveyorContext, boundary_feature_collection

router = APIRouter(prefix="/api", tags=["Map"])


def _bbox(text: Optional[str]):
    try:
        return map_service.parse_bbox(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _classes(text: Optional[str]) -> Optional[list[str]]:
    if not text:
        return None
    keys = [class_key(item) for item in text.split(",") if item.strip()]
    return keys or None


@router.get("/map/layers")
def map_layers(
    source: str = Depends(source_param),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    return map_service.layer_catalog(context, settings, store, source)


@router.get("/map/assigned-area")
def assigned_area(context: SurveyorContext = Depends(current_context)):
    collection = boundary_feature_collection(context)
    collection["available"] = bool(collection["features"])
    collection["notes"] = context.notes
    return collection


@router.get("/map/parcels")
def map_parcels(
    bbox: Optional[str] = Query(default=None),
    zoom: Optional[float] = Query(default=None, ge=0, le=24),
    limit: Optional[int] = Query(default=None, ge=1, le=20000),
    source: str = Depends(source_param),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    return map_service.features(
        context, settings, store, source=source, layer="parcels",
        bbox=_bbox(bbox), zoom=zoom, limit=limit,
    )


@router.get("/map/features")
def map_features(
    classes: Optional[str] = Query(default=None, description="Comma-separated class names"),
    bbox: Optional[str] = Query(default=None),
    zoom: Optional[float] = Query(default=None, ge=0, le=24),
    limit: Optional[int] = Query(default=None, ge=1, le=20000),
    source: str = Depends(source_param),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    return map_service.features(
        context, settings, store, source=source, layer="landcover",
        classes=_classes(classes), bbox=_bbox(bbox), zoom=zoom, limit=limit,
    )


@router.get("/map/gnss")
def map_gnss(
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    return dataset_service.gnss_points(settings, store)


@router.get("/map/features/{uid}")
def map_feature(
    uid: str,
    geometry: str = Query(default="overview", description="'overview' or 'detail'"),
    reasoning: bool = Query(default=True),
    source: str = Depends(source_param),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    detail = map_service.feature_detail(store, source=source, uid=uid, geometry=geometry)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Feature '{uid}' not found.")
    if reasoning and detail["properties"].get("layer") == "parcels":
        detail["reasoning"] = parcel_service.reason(detail, source=source, settings=settings)
    return detail


@router.get("/parcels/{parcel_id}")
def parcel_detail(
    parcel_id: str,
    source: str = Depends(source_param),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    """A candidate parcel with its attributes, review state and reasoning."""

    runtime.ensure_source(source)
    if runtime.get_layers().get(source, parcel_id, kind="parcels", geometry="none") is None:
        raise HTTPException(status_code=404, detail=f"Parcel '{parcel_id}' not found.")
    detail = map_service.feature_detail(store, source=source, uid=parcel_id)
    detail["reasoning"] = parcel_service.reason(detail, source=source, settings=settings)
    return detail
