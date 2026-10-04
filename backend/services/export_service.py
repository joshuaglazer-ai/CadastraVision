"""GIS-ready export with provenance.

Exports use the full-resolution geometry and carry, per feature, whether it
is still AI GENERATED or has been SURVEYOR VERIFIED / edited / flagged. The
collection metadata records the project, assignment, surveyor, processing
job, model and time. Preliminary output is never labelled as an official
cadastral record.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any, Iterator

from backend.ai.model import MODEL_LABEL
from backend.config import APP_NAME, DISCLAIMER, Settings
from backend.core import runtime
from backend.core.store import Store
from backend.services import map_service
from backend.services.assignment_service import SurveyorContext

EXPORT_LAYERS = ("parcels", "landcover", "plots")
STATUS_FILTERS = ("all", "verified", "unverified")

STATUS_LABEL = {
    "AI_GENERATED": "AI GENERATED",
    "REVIEW_REQUIRED": "AI GENERATED - REVIEW REQUIRED",
    "SURVEYOR_REVIEWED": "AI GENERATED - GROUND TRUTH RECORDED",
    "SURVEYOR_VERIFIED": "SURVEYOR VERIFIED",
    "EDITED": "SURVEYOR VERIFIED - GEOMETRY EDITED",
    "FLAGGED": "FLAGGED BY SURVEYOR",
    "REJECTED": "REJECTED BY SURVEYOR",
}
VERIFIED = ("SURVEYOR_VERIFIED", "EDITED")


class ExportError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _selected(status: str, status_filter: str) -> bool:
    if status_filter == "verified":
        return status in VERIFIED
    if status_filter == "unverified":
        return status not in VERIFIED
    return True


def _export_features(
    store: Store,
    source: str,
    layer: str,
    classes: list[str] | None,
    status_filter: str,
) -> Iterator[dict[str, Any]]:
    layers = runtime.get_layers()
    states = store.feature_states(source)
    # Every feature of a job names the model it came from; jobs that ran
    # before features carried it get it from the job record.
    model_attributes = {}
    job_model = _job_model(store, source)
    if job_model:
        model_attributes = {
            "model_id": job_model.get("id"),
            "model_file": job_model.get("file"),
            "model_hash": job_model.get("hash"),
        }
    for feature in layers.iter_full(source, layer, classes):
        state = states.get((source, feature["id"]))
        status = map_service.status_for(feature["properties"], state)
        if not _selected(status, status_filter):
            continue
        properties = dict(feature["properties"])
        properties.pop("layer", None)
        for key, value in model_attributes.items():
            if properties.get(key) is None:
                properties[key] = value
        properties["verification_status"] = status
        properties["status_label"] = STATUS_LABEL.get(status, status)
        properties["review_count"] = state["review_count"] if state else 0
        properties["last_review_at"] = state["last_review_at"] if state else None
        properties["last_reviewed_by"] = state["last_surveyor_id"] if state else None
        properties["geometry_source"] = "AI"
        geometry = feature["geometry"]
        if state and status == "EDITED" and state.get("edited_geometry"):
            geometry = state["edited_geometry"]
            properties["geometry_source"] = "SURVEYOR_EDIT"
        yield {"type": "Feature", "id": feature["id"], "properties": properties, "geometry": geometry}


def _job_model(store: Store, source: str) -> dict[str, Any] | None:
    if not source.startswith("job:"):
        return None
    job = store.get_job(source.split(":", 1)[1])
    return (job or {}).get("model") or None


def metadata(
    context: SurveyorContext,
    settings: Settings,
    store: Store,
    *,
    source: str,
    layer: str,
    status_filter: str,
) -> dict[str, Any]:
    job = None
    if source.startswith("job:"):
        job = store.get_job(source.split(":", 1)[1])

    assignment = context.assignment or {}
    review_stats = store.review_stats(source)
    return {
        "project": APP_NAME,
        "product": "AI + GIS Surveyor Assistance Platform",
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "layer": layer,
        "source": source,
        "status_filter": status_filter,
        "record_status": (
            "SURVEYOR VERIFIED FEATURES ONLY"
            if status_filter == "verified"
            else "AI GENERATED / PRELIMINARY (see verification_status per feature)"
        ),
        "disclaimer": DISCLAIMER,
        "legal_notice": (
            "This file is a survey-assistance product. It is not an official "
            "cadastral record and does not establish ownership or legal boundaries."
        ),
        "assignment": {
            "assignment_id": assignment.get("assignment_id"),
            "district": assignment.get("district"),
            "taluk": assignment.get("taluk"),
            "village": assignment.get("village"),
            "is_demo": assignment.get("is_demo"),
            # SELF-DECLARED WORK AREA for a boundary the surveyor drew or uploaded.
            "label": assignment.get("label"),
            "kind": assignment.get("kind"),
            "is_official": assignment.get("is_official"),
            "name": assignment.get("name"),
        },
        "surveyor": {
            "surveyor_id": context.surveyor_id,
            "name": context.surveyor.get("name"),
        },
        "processing_job": (
            {
                "job_id": job["job_id"],
                "input_dataset": job["input_dataset"],
                "generated_at": (job.get("summary") or {}).get("generated_at"),
                "completed_at": job["updated_at"],
            }
            if job
            else None
        ),
        "model": _model_metadata(store, source, settings),
        "verification": {
            "verified_features": review_stats["verified_count"] + review_stats["edited_count"],
            "flagged_features": review_stats["flagged_count"] + review_stats["rejected_count"],
            "total_reviews": review_stats["total_reviews"],
            "status_counts": review_stats["status_counts"],
        },
        "crs": "OGC:CRS84 (longitude, latitude; WGS 84)",
    }


def _model_metadata(store: Store, source: str, settings: Settings) -> dict[str, Any]:
    """The checkpoint behind the exported features."""

    status = "Predictions are model-derived and require surveyor verification."
    job_model = _job_model(store, source)
    if job_model:
        return {
            **job_model,
            "architecture": MODEL_LABEL.split(" (")[0],
            "normalization": settings.normalization,
            "status": status,
        }
    return {
        "id": None,
        "name": "Not recorded: existing project layers supplied as files",
        "file": None,
        "hash": None,
        "architecture": MODEL_LABEL.split(" (")[0],
        "status": status,
    }


def _check(source: str, layer: str, status_filter: str) -> None:
    if layer not in EXPORT_LAYERS:
        raise ExportError(f"Unknown layer '{layer}'. Use one of: {', '.join(EXPORT_LAYERS)}.")
    if status_filter not in STATUS_FILTERS:
        raise ExportError(f"Unknown status filter '{status_filter}'.")
    runtime.ensure_source(source)
    if runtime.get_layers().layer(source, layer) is None:
        raise ExportError("Data unavailable: this layer has not been generated for the source.", 404)


def geojson_stream(
    context: SurveyorContext,
    settings: Settings,
    store: Store,
    *,
    source: str,
    layer: str,
    classes: list[str] | None = None,
    status_filter: str = "all",
) -> Iterator[bytes]:
    """Yield a GeoJSON FeatureCollection in chunks."""

    _check(source, layer, status_filter)
    meta = metadata(context, settings, store, source=source, layer=layer, status_filter=status_filter)

    def generate() -> Iterator[bytes]:
        yield b'{"type":"FeatureCollection","name":' + json.dumps(f"cadastra_vision_{layer}").encode()
        yield b',"crs":{"type":"name","properties":{"name":"urn:ogc:def:crs:OGC:1.3:CRS84"}}'
        yield b',"metadata":' + json.dumps(meta, ensure_ascii=False).encode("utf-8")
        yield b',"features":[\n'
        first = True
        for feature in _export_features(store, source, layer, classes, status_filter):
            chunk = json.dumps(feature, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            yield chunk if first else b",\n" + chunk
            first = False
        yield b"\n]}\n"

    return generate()


CSV_COLUMNS = [
    "uid",
    "class_name",
    "verification_status",
    "status_label",
    "geometry_source",
    "area_m2",
    "perimeter_m",
    "length_m",
    "width_m",
    "confidence",
    "entropy",
    "review_priority",
    "review_reasons",
    "geometry_status",
    "nearest_road_distance_m",
    "road_access_candidate",
    "review_count",
    "last_review_at",
    "last_reviewed_by",
    "building_feature_id",
    "building_area_m2",
    "coverage_ratio",
    "buildings_inside",
    "delineation_method",
    "processing_job_id",
    "model_id",
    "model_file",
    "model_hash",
    "source_dataset",
    "generated_at",
]


def csv_stream(
    context: SurveyorContext,
    settings: Settings,
    store: Store,
    *,
    source: str,
    layer: str,
    classes: list[str] | None = None,
    status_filter: str = "all",
) -> Iterator[bytes]:
    """Attribute table as CSV (no geometry), with a provenance header."""

    _check(source, layer, status_filter)
    meta = metadata(context, settings, store, source=source, layer=layer, status_filter=status_filter)

    def generate() -> Iterator[bytes]:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        for line in (
            f"# {meta['project']} attribute export",
            f"# exported_at: {meta['exported_at']}",
            f"# layer: {layer}; source: {source}; status filter: {status_filter}",
            f"# assignment: {meta['assignment']['assignment_id']}; surveyor: {meta['surveyor']['surveyor_id']}",
            f"# model: {meta['model']['name']}"
            + (
                f" (id {meta['model']['id']}, file {meta['model']['file']}, sha256 {meta['model']['hash']})"
                if meta["model"].get("id")
                else ""
            ),
            f"# {meta['disclaimer']}",
        ):
            buffer.write(line + "\n")
        writer.writerow(CSV_COLUMNS)
        yield buffer.getvalue().encode("utf-8")

        for feature in _export_features(store, source, layer, classes, status_filter):
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            properties = feature["properties"]
            row = []
            for column in CSV_COLUMNS:
                value = properties.get(column)
                if isinstance(value, list):
                    value = "; ".join(str(item) for item in value)
                row.append("" if value is None else value)
            writer.writerow(row)
            yield buffer.getvalue().encode("utf-8")

    return generate()


def geopackage_bytes(
    context: SurveyorContext,
    settings: Settings,
    store: Store,
    *,
    source: str,
    layer: str,
    classes: list[str] | None = None,
    status_filter: str = "all",
) -> bytes:
    """GeoPackage export. Needs GeoPandas with a GPKG-capable engine."""

    _check(source, layer, status_filter)
    try:
        import geopandas as gpd
        from shapely.geometry import shape
    except ImportError as exc:
        raise ExportError(
            "GeoPackage export needs GeoPandas, which is not installed. Use GeoJSON instead.", 501
        ) from exc

    import tempfile
    from pathlib import Path

    records = []
    geometries = []
    for feature in _export_features(store, source, layer, classes, status_filter):
        properties = {}
        for key, value in feature["properties"].items():
            if isinstance(value, (list, dict)):
                value = json.dumps(value, ensure_ascii=False)
            properties[key] = value
        records.append(properties)
        geometries.append(shape(feature["geometry"]))
    if not records:
        raise ExportError("No features match this export.", 404)

    frame = gpd.GeoDataFrame(records, geometry=geometries, crs="EPSG:4326")
    meta = metadata(context, settings, store, source=source, layer=layer, status_filter=status_filter)
    layer_name = f"cadastra_vision_{layer}"
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "export.gpkg"
        try:
            frame.to_file(target, layer=layer_name, driver="GPKG")
            _write_gpkg_metadata(target, layer_name, meta)
        except Exception as exc:
            raise ExportError(f"GeoPackage could not be written: {exc}", 501) from exc
        return target.read_bytes()


def _flatten(prefix: str, value: Any, out: list[tuple[str, str]]) -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            _flatten(f"{prefix}.{key}" if prefix else str(key), inner, out)
    else:
        out.append((prefix, "" if value is None else (json.dumps(value) if isinstance(value, list) else str(value))))


def _write_gpkg_metadata(path, layer_name: str, meta: dict[str, Any]) -> None:
    """Embed the export metadata in the GeoPackage.

    A key / value attribute table ``cadastra_vision_metadata`` (registered in
    ``gpkg_contents``, so GIS software lists it) holds the model, processing
    job, assignment, surveyor, dates and the disclaimer; the feature layer's
    description carries the disclaimer as well.
    """

    import sqlite3

    rows: list[tuple[str, str]] = []
    _flatten("", meta, rows)
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(
            "CREATE TABLE cadastra_vision_metadata (key TEXT PRIMARY KEY NOT NULL, value TEXT)"
        )
        connection.executemany("INSERT INTO cadastra_vision_metadata (key, value) VALUES (?, ?)", rows)
        connection.execute(
            """INSERT INTO gpkg_contents (table_name, data_type, identifier, description, last_change)
               VALUES ('cadastra_vision_metadata', 'attributes', 'cadastra_vision_metadata', ?,
                       strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))""",
            ("Export metadata: model, processing job, assignment, surveyor, dates, disclaimer",),
        )
        connection.execute(
            "UPDATE gpkg_contents SET description = ? WHERE table_name = ?",
            (f"{meta['record_status']}. {meta['disclaimer']} {meta['legal_notice']}", layer_name),
        )
        connection.commit()
    finally:
        connection.close()
