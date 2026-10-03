"""The six land-cover classes produced by the segmentation model.

Single source of truth: every other module imports from here.
"""

from __future__ import annotations

NUM_CLASSES = 6

# Value written to the prediction raster where the input had no valid data.
NODATA_CLASS = 255

CLASS_NAMES: dict[int, str] = {
    0: "Background",
    1: "Field",
    2: "Building",
    3: "Road",
    4: "Water",
    5: "Other",
}

CLASS_KEYS: dict[int, str] = {
    0: "background",
    1: "field",
    2: "building",
    3: "road",
    4: "water",
    5: "other",
}

# Prefix used when minting feature identifiers, e.g. BLD-000042.
CLASS_PREFIX: dict[int, str] = {
    0: "BG",
    1: "FLD",
    2: "BLD",
    3: "RD",
    4: "WTR",
    5: "OTH",
}

KEY_TO_ID: dict[str, int] = {key: cid for cid, key in CLASS_KEYS.items()}

_ALIASES: dict[str, str] = {
    "background": "background",
    "bg": "background",
    "field": "field",
    "fields": "field",
    "agriculture": "field",
    "farmland": "field",
    "building": "building",
    "buildings": "building",
    "built-up": "building",
    "builtup": "building",
    "road": "road",
    "roads": "road",
    "water": "water",
    "waterbody": "water",
    "water body": "water",
    "other": "other",
    "others": "other",
}


def class_key(value) -> str:
    """Normalise a class id or free-text class name to a canonical key.

    Unknown names are returned lower-cased rather than forced into one of
    the six classes, so unexpected data stays visible instead of being
    silently relabelled.
    """

    if value is None:
        return "unknown"

    if isinstance(value, (int,)) and not isinstance(value, bool):
        return CLASS_KEYS.get(int(value), "unknown")

    if isinstance(value, float) and value == value and float(value).is_integer():
        return CLASS_KEYS.get(int(value), "unknown")

    text = str(value).strip().lower()
    if text == "" or text == "nan":
        return "unknown"
    if text.isdigit():
        return CLASS_KEYS.get(int(text), "unknown")
    return _ALIASES.get(text, text)


def class_id_for_key(key: str) -> int | None:
    return KEY_TO_ID.get(key)
