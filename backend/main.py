"""Cadastra Vision API.

    AI proposes -> GIS validates -> Surveyor verifies

Run from the repository root:

    uvicorn backend.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import (
    analytics,
    datasets,
    export,
    legacy,
    map as map_api,
    processing,
    reviews,
    surveyors,
    system,
    terrain,
)
from backend.config import API_VERSION, APP_NAME, APP_TAGLINE, settings
from backend.core import runtime
from backend.core.store import get_store
from backend.services import processing_service

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cadastra")


def _index_existing_layers() -> None:
    """Build the layer index in the background so start-up is immediate.

    The first run reads the GeoJSON layers (about half a minute for the
    Uplarshi data); afterwards the cache is reused.
    """

    try:
        indexed = runtime.ensure_source(runtime.EXISTING_SOURCE)
        for kind, record in indexed.items():
            if record:
                log.info("Layer '%s' ready: %s features", kind, f"{record['feature_count']:,}")
            else:
                log.warning("Layer '%s' is unavailable (file missing or unreadable).", kind)
    except Exception:  # never take the API down because of a data problem
        log.exception("Indexing the existing layers failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    store = get_store()
    interrupted = processing_service.recover_interrupted(store)
    if interrupted:
        log.warning("%s processing job(s) were interrupted by the last shutdown.", interrupted)
    if settings.auth_mode == "off":
        log.warning(
            "CADASTRA_AUTH=off: authentication is disabled. Use this for local development only."
        )
    elif not settings.supabase_url or not settings.supabase_key:
        log.warning("SUPABASE_URL / SUPABASE_KEY are not set: every request will be rejected.")
    threading.Thread(target=_index_existing_layers, name="cadastra-index", daemon=True).start()
    yield


app = FastAPI(
    title=f"{APP_NAME} API",
    description=(
        f"{APP_TAGLINE}. AI proposes, GIS validates, surveyors verify. "
        "AI-generated features are preliminary and are not legal cadastral ownership records."
    ),
    version=API_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

app.include_router(system.router)
app.include_router(surveyors.router)
app.include_router(datasets.router)
app.include_router(map_api.router)
app.include_router(processing.router)
app.include_router(reviews.router)
app.include_router(analytics.router)
app.include_router(export.router)
app.include_router(terrain.router)
app.include_router(legacy.router)
