"""Process-wide singletons and the catalogue of vector layer sources.

A *source* groups the layers that belong together on the map:

* ``existing``      - the layers shipped with the project (candidate parcels
                      and the land-cover features they were derived from)
* ``job:<JOB-ID>``  - the output of one AI processing job
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from backend.config import Settings, settings
from backend.core.store import Store, get_store
from backend.gis.layers import GeoJSONError, LayerStore

log = logging.getLogger("cadastra")

EXISTING_SOURCE = "existing"

_layers: LayerStore | None = None
_lock = threading.Lock()
_bootstrap_errors: dict[str, str] = {}


def get_settings() -> Settings:
    return settings


def get_layers() -> LayerStore:
    global _layers
    if _layers is None:
        with _lock:
            if _layers is None:
                settings.ensure_dirs()
                _layers = LayerStore(
                    settings.cache_dir / "layers.db",
                    sliver_area_m2=settings.sliver_area_m2,
                    min_parcel_area_m2=settings.min_candidate_parcel_m2,
                    road_access_distance_m=settings.road_access_distance_m,
                )
    return _layers


def set_layers(layers: LayerStore | None) -> None:
    """Replace the layer store (used by tests)."""

    global _layers
    _layers = layers


def job_source(job_id: str) -> str:
    return f"job:{job_id}"


def job_output_dir(job_id: str) -> Path:
    return settings.output_dir / job_id


def existing_layer_files() -> dict[str, Path]:
    return {"parcels": settings.parcels_file, "landcover": settings.landcover_file}


def ensure_source(source: str) -> dict[str, Any]:
    """Make sure the layers of ``source`` are indexed.

    Returns ``{kind: layer-record-or-None}``. A missing file yields ``None``
    (the UI then says "Data unavailable"); an unreadable file is recorded in
    :func:`bootstrap_errors` and also yields ``None`` rather than a crash.
    """

    layers = get_layers()
    result: dict[str, Any] = {}

    if source == EXISTING_SOURCE:
        files = existing_layer_files()
        labels = {
            "parcels": "Candidate parcels (existing layer)",
            "landcover": "Land-cover features (existing layer)",
        }
        origin = "existing"
    elif source.startswith("job:"):
        job_id = source.split(":", 1)[1]
        out = job_output_dir(job_id)
        files = {
            "parcels": out / "candidate_parcels.geojson",
            "landcover": out / "ai_features.geojson",
        }
        labels = {
            "parcels": f"Candidate parcels ({job_id})",
            "landcover": f"AI features ({job_id})",
        }
        origin = "processing_job"
    else:
        return result

    for kind, path in files.items():
        key = f"{source}|{kind}"
        try:
            result[kind] = layers.ensure(source, kind, path, label=labels[kind], origin=origin)
            _bootstrap_errors.pop(key, None)
        except GeoJSONError as exc:
            log.error("Layer %s could not be indexed: %s", key, exc)
            _bootstrap_errors[key] = str(exc)
            result[kind] = None
    return result


def bootstrap_errors() -> dict[str, str]:
    return dict(_bootstrap_errors)


def known_sources(store: Store | None = None) -> list[dict[str, Any]]:
    """Sources the map can show, newest job first after the existing layers."""

    store = store or get_store()
    sources: list[dict[str, Any]] = []

    files = existing_layer_files()
    sources.append(
        {
            "source": EXISTING_SOURCE,
            "label": "Existing GIS layers",
            "origin": "existing",
            "available": any(path.exists() for path in files.values()),
        }
    )
    for job in store.list_jobs(limit=50):
        if job["status"] != "COMPLETED":
            continue
        sources.append(
            {
                "source": job_source(job["job_id"]),
                "label": f"AI output · {job['job_id']} · {job['input_dataset']}",
                "origin": "processing_job",
                "job_id": job["job_id"],
                "created_at": job["created_at"],
                "available": (job_output_dir(job["job_id"]) / "ai_features.geojson").exists(),
            }
        )
    return sources


def valid_source(source: str | None, store: Store | None = None) -> str:
    """Normalise a client-supplied source name; reject unknown ones."""

    if not source or source == EXISTING_SOURCE:
        return EXISTING_SOURCE
    if source.startswith("job:"):
        job_id = source.split(":", 1)[1]
        job = (store or get_store()).get_job(job_id)
        if job is not None:
            return job_source(job["job_id"])
    raise KeyError(source)
