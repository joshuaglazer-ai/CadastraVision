"""Analytics computed from stored data."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.config import settings
from backend.core.deps import current_context, source_param, store_dep
from backend.core.store import Store
from backend.services import analytics_service
from backend.services.assignment_service import SurveyorContext

router = APIRouter(prefix="/api", tags=["Analytics"])


@router.get("/analytics")
def analytics(
    source: str = Depends(source_param),
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    return analytics_service.overview(context, settings, store, source)
