"""Compare the two input normalisations on a real orthoimage.

    python -m backend.scripts.compare_normalization path/to/ortho.tif [tiles]

Runs the trained model on a sample of 512 px tiles with ``scale_255`` and
with ``imagenet`` preprocessing and prints, for each, the mean confidence,
mean entropy and class shares. The preprocessing that matches training
gives clearly higher confidence and a plausible class mix. Set the winner
as MODEL_NORMALIZATION in backend/.env.
"""

from __future__ import annotations

import sys

import numpy as np

from backend.ai.classes import CLASS_NAMES, NUM_CLASSES
from backend.ai.inference import NORMALIZATIONS, predict_probabilities, summarise_probabilities
from backend.ai.model import get_model
from backend.ai.raster import inspect_raster, validate_for_inference
from backend.config import settings


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    import rasterio
    from rasterio.windows import Window

    path = argv[1]
    wanted = int(argv[2]) if len(argv) > 2 else 24

    meta = inspect_raster(path)
    checks = validate_for_inference(meta)
    if checks["errors"]:
        print("This raster cannot be used:", " ".join(checks["errors"]))
        return 1

    model, device = get_model(settings.model_path)
    tile = 512
    rng = np.random.default_rng(12345)
    results = {name: {"confidence": [], "entropy": [], "classes": np.zeros(NUM_CLASSES)} for name in NORMALIZATIONS}

    with rasterio.open(path) as src:
        used = 0
        attempts = 0
        while used < wanted and attempts < wanted * 20:
            attempts += 1
            col = int(rng.integers(0, max(1, src.width - tile)))
            row = int(rng.integers(0, max(1, src.height - tile)))
            window = Window(col, row, min(tile, src.width - col), min(tile, src.height - row))
            valid = src.dataset_mask(window=window) > 0
            rgb = src.read(meta["rgb_bands"], window=window)
            valid &= rgb.any(axis=0)
            if valid.mean() < 0.9:
                continue
            image = np.transpose(rgb, (1, 2, 0))
            for name in NORMALIZATIONS:
                probabilities = predict_probabilities(model, image, device, name)
                prediction, confidence, entropy = summarise_probabilities(probabilities)
                results[name]["confidence"].append(float(confidence[valid].mean()))
                results[name]["entropy"].append(float(entropy[valid].mean()))
                results[name]["classes"] += np.bincount(prediction[valid], minlength=NUM_CLASSES)[:NUM_CLASSES]
            used += 1

    if used == 0:
        print("No tile with enough valid pixels was found.")
        return 1

    print(f"{used} tiles of {tile} px from {meta['file_name']} on {device}\n")
    for name in NORMALIZATIONS:
        result = results[name]
        shares = result["classes"] / max(result["classes"].sum(), 1)
        print(f"{name}")
        print(f"  mean confidence : {np.mean(result['confidence']):.3f}")
        print(f"  mean entropy    : {np.mean(result['entropy']):.3f}")
        print("  class shares    : " + ", ".join(f"{CLASS_NAMES[i]} {shares[i] * 100:.1f}%" for i in range(NUM_CLASSES)))
        print()
    best = max(NORMALIZATIONS, key=lambda n: np.mean(results[n]["confidence"]))
    print(f"Higher mean confidence: {best}  (current setting: {settings.normalization})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
