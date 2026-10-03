"""Surveyor profile, assignment and work areas, derived from the authenticated account."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from backend.config import settings
from backend.core.deps import current_context, service_error, store_dep
from backend.core.store import Store
from backend.services import work_area_service
from backend.services.assignment_service import SurveyorContext, boundary_feature_collection
from backend.services.work_area_service import WorkAreaError

router = APIRouter(prefix="/api", tags=["Surveyor"])


@router.get("/surveyors/me")
def me(context: SurveyorContext = Depends(current_context)):
    return {
        "surveyor": context.surveyor,
        "assignment": context.assignment,
        "notes": context.notes,
    }


@router.get("/assignments/current")
def current_assignment(context: SurveyorContext = Depends(current_context)):
    if context.assignment is None:
        raise HTTPException(
            status_code=404,
            detail=context.notes[0] if context.notes else "No survey assignment found.",
        )
    return {
        "assignment": context.assignment,
        "surveyor": context.surveyor,
        "notes": context.notes,
    }


@router.get("/assignments/current/boundary")
def current_boundary(context: SurveyorContext = Depends(current_context)):
    if context.boundary is None:
        raise HTTPException(status_code=404, detail="No assigned survey boundary found.")
    return boundary_feature_collection(context)


# ---- work areas ---------------------------------------------------------
# The owner is always the signed-in account; nothing in the request names it.


@router.get("/assignments")
def list_assignments(
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    """Registry assignments for this e-mail, then the account's own work areas."""

    return work_area_service.list_areas(context, settings, store)


@router.post("/assignments/measure")
def measure_boundary(
    payload: dict[str, Any] = Body(...),
    context: SurveyorContext = Depends(current_context),
):
    """Check and measure a boundary (``{"boundary": ...}``) without saving it."""

    try:
        return work_area_service.measure(payload.get("boundary") if isinstance(payload, dict) else None)
    except WorkAreaError as exc:
        raise service_error(exc)


@router.post("/assignments", status_code=201)
def create_work_area(
    payload: dict[str, Any] = Body(...),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    try:
        return work_area_service.create_area(payload, context, store)
    except WorkAreaError as exc:
        raise service_error(exc)


@router.patch("/assignments/{area_id}")
def update_work_area(
    area_id: str,
    payload: dict[str, Any] = Body(...),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    try:
        return work_area_service.update_area(area_id, payload, context, store)
    except WorkAreaError as exc:
        raise service_error(exc)


@router.delete("/assignments/{area_id}")
def delete_work_area(
    area_id: str,
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    try:
        return work_area_service.delete_area(area_id, context, store)
    except WorkAreaError as exc:
        raise service_error(exc)


@router.post("/assignments/{assignment_id}/activate")
def activate_assignment(
    assignment_id: str,
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    try:
        return work_area_service.activate(assignment_id, context, settings, store)
    except WorkAreaError as exc:
        raise service_error(exc)
