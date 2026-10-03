"""Inspect the model checkpoint with NumPy only (no PyTorch needed).

    python -m backend.scripts.inspect_checkpoint [path/to/checkpoint.pth]

Reports the tensors, checks them against the expected U-Net / ResNet34
six-class layout, and estimates the input normalisation used in training
from the statistics stored in the first batch-norm layer.
"""

from __future__ import annotations

import collections
import io
import json
import pickle
import sys
import zipfile
from pathlib import Path

import numpy as np

_DTYPES = {
    "FloatStorage": "<f4",
    "LongStorage": "<i8",
    "HalfStorage": "<f2",
    "DoubleStorage": "<f8",
    "IntStorage": "<i4",
    "BoolStorage": "?",
    "ByteStorage": "u1",
}

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406])
IMAGENET_STD = np.array([0.229, 0.224, 0.225])


class _Storage:
    def __init__(self, key: str, dtype: str):
        self.key, self.dtype = key, dtype


class _Tensor:
    def __init__(self, storage: _Storage, offset: int, size):
        self.storage, self.offset, self.size = storage, offset, tuple(size)


class _Unpickler(pickle.Unpickler):
    """Reads tensor *descriptions* only; refuses anything unexpected."""

    def find_class(self, module, name):
        if (module, name) == ("torch._utils", "_rebuild_tensor_v2"):
            return lambda storage, offset, size, stride, *a, **k: _Tensor(storage, offset, size)
        if module == "torch" and name.endswith("Storage"):
            return name
        if (module, name) == ("collections", "OrderedDict"):
            return collections.OrderedDict
        raise pickle.UnpicklingError(f"unexpected object in checkpoint: {module}.{name}")

    def persistent_load(self, pid):
        return _Storage(pid[2], _DTYPES[pid[1]])


def read_state_dict(path: Path) -> "collections.OrderedDict[str, np.ndarray]":
    archive = zipfile.ZipFile(path)
    root = archive.namelist()[0].split("/")[0]
    described = _Unpickler(io.BytesIO(archive.read(f"{root}/data.pkl"))).load()
    if isinstance(described, dict):
        for key in ("state_dict", "model_state_dict", "model"):
            if key in described and isinstance(described[key], dict):
                described = described[key]
                break
    tensors: "collections.OrderedDict[str, np.ndarray]" = collections.OrderedDict()
    for name, tensor in described.items():
        if not isinstance(tensor, _Tensor):
            continue
        raw = np.frombuffer(archive.read(f"{root}/data/{tensor.storage.key}"), dtype=tensor.storage.dtype)
        count = int(np.prod(tensor.size)) if tensor.size else 1
        tensors[name[len("module."):] if name.startswith("module.") else name] = (
            raw[tensor.offset : tensor.offset + count].reshape(tensor.size)
        )
    return tensors


def estimate_input_statistics(tensors) -> dict:
    """Mean and spread of the network input seen during training.

    For a constant input m, conv1 outputs ``sum(weights) · m`` per filter,
    so ``bn1.running_mean = A · m`` where ``A`` is the per-channel weight
    sum of each filter. Solving that 64 x 3 system recovers m.
    """

    weights = tensors["encoder.conv1.weight"].astype(np.float64)
    mean = tensors["encoder.bn1.running_mean"].astype(np.float64)
    variance = tensors["encoder.bn1.running_var"].astype(np.float64)

    response = weights.sum(axis=(2, 3))
    estimate, *_ = np.linalg.lstsq(response, mean, rcond=None)
    predicted = response @ estimate
    r_squared = 1.0 - ((mean - predicted) ** 2).sum() / ((mean - mean.mean()) ** 2).sum()

    # Filters that respond strongly to a constant input act as local
    # averages, so their output spread approximates the input spread.
    strongest = np.argsort(-np.linalg.norm(response, axis=1))[:24]
    spread = float(
        np.median(np.sqrt(variance[strongest]) / np.abs(response[strongest].sum(axis=1)))
    )
    return {
        "mean_input_rgb": [round(float(v), 4) for v in estimate],
        "fit_r_squared": round(float(r_squared), 6),
        "input_spread_estimate": round(spread, 4),
        "as_pixels_if_scale_255": [round(float(v) * 255, 1) for v in estimate],
        "pixel_contrast_if_scale_255": round(spread * 255, 1),
        "as_pixels_if_imagenet": [
            round(float(v), 1) for v in (estimate * IMAGENET_STD + IMAGENET_MEAN) * 255
        ],
        "pixel_contrast_if_imagenet": round(spread * float(IMAGENET_STD.mean()) * 255, 1),
    }


def main(argv: list[str]) -> int:
    default = Path(__file__).resolve().parents[1] / "models" / "best_weighted_multiclass_unet.pth"
    path = Path(argv[1]) if len(argv) > 1 else default
    if not path.exists():
        print(f"Checkpoint not found: {path}")
        return 1

    tensors = read_state_dict(path)
    parameters = sum(int(t.size) for t in tensors.values() if t.dtype.kind == "f")
    print(f"Checkpoint        : {path.name} ({path.stat().st_size / 1e6:.1f} MB)")
    print(f"Tensors           : {len(tensors)}")
    print(f"Float values      : {parameters:,}")
    print(f"First conv        : {tensors['encoder.conv1.weight'].shape}  (out, in, kH, kW)")
    print(f"Segmentation head : {tensors['segmentation_head.0.weight'].shape}  -> "
          f"{tensors['segmentation_head.0.weight'].shape[0]} classes")
    finite = all(np.isfinite(t).all() for t in tensors.values() if t.dtype.kind == "f")
    print(f"All values finite : {finite}")

    manifest_path = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "checkpoint_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())["tensors"]
        same = set(manifest) == set(tensors) and all(
            list(tensors[k].shape) == manifest[k]["shape"] for k in manifest
        )
        print(f"Matches manifest  : {same}  (tests/fixtures/checkpoint_manifest.json)")

    stats = estimate_input_statistics(tensors)
    print("\nInput statistics recorded by the first batch-norm layer")
    print(f"  mean network input (R, G, B) : {stats['mean_input_rgb']}  (fit R² = {stats['fit_r_squared']})")
    print(f"  input spread (approximate)   : {stats['input_spread_estimate']}")
    print("  read as x / 255              : "
          f"mean pixel {stats['as_pixels_if_scale_255']}, contrast about {stats['pixel_contrast_if_scale_255']} grey levels")
    print("  read as ImageNet-normalised  : "
          f"mean pixel {stats['as_pixels_if_imagenet']}, contrast about {stats['pixel_contrast_if_imagenet']} grey levels")
    verdict = (
        "scale_255" if stats["pixel_contrast_if_imagenet"] < 20 <= stats["pixel_contrast_if_scale_255"] else "undetermined"
    )
    if verdict == "scale_255":
        print(
            "\nConclusion: consistent with MODEL_NORMALIZATION=scale_255. Drone orthoimagery of a\n"
            "settlement has a contrast of tens of grey levels, which fits the x / 255 reading and\n"
            "not the ImageNet one. This is inferred from the weights, not read from training code;\n"
            "confirm on real imagery with backend.scripts.compare_normalization."
        )
    else:
        print("\nConclusion: undetermined from the checkpoint alone; compare both on real imagery.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
