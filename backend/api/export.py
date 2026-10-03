"""GIS-ready export."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse

from backend.ai.classes import class_key
from backend.config import settings
from backend.core.deps import current_context, service_error, source_param, store_dep
from backend.core.store import Store
from backend.services import export_service
from backend.services.assignment_service import SurveyorContext

router = APIRouter(prefix="/api/export", tags=["Export"])


def _filename(layer: str, status: str, extension: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = "" if status == "all" else f"_{status}"
    return f"cadastra_vision_{layer}{suffix}_{stamp}.{extension}"


def _classes(text: Optional[str]) -> Optional[list[str]]:
    if not text:
        return None
    return [class_key(item) for item in text.split(",") if item.strip()] or None


def _audit(store: Store, context: SurveyorContext, fmt: str, source: str, layer: str, status: str) -> None:
    store.add_audit(
        actor_id=context.surveyor_id,
        actor_email=context.email,
        action=f"export.{fmt}",
        entity_type="export",
        entity_id=f"{source}:{layer}",
        after={"status_filter": status},
    )


@router.get("/summary")
def export_summary(
    layer: str = Query(default="parcels"),
    status: str = Query(default="all"),
    source: str = Depends(source_param),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    """The provenance block that an export would carry."""

    try:
        export_service._check(source, layer, status)
    except export_service.ExportError as exc:
        raise service_error(exc)
    return export_service.metadata(
        context, settings, store, source=source, layer=layer, status_filter=status
    )


@router.get("/geojson")
def export_geojson(
    layer: str = Query(default="parcels"),
    status: str = Query(default="all"),
    classes: Optional[str] = Query(default=None),
    source: str = Depends(source_param),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    try:
        stream = export_service.geojson_stream(
            context, settings, store, source=source, layer=layer,
            classes=_classes(classes), status_filter=status,
        )
    except export_service.ExportError as exc:
        raise service_error(exc)
    _audit(store, context, "geojson", source, layer, status)
    return StreamingResponse(
        stream,
        media_type="application/geo+json",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename(layer, status, "geojson")}"'
        },
    )


@router.get("/csv")
def export_csv(
    layer: str = Query(default="parcels"),
    status: str = Query(default="all"),
    classes: Optional[str] = Query(default=None),
    source: str = Depends(source_param),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    try:
        stream = export_service.csv_stream(
            context, settings, store, source=source, layer=layer,
            classes=_classes(classes), status_filter=status,
        )
    except export_service.ExportError as exc:
        raise service_error(exc)
    _audit(store, context, "csv", source, layer, status)
    return StreamingResponse(
        stream,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename(layer, status, "csv")}"'
        },
    )


@router.get("/gpkg")
def export_gpkg(
    layer: str = Query(default="parcels"),
    status: str = Query(default="all"),
    classes: Optional[str] = Query(default=None),
    source: str = Depends(source_param),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    try:
        content = export_service.geopackage_bytes(
            context, settings, store, source=source, layer=layer,
            classes=_classes(classes), status_filter=status,
        )
    except export_service.ExportError as exc:
        raise service_error(exc)
    _audit(store, context, "gpkg", source, layer, status)
    return Response(
        content=content,
        media_type="application/geopackage+sqlite3",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename(layer, status, "gpkg")}"'
        },
    )
