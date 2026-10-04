"""The registry of segmentation checkpoints a processing job can use.

``backend/models/registry.json`` lists the checkpoints by id. A client only
ever sends an id; the file is looked up here, inside the models folder, so a
request can never name a path. An entry whose file is not present is listed
as unavailable, never an error.

Every entry must describe the architecture the application runs (U-Net /
ResNet34, six classes, 8-bit RGB scaled by 1/255). An entry that declares
anything else is listed as unavailable with the reason, because loading it
would fail or, worse, run with the wrong preprocessing.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from backend.config import Settings, display_path

DEFAULT_MODEL_ID = "village"
REGISTRY_FILE_NAME = "registry.json"

SUPPORTED = {"architecture": "unet_resnet34", "classes": 6, "normalization": "scale_255"}
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ ()-]{0,200}\.(pth|pt)$")

# Used when the registry file is absent: the default checkpoint only.
BUILTIN = [
    {
        "id": DEFAULT_MODEL_ID,
        "name": "Village model (SVAMITVA)",
        "file": "best_weighted_multiclass_unet.pth",
        "trained_on": "SVAMITVA drone orthoimagery of rural villages",
        "suits": "village",
        "licence": "Project checkpoint",
        "default": True,
        **SUPPORTED,
    }
]


class ModelChoiceError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


_hash_cache: dict[tuple[str, float, int], str] = {}
_hash_lock = threading.Lock()


def short_hash(path: Path) -> str:
    """First 12 hex digits of the file's SHA-256 (cached per file version)."""

    stat = path.stat()
    key = (str(path), stat.st_mtime, stat.st_size)
    with _hash_lock:
        if key in _hash_cache:
            return _hash_cache[key]
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            sha.update(block)
    digest = sha.hexdigest()[:12]
    with _hash_lock:
        _hash_cache[key] = digest
    return digest


def models_dir(settings: Settings) -> Path:
    return settings.configured_model_path.parent


def registry_path(settings: Settings) -> Path:
    return models_dir(settings) / REGISTRY_FILE_NAME


def _raw_entries(settings: Settings) -> tuple[list[dict[str, Any]], str | None]:
    path = registry_path(settings)
    if not path.exists():
        return [dict(entry) for entry in BUILTIN], f"{display_path(path)} not found; only the default model is listed."
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [dict(entry) for entry in BUILTIN], f"{display_path(path)} could not be read ({exc}); only the default model is listed."
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list) or not models:
        return [dict(entry) for entry in BUILTIN], f"{display_path(path)} lists no models; only the default model is listed."
    return [entry for entry in models if isinstance(entry, dict)], None


def _resolve(entry: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Validate one entry and find its file. Never raises."""

    model = {
        "id": str(entry.get("id") or ""),
        "name": str(entry.get("name") or entry.get("id") or "Unnamed model"),
        "file": str(entry.get("file") or ""),
        "trained_on": entry.get("trained_on"),
        "suits": entry.get("suits"),
        "licence": entry.get("licence"),
        "architecture": entry.get("architecture", SUPPORTED["architecture"]),
        "classes": entry.get("classes", SUPPORTED["classes"]),
        "normalization": entry.get("normalization", SUPPORTED["normalization"]),
        "default": bool(entry.get("default")),
        "available": False,
        "reason": None,
        "hash": None,
        "size_bytes": None,
        "_path": None,
    }
    if not _ID.match(model["id"]):
        model["reason"] = "The registry entry has an invalid id."
        return model
    if not _FILE.match(model["file"]):
        model["reason"] = "The registry entry must name a .pth or .pt file in the models folder, without a path."
        return model
    unsupported = [key for key, value in SUPPORTED.items() if model[key] != value]
    if unsupported:
        model["reason"] = (
            "Not supported by this application: "
            + ", ".join(f"{key} {model[key]!r} (needs {SUPPORTED[key]!r})" for key in unsupported)
        )
        return model

    folder = models_dir(settings)
    if model["file"] == settings.configured_model_path.name:
        path = settings.model_path  # the default checkpoint keeps its file-name fallback
    else:
        path = folder / model["file"]
    if not path.is_file():
        model["reason"] = f"File missing: put {model['file']} in {display_path(folder)}/."
        return model
    model["available"] = True
    model["_path"] = path
    model["size_bytes"] = path.stat().st_size
    return model


def list_models(settings: Settings, *, with_hash: bool = True) -> dict[str, Any]:
    """Every registered model with its availability. Paths are not included."""

    raw, note = _raw_entries(settings)
    models = []
    seen: set[str] = set()
    for entry in raw:
        model = _resolve(entry, settings)
        if model["id"] in seen:
            model["available"] = False
            model["reason"] = "Duplicate id in the registry; the first entry is used."
        seen.add(model["id"])
        if with_hash and model["available"]:
            model["hash"] = short_hash(model["_path"])
        models.append(model)
    default = next((m["id"] for m in models if m["default"]), None) or DEFAULT_MODEL_ID
    return {"models": [public(m) for m in models], "default": default, "note": note, "_resolved": models}


def public(model: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in model.items() if not key.startswith("_")}


def choose(settings: Settings, model_id: str | None) -> tuple[dict[str, Any], Path]:
    """The model a job will run with, and its file. Raises ``ModelChoiceError``."""

    listing = list_models(settings)
    wanted = model_id or listing["default"]
    if not _ID.match(str(wanted)):
        raise ModelChoiceError("Unknown model id.", 400)
    model = next((m for m in listing["_resolved"] if m["id"] == wanted), None)
    if model is None:
        raise ModelChoiceError(f"Unknown model '{wanted}'.", 400)
    if not model["available"]:
        raise ModelChoiceError(f"{model['name']} is unavailable. {model['reason']}", 409)
    return public(model), model["_path"]


def job_record(model: dict[str, Any], *, retrospective: bool = False) -> dict[str, Any]:
    """What a job keeps about the model it ran with."""

    record = {
        "id": model["id"],
        "name": model["name"],
        "file": model["file"],
        "hash": model.get("hash"),
        "trained_on": model.get("trained_on"),
        "suits": model.get("suits"),
        "licence": model.get("licence"),
    }
    if retrospective:
        record["note"] = "Recorded after the run: the job ran before models were selectable, with the default model."
    return record


def default_record(settings: Settings) -> dict[str, Any]:
    """The default model as a job record, for jobs that predate the registry."""

    listing = list_models(settings)
    model = next((m for m in listing["_resolved"] if m["id"] == listing["default"]), None)
    if model is None:
        model = _resolve(dict(BUILTIN[0]), settings)
        if model["available"]:
            model["hash"] = short_hash(model["_path"])
    return job_record(public(model), retrospective=True)
