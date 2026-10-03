"""Measured terrain: DSM, DTM and building height (DSM − DTM)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.config import settings
from backend.core.deps import current_context, service_error, source_param
from backend.services import map_service, terrain_service
from backend.services.assignment_service import SurveyorContext

router = APIRouter(prefix="/api/terrain", tags=["Terrain"])


def _bbox(text: Optional[str]):
    try:
        return map_service.parse_bbox(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/status")
def terrain_status(context: SurveyorContext = Depends(current_context)):
    return terrain_service.status(settings)


@router.get("/grid")
def terrain_grid(
    bbox: str = Query(..., description="min_lon,min_lat,max_lon,max_lat"),
    surface: str = Query(default="dtm"),
    size: int = Query(default=128, ge=8, le=192),
    context: SurveyorContext = Depends(current_context),
):
    try:
        return terrain_service.grid(settings, _bbox(bbox), surface=surface, size=size)
    except terrain_service.TerrainError as exc:
        raise service_error(exc)


@router.get("/buildings")
def terrain_buildings(
    bbox: Optional[str] = Query(default=None),
    source: str = Depends(source_param),
    context: SurveyorContext = Depends(current_context),
):
    try:
        return terrain_service.building_heights(settings, source, _bbox(bbox))
    except terrain_service.TerrainError as exc:
        raise service_error(exc)
