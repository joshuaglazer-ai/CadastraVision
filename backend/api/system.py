"""Health, identity of the service, and runtime status."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.ai.model import model_status
from backend.config import API_VERSION, APP_NAME, APP_PRINCIPLE, APP_TAGLINE, DISCLAIMER, settings
from backend.core import runtime
from backend.core.deps import current_context, store_dep
from backend.core.store import Store
from backend.services.assignment_service import SurveyorContext

router = APIRouter(tags=["System"])


@router.get("/health")
def health():
    return {"status": "ok", "service": "cadastra-vision-api", "version": API_VERSION}


@router.get("/")
def root():
    return {
        "name": f"{APP_NAME} API",
        "tagline": APP_TAGLINE,
        "principle": APP_PRINCIPLE,
        "status": "running",
        "docs": "/docs",
        "version": API_VERSION,
    }


@router.get("/api/system/status")
def system_status(
    context: SurveyorContext = Depends(current_context),
    store: Store = Depends(store_dep),
):
    """Model availability, layer indexing state and configuration."""

    layers = runtime.get_layers()
    files = runtime.data_files()
    model = model_status(settings.model_path, settings.normalization)
    model["checkpoint_file_status"] = files["model"]
    return {
        "version": API_VERSION,
        "auth_mode": settings.auth_mode,
        "dev_session": bool(context.surveyor.get("is_dev_session")),
        "model": model,
        "pipeline": {
            "tile_size": settings.tile_size,
            "tile_overlap": settings.tile_overlap,
            "polygonize_chunk": settings.polygonize_chunk,
            "sieve_min_pixels": settings.sieve_min_pixels,
            "sliver_area_m2": settings.sliver_area_m2,
            "min_candidate_parcel_m2": settings.min_candidate_parcel_m2,
            "road_access_distance_m": settings.road_access_distance_m,
            "max_upload_mb": settings.max_upload_mb,
        },
        "layers": layers.layers(),
        "indexing": layers.progress(),
        "indexing_errors": runtime.bootstrap_errors(),
        # Which file each required input was read from, or why none was.
        "data_files": files,
        "sources": runtime.known_sources(store),
        "disclaimer": DISCLAIMER,
    }
