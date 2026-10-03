"""Tile-level inference: RGB tile in, class / confidence / entropy out.

Preprocessing must match training. The checkpoint's first batch-norm layer
recorded the statistics of the inputs it saw: solving
``running_mean = sum(conv1 weights) · mean_input`` gives a mean network input
of about (0.383, 0.394, 0.358) with a perfect fit, and an input spread of
roughly 0.17. That is what 8-bit imagery divided by 255 looks like; under
ImageNet mean/std normalisation the same statistics would require imagery
with a contrast of only ~10 grey levels. ``scale_255`` is therefore the
default (and is what the original inference code did). The alternative is
kept selectable with ``MODEL_NORMALIZATION=imagenet`` and can be compared on
real imagery with ``python -m backend.scripts.compare_normalization``.
"""

from __future__ import annotations

import numpy as np

from backend.ai.classes import CLASS_NAMES, NUM_CLASSES  # noqa: F401  (compatibility)

MODEL_STRIDE = 32

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

NORMALIZATIONS = ("scale_255", "imagenet")


def preprocess(image, normalization: str = "scale_255") -> np.ndarray:
    """Convert an RGB image ``[H, W, 3]`` to a float32 array ``[3, H, W]``."""

    image = np.asarray(image)
    if image.ndim != 3:
        raise ValueError(f"Expected a 3D image [H, W, C], got shape {image.shape}")
    if image.shape[2] != 3:
        raise ValueError(f"Expected an RGB image with 3 channels, got {image.shape[2]}")
    if normalization not in NORMALIZATIONS:
        raise ValueError(f"Unknown normalization '{normalization}'")

    if image.dtype == np.uint8:
        scaled = image.astype(np.float32) / 255.0
    else:
        scaled = image.astype(np.float32)
        # Float imagery already in 0..1 is left alone; 0..255 is scaled.
        if scaled.size and float(scaled.max()) > 1.0:
            scaled = scaled / 255.0

    if normalization == "imagenet":
        scaled = (scaled - IMAGENET_MEAN) / IMAGENET_STD

    return np.ascontiguousarray(scaled.transpose(2, 0, 1))


def pad_to_stride(array: np.ndarray, stride: int = MODEL_STRIDE) -> tuple[np.ndarray, int, int]:
    """Reflect-pad ``[C, H, W]`` on the bottom/right to a multiple of ``stride``."""

    _, height, width = array.shape
    padded_height = ((height + stride - 1) // stride) * stride
    padded_width = ((width + stride - 1) // stride) * stride
    pad_bottom = padded_height - height
    pad_right = padded_width - width
    if pad_bottom == 0 and pad_right == 0:
        return array, height, width
    # np.pad reflects repeatedly, so it also works when the pad is larger
    # than the tile (torch's reflect padding does not).
    mode = "reflect" if height > 1 and width > 1 else "edge"
    padded = np.pad(array, ((0, 0), (0, pad_bottom), (0, pad_right)), mode=mode)
    return padded, height, width


def predict_probabilities(model, image, device, normalization: str = "scale_255") -> np.ndarray:
    """Class probabilities ``[6, H, W]`` (float32) for one RGB tile."""

    import torch

    array = preprocess(image, normalization)
    array, height, width = pad_to_stride(array)
    tensor = torch.from_numpy(array).unsqueeze(0).to(device)

    model.eval()
    with torch.no_grad():
        logits = model(tensor)
        probabilities = torch.softmax(logits, dim=1)

    return probabilities[0, :, :height, :width].cpu().numpy().astype(np.float32)


def summarise_probabilities(probabilities: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """From ``[C, H, W]`` probabilities return ``(class, confidence, entropy)``.

    * class       - arg-max class id, uint8
    * confidence  - probability of that class, float32 in (0, 1]
    * entropy     - Shannon entropy in nats, float32 in [0, ln 6]
    """

    prediction = np.argmax(probabilities, axis=0).astype(np.uint8)
    confidence = np.max(probabilities, axis=0).astype(np.float32)
    entropy = (-np.sum(probabilities * np.log(probabilities + 1e-8), axis=0)).astype(np.float32)
    np.clip(entropy, 0.0, None, out=entropy)
    return prediction, confidence, entropy


def predict(model, image, device, normalization: str = "scale_255"):
    """Run six-class semantic segmentation on one RGB tile.

    Returns ``(prediction, probabilities, confidence, entropy)``.
    """

    probabilities = predict_probabilities(model, image, device, normalization)
    prediction, confidence, entropy = summarise_probabilities(probabilities)
    return prediction, probabilities, confidence, entropy
