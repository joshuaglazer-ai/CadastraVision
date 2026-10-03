"""Surveyor profile and assignment, derived from the authenticated account."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.core.deps import current_context
from backend.services.assignment_service import SurveyorContext, boundary_feature_collection

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
