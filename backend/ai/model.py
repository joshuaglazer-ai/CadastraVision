"""The segmentation model: a U-Net with a ResNet34 encoder, six classes.

This is the same architecture the checkpoint ``best_weighted_multiclass_unet.pth``
was trained with. The checkpoint is a bare ``state_dict`` of 278 tensors
(``encoder.*``, ``decoder.blocks.*``, ``segmentation_head.0.*``); it is
loaded strictly, so any mismatch is an error and never a silent fallback.

Heavy imports (torch, segmentation_models_pytorch) happen inside the
functions so that the rest of the API works on a machine without them and
reports the model as unavailable instead of failing to start.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from backend.ai.classes import CLASS_NAMES, NUM_CLASSES  # re-exported for compatibility

ARCHITECTURE = "U-Net"
ENCODER = "resnet34"
IN_CHANNELS = 3
MODEL_STRIDE = 32
MODEL_LABEL = "U-Net / ResNet34, 6 classes (SVAMITVA-trained)"


class ModelLoadError(RuntimeError):
    """The model or its checkpoint could not be loaded. The message is
    written for the person operating the system."""


def create_model(device=None):
    """Create the six-class U-Net with a ResNet34 encoder (untrained)."""

    try:
        import torch
        import segmentation_models_pytorch as smp
    except ImportError as exc:
        raise ModelLoadError(
            "PyTorch and segmentation-models-pytorch are required to run the model "
            f"but could not be imported ({exc}). Install backend/requirements.txt."
        ) from exc

    if device is None:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    model = smp.Unet(
        encoder_name=ENCODER,
        encoder_weights=None,
        in_channels=IN_CHANNELS,
        classes=NUM_CLASSES,
    )
    return model.to(device), device


def _read_state_dict(path: Path, device) -> dict[str, Any]:
    import torch

    try:
        try:
            checkpoint = torch.load(path, map_location=device, weights_only=True)
        except TypeError:  # torch < 1.13 has no weights_only argument
            checkpoint = torch.load(path, map_location=device)
    except Exception as exc:
        raise ModelLoadError(f"Checkpoint {path.name} could not be read: {exc}") from exc

    # Common wrappers around a state_dict.
    if isinstance(checkpoint, dict):
        for key in ("state_dict", "model_state_dict", "model"):
            if key in checkpoint and isinstance(checkpoint[key], dict):
                checkpoint = checkpoint[key]
                break

    if not isinstance(checkpoint, dict):
        raise ModelLoadError(
            f"Checkpoint {path.name} does not contain a state_dict "
            f"(found {type(checkpoint).__name__})."
        )

    # Strip the "module." prefix written by nn.DataParallel.
    return {
        (key[len("module."):] if isinstance(key, str) and key.startswith("module.") else key): value
        for key, value in checkpoint.items()
    }


def load_model(path: Path | str, device=None):
    """Build the model and load ``path`` strictly. Returns ``(model, device)``."""

    path = Path(path)
    if not path.exists():
        raise ModelLoadError(
            f"Model checkpoint not found at {path}. Copy best_weighted_multiclass_unet.pth "
            "into backend/models/ or set CADASTRA_MODEL_PATH."
        )

    model, device = create_model(device)
    state_dict = _read_state_dict(path, device)

    expected = model.state_dict()
    missing = sorted(set(expected) - set(state_dict))
    unexpected = sorted(set(state_dict) - set(expected))
    mismatched = sorted(
        key
        for key in set(expected) & set(state_dict)
        if tuple(expected[key].shape) != tuple(getattr(state_dict[key], "shape", ()))
    )
    if missing or unexpected or mismatched:
        parts = []
        if missing:
            parts.append(f"{len(missing)} tensors missing (e.g. {missing[0]})")
        if unexpected:
            parts.append(f"{len(unexpected)} unexpected tensors (e.g. {unexpected[0]})")
        if mismatched:
            key = mismatched[0]
            parts.append(
                f"{len(mismatched)} shape mismatches (e.g. {key}: checkpoint "
                f"{tuple(state_dict[key].shape)} vs model {tuple(expected[key].shape)})"
            )
        raise ModelLoadError(
            f"Checkpoint {path.name} does not match {ARCHITECTURE}/{ENCODER} with "
            f"{NUM_CLASSES} classes: " + "; ".join(parts) + "."
        )

    model.load_state_dict(state_dict, strict=True)
    model.eval()
    return model, device


_cached: dict[str, Any] = {}
_cache_lock = threading.Lock()


def get_model(path: Path | str):
    """Load once per process and reuse (reloaded if the file changes).

    Only one model is held at a time: switching to another checkpoint
    releases the current one before the next is loaded.
    """

    path = Path(path)
    stamp = (str(path), path.stat().st_mtime, path.stat().st_size) if path.exists() else None
    with _cache_lock:
        if stamp is not None and _cached.get("stamp") == stamp:
            return _cached["model"], _cached["device"]
        release_model()
        model, device = load_model(path)
        _cached.update(stamp=stamp, model=model, device=device, path=str(path))
        return model, device


def release_model() -> None:
    """Drop the cached model and return its memory (GPU memory too)."""

    had_model = _cached.get("model") is not None
    _cached.clear()
    if not had_model:
        return
    import gc

    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # torch missing or no CUDA: nothing more to free
        pass


def loaded_model_path() -> str | None:
    """File of the model currently in memory, if any."""

    return _cached.get("path")


def model_status(path: Path | str, normalization: str) -> dict[str, Any]:
    """What the API reports about the model, without loading it."""

    path = Path(path)
    status: dict[str, Any] = {
        "label": MODEL_LABEL,
        "architecture": ARCHITECTURE,
        "encoder": ENCODER,
        "framework": "PyTorch",
        "input": "RGB, 8-bit",
        "classes": [{"id": cid, "name": name} for cid, name in CLASS_NAMES.items()],
        "normalization": normalization,
        "checkpoint_file": path.name,
        "checkpoint_present": path.exists(),
        "checkpoint_bytes": path.stat().st_size if path.exists() else None,
        "loaded": bool(_cached.get("model") is not None),
        "device": str(_cached["device"]) if _cached.get("device") is not None else None,
        "runtime_available": True,
        "runtime_error": None,
        "training_data": "SVAMITVA drone orthoimagery",
        "generalisation_note": (
            "The model can be applied to new compatible survey imagery, but its "
            "performance varies with geography, sensor, resolution, season, "
            "illumination and image quality. Every result carries model-derived "
            "confidence and entropy and requires surveyor verification."
        ),
    }
    try:
        import torch  # noqa: F401
        import segmentation_models_pytorch  # noqa: F401
    except Exception as exc:  # ImportError or a broken install
        status["runtime_available"] = False
        status["runtime_error"] = f"PyTorch runtime unavailable: {exc}"
    return status
