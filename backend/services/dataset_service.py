"""Dataset discovery across the survey data sources.

Every source category is always listed, with whatever is really on disk.
An empty category is reported as NOT AVAILABLE; nothing is invented to make
the list look complete. Only file names relative to the data directory are
exposed, never server paths.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np

from backend.config import Settings
from backend.core import runtime
from backend.core.store import Store
from backend.gis.geojson_io import GeoJSONError, crs_name, lonlat_transformer, read_header

RASTER_EXT = {".tif", ".tiff"}
VECTOR_EXT = {".geojson", ".json", ".gpkg", ".shp", ".kml"}
DOCUMENT_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx"}
POINT_EXT = {".csv", ".geojson", ".json", ".gpx"}

# Largest vector file that is read whole to draw as a reference overlay.
OVERLAY_MAX_BYTES = 25 * 1024 * 1024

SOURCE_TYPES: list[dict[str, Any]] = [
    {
        "key": "drone",
        "label": "Drone / ORI imagery",
        "description": "High-resolution orthorectified drone imagery (GeoTIFF). This is the input to AI segmentation.",
        "dirs": ["imagery"],
        "extensions": RASTER_EXT,
        "kind": "raster",
        "upload": True,
    },
    {
        "key": "satellite",
        "label": "Satellite imagery",
        "description": "Satellite scenes for additional spatial context (GeoTIFF).",
        "dirs": ["satellite"],
        "extensions": RASTER_EXT,
        "kind": "raster",
        "upload": True,
    },
    {
        "key": "dsm",
        "label": "DSM",
        "description": "Digital surface model. Together with a DTM it gives measured building height (DSM − DTM).",
        "dirs": ["dsm"],
        "extensions": RASTER_EXT,
        "kind": "raster",
        "upload": True,
    },
    {
        "key": "dtm",
        "label": "DTM",
        "description": "Digital terrain model (bare ground elevation).",
        "dirs": ["dtm"],
        "extensions": RASTER_EXT,
        "kind": "raster",
        "upload": True,
    },
    {
        "key": "ai_layers",
        "label": "AI-derived GIS layers",
        "description": "Land-cover features and candidate parcels produced by the segmentation model. Preliminary until verified.",
        "dirs": [],
        "extensions": set(),
        "kind": "vector",
        "upload": False,
    },
    {
        "key": "land_records",
        "label": "Existing maps and land records",
        "description": "Existing cadastral / revenue GIS layers. When present, AI features are overlaid on them for parcel reasoning.",
        "dirs": ["land_records"],
        "extensions": VECTOR_EXT,
        "kind": "vector",
        "upload": True,
    },
    {
        "key": "gis",
        "label": "GIS reference data",
        "description": "Roads, utilities and other infrastructure layers.",
        "dirs": ["gis"],
        "extensions": VECTOR_EXT,
        "kind": "vector",
        "upload": True,
    },
    {
        "key": "survey_of_india",
        "label": "Survey of India data",
        "description": "Survey of India map sheets and control data.",
        "dirs": ["survey_of_india"],
        "extensions": VECTOR_EXT | RASTER_EXT,
        "kind": "mixed",
        "upload": True,
    },
    {
        "key": "documents",
        "label": "Owner documents",
        "description": "Land and ownership documents supplied by owners. Stored for the surveyor's reference; not read by the AI.",
        "dirs": ["documents"],
        "extensions": DOCUMENT_EXT,
        "kind": "document",
        "upload": True,
    },
    {
        "key": "gnss",
        "label": "GNSS / ground truth",
        "description": "Field positions from GNSS or IoT devices, and ground-truth observations recorded during review.",
        "dirs": ["gnss"],
        "extensions": POINT_EXT,
        "kind": "points",
        "upload": True,
    },
]
SOURCE_BY_KEY = {item["key"]: item for item in SOURCE_TYPES}

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_raster_cache: dict[tuple[str, float, int], dict[str, Any]] = {}
_vector_cache: dict[tuple[str, float, int], dict[str, Any]] = {}


class DatasetError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def safe_filename(name: str) -> str:
    """Reduce an uploaded file name to a safe base name."""

    base = Path(str(name).replace("\\", "/")).name.strip()
    base = _SAFE_NAME.sub("_", base).strip("._")
    if not base:
        raise DatasetError("The file has no usable name.")
    return base[:120]


def dataset_id_for(relative_path: str) -> str:
    return "DS-" + hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:10].upper()


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds")


def _relative(path: Path, settings: Settings) -> str:
    try:
        return path.resolve().relative_to(settings.data_dir.resolve()).as_posix()
    except ValueError:
        return path.name


# ------------------------------------------------------------------ metadata
def raster_metadata(path: Path) -> dict[str, Any]:
    stat = path.stat()
    key = (str(path), stat.st_mtime, stat.st_size)
    if key in _raster_cache:
        return _raster_cache[key]
    try:
        from backend.ai.raster import inspect_raster

        meta = inspect_raster(path)
        meta.pop("crs_wkt", None)
        result = {"ok": True, **meta}
    except Exception as exc:  # RasterError, missing rasterio, corrupt file
        result = {"ok": False, "error": str(exc)}
    _raster_cache[key] = result
    return result


def _bbox_of_coordinates(coordinates, box: list[float]) -> None:
    if not isinstance(coordinates, (list, tuple)) or not coordinates:
        return
    if isinstance(coordinates[0], (int, float)):
        x, y = float(coordinates[0]), float(coordinates[1])
        if math.isfinite(x) and math.isfinite(y):
            box[0] = min(box[0], x)
            box[1] = min(box[1], y)
            box[2] = max(box[2], x)
            box[3] = max(box[3], y)
        return
    for item in coordinates:
        _bbox_of_coordinates(item, box)


def vector_metadata(path: Path) -> dict[str, Any]:
    """Feature count, CRS, geometry types and extent of a GeoJSON file."""

    stat = path.stat()
    key = (str(path), stat.st_mtime, stat.st_size)
    if key in _vector_cache:
        return _vector_cache[key]

    suffix = path.suffix.lower()
    if suffix not in (".geojson", ".json"):
        result = {
            "ok": True,
            "note": f"{suffix[1:].upper()} files are listed but not inspected; convert to GeoJSON to overlay them.",
        }
        _vector_cache[key] = result
        return result

    try:
        from backend.gis.geojson_io import scan_feature_collection

        box = [math.inf, math.inf, -math.inf, -math.inf]
        types: dict[str, int] = {}

        def on_feature(feature: dict[str, Any]) -> None:
            geometry = (feature or {}).get("geometry") or {}
            kind = geometry.get("type") or "None"
            types[kind] = types.get(kind, 0) + 1
            _bbox_of_coordinates(geometry.get("coordinates"), box)

        header = scan_feature_collection(path, on_feature)
        crs = crs_name(header)
        extent = None
        if math.isfinite(box[0]):
            corners = np.array([[box[0], box[1]], [box[2], box[3]]], dtype=np.float64)
            transform = lonlat_transformer(crs)
            if transform is not None:
                corners = transform(corners)
            extent = [
                float(min(corners[:, 0])),
                float(min(corners[:, 1])),
                float(max(corners[:, 0])),
                float(max(corners[:, 1])),
            ]
        result = {
            "ok": True,
            "crs": crs,
            "feature_count": int(header.get("feature_count") or 0),
            "geometry_types": types,
            "bounds_lonlat": extent,
        }
        # Read as longitude/latitude (no crs member, or a geographic one) but
        # the numbers cannot be degrees: almost always projected coordinates,
        # such as UTM, saved without their CRS. Drawing them would be wrong.
        if extent and (max(abs(extent[0]), abs(extent[2])) > 180 or max(abs(extent[1]), abs(extent[3])) > 90):
            declared = "declares no CRS" if not header.get("crs") else f"declares {crs}"
            result = {
                "ok": False,
                "error": (
                    f"The file {declared}, so its coordinates are read as longitude/latitude, but they "
                    f"reach x {max(abs(extent[0]), abs(extent[2])):,.0f} and y {max(abs(extent[1]), abs(extent[3])):,.0f}, "
                    "which are not degrees. They look like projected coordinates (for example UTM). Export the "
                    "file again in EPSG:4326, or keep EPSG:32643 and include its \"crs\" member."
                ),
                "crs": crs,
                "feature_count": int(header.get("feature_count") or 0),
            }
    except GeoJSONError as exc:
        result = {"ok": False, "error": str(exc)}
    _vector_cache[key] = result
    return result


def read_points(path: Path) -> list[dict[str, Any]]:
    """Points from a GNSS CSV (latitude / longitude columns) or GeoJSON."""

    suffix = path.suffix.lower()
    points: list[dict[str, Any]] = []
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                return points
            lookup = {name.strip().lower(): name for name in reader.fieldnames if name}
            lat_key = next((lookup[k] for k in ("lat", "latitude", "y", "northing_deg") if k in lookup), None)
            lon_key = next((lookup[k] for k in ("lon", "lng", "long", "longitude", "x", "easting_deg") if k in lookup), None)
            if not lat_key or not lon_key:
                raise DatasetError(
                    f"{path.name} needs 'latitude' and 'longitude' columns (decimal degrees, WGS 84)."
                )
            for row_number, row in enumerate(reader, start=2):
                try:
                    lat = float(row[lat_key])
                    lon = float(row[lon_key])
                except (TypeError, ValueError):
                    continue
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    continue
                properties = {k: v for k, v in row.items() if k not in (lat_key, lon_key) and k}
                properties["row"] = row_number
                points.append({"lon": lon, "lat": lat, "properties": properties})
    elif suffix in (".geojson", ".json"):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        for feature in data.get("features") or []:
            geometry = (feature or {}).get("geometry") or {}
            if geometry.get("type") == "Point":
                lon, lat = geometry["coordinates"][:2]
                points.append(
                    {"lon": float(lon), "lat": float(lat), "properties": feature.get("properties") or {}}
                )
    return points


# ----------------------------------------------------------------- discovery
def _file_entry(path: Path, source: dict[str, Any], settings: Settings) -> dict[str, Any]:
    stat = path.stat()
    relative = _relative(path, settings)
    suffix = path.suffix.lower()
    entry: dict[str, Any] = {
        "dataset_id": dataset_id_for(relative),
        "name": path.name,
        "source_type": source["key"],
        "source_label": source["label"],
        "kind": "raster" if suffix in RASTER_EXT and source["kind"] != "document" else source["kind"],
        "origin": "Local file",
        "relative_path": relative,
        "size_bytes": stat.st_size,
        "date": _iso(stat.st_mtime),
        "crs": None,
        "resolution_m": None,
        "extent": None,
        "feature_count": None,
        "status": "AVAILABLE",
        "processing_state": None,
        "notes": [],
    }

    if entry["kind"] == "raster":
        meta = raster_metadata(path)
        if not meta.get("ok"):
            entry["status"] = "REQUIRES REVIEW"
            entry["notes"].append(meta.get("error") or "Raster could not be inspected.")
        else:
            entry["crs"] = meta.get("crs")
            entry["resolution_m"] = meta.get("resolution_m")
            entry["extent"] = meta.get("bounds_lonlat")
            entry["width"] = meta.get("width")
            entry["height"] = meta.get("height")
            entry["band_count"] = meta.get("band_count")
            entry["dtypes"] = meta.get("dtypes")
            if not meta.get("crs"):
                entry["status"] = "REQUIRES REVIEW"
                entry["notes"].append("No coordinate reference system.")
    elif source["kind"] in ("vector", "mixed"):
        entry["kind"] = "vector"
        meta = vector_metadata(path)
        if not meta.get("ok"):
            entry["status"] = "REQUIRES REVIEW"
            entry["notes"].append(meta.get("error") or "Vector file could not be inspected.")
        else:
            entry["crs"] = meta.get("crs")
            entry["feature_count"] = meta.get("feature_count")
            entry["extent"] = meta.get("bounds_lonlat")
            entry["geometry_types"] = meta.get("geometry_types")
            if meta.get("note"):
                entry["notes"].append(meta["note"])
    elif source["kind"] == "points":
        try:
            points = read_points(path)
            entry["feature_count"] = len(points)
            entry["crs"] = "EPSG:4326"
            if points:
                lons = [p["lon"] for p in points]
                lats = [p["lat"] for p in points]
                entry["extent"] = [min(lons), min(lats), max(lons), max(lats)]
            else:
                entry["status"] = "REQUIRES REVIEW"
                entry["notes"].append("No usable points were found in the file.")
        except (DatasetError, ValueError, OSError) as exc:
            entry["status"] = "REQUIRES REVIEW"
            entry["notes"].append(str(exc))
    elif source["kind"] == "document":
        entry["status"] = "REQUIRES REVIEW"
        entry["notes"].append("Owner document: to be read and checked by the surveyor.")
    return entry


def _scan(source: dict[str, Any], settings: Settings) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for name in source["dirs"]:
        directory = settings.data_dir / name
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix.lower() in source["extensions"]:
                entries.append(_file_entry(path, source, settings))
    return entries


def _layer_entry(layer: dict[str, Any], source_key: str, origin: str, name: str) -> dict[str, Any]:
    return {
        "dataset_id": "DS-" + hashlib.sha1(layer["layer_key"].encode()).hexdigest()[:10].upper(),
        "name": name,
        "source_type": source_key,
        "source_label": SOURCE_BY_KEY[source_key]["label"],
        "kind": "vector",
        "origin": origin,
        "relative_path": Path(layer["path"]).name,
        "size_bytes": layer.get("file_size"),
        "date": _iso(layer["file_mtime"]) if layer.get("file_mtime") else None,
        "crs": layer.get("crs"),
        "resolution_m": None,
        "extent": layer.get("bbox"),
        "feature_count": layer.get("feature_count"),
        "status": "AVAILABLE",
        "processing_state": "PROCESSED",
        "layer_source": layer["source"],
        "layer_kind": layer["kind"],
        "notes": (
            [f"{layer['skipped_count']} feature(s) could not be read and are excluded."]
            if layer.get("skipped_count")
            else []
        )
        + ["AI generated / preliminary. Requires surveyor verification."],
    }


def discover(settings: Settings, store: Store, surveyor_id: str | None = None) -> dict[str, Any]:
    """Return every source category with its datasets and status."""

    jobs = store.list_jobs(limit=200)
    jobs_by_input: dict[str, dict[str, Any]] = {}
    for job in reversed(jobs):  # newest wins
        jobs_by_input[job["input_dataset"]] = job

    layers = runtime.get_layers()
    categories: list[dict[str, Any]] = []

    for source in SOURCE_TYPES:
        datasets: list[dict[str, Any]] = []
        notes: list[str] = []

        if source["key"] == "ai_layers":
            existing = runtime.ensure_source(runtime.EXISTING_SOURCE)
            files = runtime.data_files()
            names = {"parcels": "Candidate parcels", "landcover": "Land-cover features"}
            for kind, layer in existing.items():
                if layer:
                    entry = _layer_entry(layer, "ai_layers", "Existing project layer", names[kind])
                    if files[kind]["status"] == "fallback":
                        entry["notes"].insert(0, files[kind]["message"])
                    datasets.append(entry)
                else:
                    # Say which file is expected and where, not only "unavailable".
                    notes.append(f"{names[kind]}: {runtime.unavailable_reason(kind)}")
            for key, message in runtime.bootstrap_errors().items():
                if not key.startswith(f"{runtime.EXISTING_SOURCE}|"):
                    notes.append(f"{key.split('|')[-1]}: {message}")
            for job in jobs:
                if job["status"] != "COMPLETED":
                    continue
                for layer in layers.layers(runtime.job_source(job["job_id"])):
                    datasets.append(
                        _layer_entry(
                            layer,
                            "ai_layers",
                            f"Processing job {job['job_id']}",
                            f"{names.get(layer['kind'], layer['kind'])} · {job['job_id']}",
                        )
                    )
        else:
            datasets.extend(_scan(source, settings))

        if source["key"] == "drone":
            # Imagery uploaded straight into a processing job.
            listed = {d["name"] for d in datasets}
            for job in jobs:
                path = Path(job.get("input_path") or "")
                if job["input_dataset"] in listed or not path.exists():
                    continue
                if settings.upload_dir.resolve() not in path.resolve().parents:
                    continue
                entry = _file_entry(path, source, settings)
                entry["origin"] = f"Uploaded for {job['job_id']}"
                entry["relative_path"] = path.name
                entry["dataset_id"] = "DS-" + hashlib.sha1(job["job_id"].encode()).hexdigest()[:10].upper()
                datasets.append(entry)
                listed.add(entry["name"])
            for entry in datasets:
                job = jobs_by_input.get(entry["name"])
                if job:
                    entry["processing_state"] = {
                        "COMPLETED": "PROCESSED",
                        "PROCESSING": "PROCESSING",
                        "QUEUED": "PROCESSING",
                        "FAILED": "FAILED",
                        "UPLOADED": "NOT STARTED",
                    }.get(job["status"], job["status"])
                    entry["job_id"] = job["job_id"]
                    if entry["status"] == "AVAILABLE" and job["status"] in ("QUEUED", "PROCESSING"):
                        entry["status"] = "PROCESSING"
                    elif entry["status"] == "AVAILABLE" and job["status"] == "COMPLETED":
                        entry["status"] = "PROCESSED"
                else:
                    entry["processing_state"] = "NOT STARTED"

        if source["key"] == "gnss":
            truth = store.ground_truth_records()
            located = [
                r for r in truth if (r.get("ground_truth") or {}).get("latitude") is not None
            ]
            if truth:
                datasets.append(
                    {
                        "dataset_id": "DS-GROUNDTRUTH",
                        "name": "Ground-truth observations (review workflow)",
                        "source_type": "gnss",
                        "source_label": source["label"],
                        "kind": "points",
                        "origin": "Recorded by surveyors",
                        "relative_path": None,
                        "size_bytes": None,
                        "date": truth[0]["created_at"],
                        "crs": "EPSG:4326",
                        "resolution_m": None,
                        "extent": None,
                        "feature_count": len(truth),
                        "status": "AVAILABLE",
                        "processing_state": None,
                        "notes": [f"{len(located)} with a GNSS position."],
                    }
                )

        status = "AVAILABLE" if datasets else "NOT AVAILABLE"
        if datasets and all(d["status"] == "REQUIRES REVIEW" for d in datasets):
            status = "REQUIRES REVIEW"

        categories.append(
            {
                "key": source["key"],
                "label": source["label"],
                "description": source["description"],
                "kind": source["kind"],
                "upload_allowed": source["upload"],
                "accepted_extensions": sorted(source["extensions"]),
                "status": status,
                "count": len(datasets),
                "datasets": datasets,
                "notes": notes,
                "empty_message": None if datasets else "Awaiting dataset",
            }
        )

    return {
        "categories": categories,
        "summary": {
            "available_categories": sum(1 for c in categories if c["status"] != "NOT AVAILABLE"),
            "total_categories": len(categories),
            "total_datasets": sum(c["count"] for c in categories),
        },
        "basemaps": [
            {
                "name": "Esri World Imagery",
                "type": "External satellite basemap service",
                "note": "Online reference imagery for orientation only. It is not an input to AI processing.",
            },
            {
                "name": "OpenStreetMap",
                "type": "External street basemap service",
                "note": "Online reference map for orientation only.",
            },
        ],
    }


def find_dataset(dataset_id: str, settings: Settings, store: Store) -> dict[str, Any] | None:
    for category in discover(settings, store)["categories"]:
        for dataset in category["datasets"]:
            if dataset["dataset_id"] == dataset_id:
                return dataset
    return None


def resolve_path(dataset: dict[str, Any], settings: Settings) -> Path | None:
    """Absolute path of a discovered file dataset, confined to the data dir."""

    relative = dataset.get("relative_path")
    if not relative:
        return None
    candidate = (settings.data_dir / relative).resolve()
    if settings.data_dir.resolve() not in candidate.parents:
        return None
    return candidate if candidate.exists() else None


def first_raster(source_key: str, settings: Settings) -> Path | None:
    """The first usable raster of a category (used for DSM / DTM)."""

    source = SOURCE_BY_KEY[source_key]
    for name in source["dirs"]:
        directory = settings.data_dir / name
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix.lower() in RASTER_EXT:
                return path
    return None


# -------------------------------------------------------------------- upload
def save_upload(
    source_key: str,
    filename: str,
    stream: BinaryIO,
    settings: Settings,
) -> Path:
    """Store an uploaded dataset in its category directory."""

    source = SOURCE_BY_KEY.get(source_key)
    if source is None or not source["upload"] or not source["dirs"]:
        raise DatasetError(f"Datasets cannot be uploaded to '{source_key}'.")

    name = safe_filename(filename)
    suffix = Path(name).suffix.lower()
    if suffix not in source["extensions"]:
        raise DatasetError(
            f"{source['label']} accepts {', '.join(sorted(source['extensions']))}; got '{suffix or 'no extension'}'."
        )

    directory = (settings.data_dir / source["dirs"][0]).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / name
    if destination.resolve().parent != directory:
        raise DatasetError("Invalid file name.")
    if destination.exists():
        stem, counter = destination.stem, 2
        while destination.exists():
            destination = directory / f"{stem}_{counter}{suffix}"
            counter += 1

    limit = settings.max_upload_mb * 1024 * 1024
    written = 0
    try:
        with destination.open("wb") as out:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                written += len(block)
                if written > limit:
                    raise DatasetError(
                        f"The file is larger than the {settings.max_upload_mb} MB upload limit.", 413
                    )
                out.write(block)
    except DatasetError:
        destination.unlink(missing_ok=True)
        raise
    if written == 0:
        destination.unlink(missing_ok=True)
        raise DatasetError("The uploaded file is empty.")
    return destination


def copy_stream(stream: BinaryIO, destination: Path, limit_bytes: int) -> int:
    """Copy an upload to ``destination`` with a size limit. Returns bytes written."""

    written = 0
    try:
        with destination.open("wb") as out:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                written += len(block)
                if written > limit_bytes:
                    raise DatasetError(
                        f"The file is larger than the {limit_bytes // (1024 * 1024)} MB upload limit.",
                        413,
                    )
                out.write(block)
    except DatasetError:
        shutil.rmtree(destination.parent, ignore_errors=True)
        raise
    return written


# ------------------------------------------------------------------ overlays
def _map_coordinates(coordinates, transform):
    if not isinstance(coordinates, (list, tuple)) or not coordinates:
        return coordinates
    if isinstance(coordinates[0], (int, float)):
        point = transform(np.array([[float(coordinates[0]), float(coordinates[1])]]))
        return [float(point[0, 0]), float(point[0, 1])]
    return [_map_coordinates(item, transform) for item in coordinates]


_reference_cache: dict[tuple, list[dict[str, Any]]] = {}


def _reference_features(dataset: dict[str, Any], settings: Settings) -> list[dict[str, Any]]:
    """Features of a reference GeoJSON dataset in lon/lat.

    Each feature's ``id`` is its position in the file, which is how a single
    record is addressed afterwards. The parsed file is cached until it changes.
    """

    path = resolve_path(dataset, settings)
    if path is None:
        raise DatasetError("Dataset file not found.", 404)
    if path.suffix.lower() not in (".geojson", ".json"):
        raise DatasetError("Only GeoJSON reference layers can be drawn on the map.")
    stat = path.stat()
    if stat.st_size > OVERLAY_MAX_BYTES:
        raise DatasetError(
            f"{path.name} is too large to draw directly ({stat.st_size / 1e6:.0f} MB)."
        )

    key = (str(path), stat.st_mtime, stat.st_size)
    cached = _reference_cache.get(key)
    if cached is not None:
        return cached

    try:
        header = read_header(path)
        transform = lonlat_transformer(crs_name(header))
    except GeoJSONError as exc:
        raise DatasetError(str(exc)) from exc
    data = json.loads(path.read_text(encoding="utf-8-sig"))

    features = []
    for index, feature in enumerate(data.get("features") or []):
        if not isinstance(feature, dict) or not isinstance(feature.get("geometry"), dict):
            continue
        geometry = feature["geometry"]
        if transform is not None:
            geometry = {
                "type": geometry.get("type"),
                "coordinates": _map_coordinates(geometry.get("coordinates"), transform),
            }
        features.append(
            {
                "type": "Feature",
                "id": index,
                "properties": feature.get("properties") or {},
                "geometry": geometry,
            }
        )

    if len(_reference_cache) > 8:
        _reference_cache.clear()
    _reference_cache[key] = features
    return features


def reference_overlay(dataset: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """A reference vector dataset as lon/lat GeoJSON for the map."""

    return {
        "type": "FeatureCollection",
        "name": dataset["name"],
        "dataset_id": dataset["dataset_id"],
        "source_type": dataset["source_type"],
        "features": _reference_features(dataset, settings),
    }


def reference_feature(dataset: dict[str, Any], settings: Settings, index: int) -> dict[str, Any]:
    """One record of a reference dataset, addressed by its position in the file."""

    for feature in _reference_features(dataset, settings):
        if feature["id"] == index:
            return feature
    raise DatasetError(f"{dataset['name']} has no feature {index}.", 404)


def gnss_points(settings: Settings, store: Store) -> dict[str, Any]:
    """GNSS files and located ground-truth observations as point features."""

    features: list[dict[str, Any]] = []
    source = SOURCE_BY_KEY["gnss"]
    for name in source["dirs"]:
        directory = settings.data_dir / name
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in (".csv", ".geojson", ".json"):
                continue
            try:
                points = read_points(path)
            except (DatasetError, ValueError, OSError):
                continue
            for point in points:
                features.append(
                    {
                        "type": "Feature",
                        "properties": {**point["properties"], "origin": "GNSS file", "file": path.name},
                        "geometry": {"type": "Point", "coordinates": [point["lon"], point["lat"]]},
                    }
                )

    for record in store.ground_truth_records():
        truth = record.get("ground_truth") or {}
        lat, lon = truth.get("latitude"), truth.get("longitude")
        if lat is None or lon is None:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "origin": "Ground truth",
                    "review_id": record["review_id"],
                    "feature_id": record["feature_id"],
                    "observed_class": truth.get("observed_class"),
                    "comment": record.get("comment"),
                    "surveyor_id": record["surveyor_id"],
                    "recorded_at": record["created_at"],
                    "accuracy_m": truth.get("accuracy_m"),
                },
                "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
            }
        )
    return {"type": "FeatureCollection", "features": features}
