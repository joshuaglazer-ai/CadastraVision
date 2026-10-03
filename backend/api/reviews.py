"""Surveyor review and audit trail.

AI proposes -> GIS validates -> Surveyor verifies.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.config import settings
from backend.core import runtime
from backend.core.deps import current_context, service_error, source_param, store_dep
from backend.core.store import ACTION_STATUS, VERIFICATION_STATUSES, Store
from backend.services import review_service
from backend.services.assignment_service import SurveyorContext

router = APIRouter(prefix="/api", tags=["Reviews"])


class ReviewCreate(BaseModel):
    feature_id: str
    action: str
    comment: str = ""
    source: Optional[str] = None
    edited_geometry: Optional[dict[str, Any]] = None
    ground_truth: Optional[dict[str, Any]] = None


class ReviewUpdate(BaseModel):
    comment: Optional[str] = None
    action: Optional[str] = None


@router.get("/reviews")
def list_reviews(
    status: Optional[str] = Query(default=None),
    feature_id: Optional[str] = Query(default=None),
    mine: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    source: str = Depends(source_param),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    """Review history for a source."""

    return {
        "source": source,
        "reviews": store.list_reviews(
            source=source,
            status=status,
            feature_id=feature_id,
            surveyor_id=context.surveyor_id if mine else None,
            limit=limit,
            offset=offset,
        ),
        "stats": store.review_stats(source),
        "actions": sorted(ACTION_STATUS),
        "statuses": list(VERIFICATION_STATUSES),
    }


@router.get("/reviews/queue")
def review_queue(
    group: str = Query(default="high"),
    include_fragments: bool = Query(default=False),
    limit: int = Query(default=50, ge=0, le=200),
    offset: int = Query(default=0, ge=0),
    source: str = Depends(source_param),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    try:
        return review_service.queue(
            context, settings, store, source=source, group=group,
            include_fragments=include_fragments, limit=limit, offset=offset,
        )
    except review_service.ReviewError as exc:
        raise service_error(exc)


@router.get("/reviews/feature/{feature_id}")
def reviews_for_feature(
    feature_id: str,
    source: str = Depends(source_param),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    reviews = store.list_reviews(feature_id=feature_id, source=source, limit=200)
    return {
        "feature_id": feature_id,
        "source": source,
        "status": store.effective_status(feature_id, source),
        "review_count": len(reviews),
        "reviews": reviews,
        "audit": store.list_audit(entity_id=f"{source}:{feature_id}", limit=200),
    }


@router.post("/reviews")
def submit_review(
    body: ReviewCreate,
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    """Record a decision. The surveyor is the authenticated account."""

    try:
        source = runtime.valid_source(body.source)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown layer source '{body.source}'.")
    try:
        return review_service.submit(
            context,
            settings,
            store,
            source=source,
            feature_id=body.feature_id,
            action=body.action,
            comment=body.comment,
            edited_geometry=body.edited_geometry,
            ground_truth=body.ground_truth,
        )
    except review_service.ReviewError as exc:
        raise service_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/reviews/{review_id}")
def get_review(
    review_id: str,
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    review = store.get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail=f"Review '{review_id}' not found.")
    return review


@router.patch("/reviews/{review_id}")
def patch_review(
    review_id: str,
    body: ReviewUpdate,
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    try:
        return review_service.update(
            context, store, review_id, comment=body.comment, action=body.action
        )
    except review_service.ReviewError as exc:
        raise service_error(exc)


@router.get("/audit")
def audit_trail(
    entity_id: Optional[str] = Query(default=None),
    mine: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    """Who did what, when, with the state before and after."""

    return {
        "events": store.list_audit(
            entity_id=entity_id,
            actor_id=context.surveyor_id if mine else None,
            limit=limit,
            offset=offset,
        )
    }
