"""Reading and validating GeoJSON without building the whole file as objects.

The land-cover layer is over 100 MB. ``json.load`` on it allocates well
over a gigabyte of Python objects; the reader here decodes one feature at a
time instead, so peak memory stays close to the size of the file text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np

from backend.gis.geometry import utm_to_lonlat, web_mercator_to_lonlat


class GeoJSONError(ValueError):
    """The file is not usable GeoJSON; the message says why."""


_WS = " \t\r\n"
_decoder = json.JSONDecoder()


def _skip_ws(text: str, pos: int) -> int:
    end = len(text)
    while pos < end and text[pos] in _WS:
        pos += 1
    return pos


def scan_feature_collection(
    path: Path | str,
    on_feature: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Walk a FeatureCollection once.

    ``on_feature`` is called for every feature in file order. The returned
    header holds every top-level member other than ``features`` plus
    ``feature_count``.
    """

    path = Path(path)
    if not path.exists():
        raise GeoJSONError(f"File not found: {path.name}")

    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise GeoJSONError(f"{path.name} is not UTF-8 text.") from exc

    pos = _skip_ws(text, 0)
    if pos >= len(text) or text[pos] != "{":
        raise GeoJSONError(f"{path.name} is not a JSON object.")
    pos += 1

    header: dict[str, Any] = {}
    count = 0
    saw_features = False

    try:
        while True:
            pos = _skip_ws(text, pos)
            if pos >= len(text):
                raise GeoJSONError(f"{path.name} ends unexpectedly.")
            if text[pos] == "}":
                break
            if text[pos] == ",":
                pos += 1
                continue

            key, pos = _decoder.raw_decode(text, pos)
            if not isinstance(key, str):
                raise GeoJSONError(f"{path.name} has a non-string member name.")
            pos = _skip_ws(text, pos)
            if pos >= len(text) or text[pos] != ":":
                raise GeoJSONError(f"{path.name} is malformed near member '{key}'.")
            pos = _skip_ws(text, pos + 1)

            if key != "features":
                header[key], pos = _decoder.raw_decode(text, pos)
                continue

            if text[pos] != "[":
                raise GeoJSONError(f"'features' in {path.name} is not an array.")
            saw_features = True
            pos += 1
            while True:
                pos = _skip_ws(text, pos)
                if pos >= len(text):
                    raise GeoJSONError(f"{path.name} ends inside the features array.")
                if text[pos] == "]":
                    pos += 1
                    break
                if text[pos] == ",":
                    pos += 1
                    continue
                feature, pos = _decoder.raw_decode(text, pos)
                count += 1
                if on_feature is not None:
                    on_feature(feature)
    except json.JSONDecodeError as exc:
        raise GeoJSONError(f"{path.name} is not valid JSON: {exc.msg} at character {exc.pos}.") from exc

    if header.get("type") != "FeatureCollection":
        raise GeoJSONError(
            f"{path.name} is a '{header.get('type')}', expected a FeatureCollection."
        )
    if not saw_features:
        raise GeoJSONError(f"{path.name} has no 'features' member.")

    header["feature_count"] = count
    return header


def read_header(path: Path | str) -> dict[str, Any]:
    """Top-level members and the feature count, without keeping features."""

    return scan_feature_collection(path, None)


def iter_features(path: Path | str) -> Iterator[dict[str, Any]]:
    """Convenience wrapper for small files (collects, then yields)."""

    features: list[dict[str, Any]] = []
    scan_feature_collection(path, features.append)
    yield from features


# ------------------------------------------------------------------------ CRS
_EPSG = re.compile(r"(?:EPSG|epsg)[:/]{1,2}(?:[0-9.]*[:/])?(\d{4,6})\s*$")


def crs_name(header: dict[str, Any]) -> str:
    """Declared CRS of a FeatureCollection as a short name.

    RFC 7946 GeoJSON has no ``crs`` member and is always longitude/latitude
    on WGS 84. Older files (and GDAL output) may carry a named CRS.
    """

    crs = header.get("crs")
    if not crs:
        return "OGC:CRS84"
    name = ""
    if isinstance(crs, dict):
        name = str((crs.get("properties") or {}).get("name") or "")
    elif isinstance(crs, str):
        name = crs
    upper = name.upper()
    if "CRS84" in upper:
        return "OGC:CRS84"
    match = _EPSG.search(name)
    if match:
        return f"EPSG:{match.group(1)}"
    return name or "OGC:CRS84"


def lonlat_transformer(crs: str) -> Callable[[np.ndarray], np.ndarray] | None:
    """Return a function mapping an ``(n, 2)`` array to lon/lat, or ``None``
    when the CRS already is longitude/latitude."""

    upper = crs.upper()
    if upper in ("OGC:CRS84", "EPSG:4326", "EPSG:4979"):
        return None

    if upper in ("EPSG:3857", "EPSG:900913"):
        def from_mercator(array: np.ndarray) -> np.ndarray:
            lon, lat = web_mercator_to_lonlat(array[:, 0], array[:, 1])
            return np.column_stack([lon, lat])

        return from_mercator

    match = re.fullmatch(r"EPSG:(326|327)(\d{2})", upper)
    if match:
        northern = match.group(1) == "326"
        zone = int(match.group(2))

        def from_utm(array: np.ndarray) -> np.ndarray:
            lon, lat = utm_to_lonlat(array[:, 0], array[:, 1], zone, northern)
            return np.column_stack([lon, lat])

        return from_utm

    try:  # any other CRS needs PROJ
        from pyproj import Transformer
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise GeoJSONError(
            f"Layer CRS {crs} needs the 'pyproj' package to be converted to longitude/latitude."
        ) from exc

    try:
        transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    except Exception as exc:  # pyproj raises CRSError subclasses
        raise GeoJSONError(f"Layer CRS '{crs}' is not recognised.") from exc

    def from_proj(array: np.ndarray) -> np.ndarray:
        lon, lat = transformer.transform(array[:, 0], array[:, 1])
        return np.column_stack([lon, lat])

    return from_proj


def reproject_geometry(geometry: dict[str, Any], transform) -> dict[str, Any]:
    """Apply ``transform`` to every ring of a Polygon / MultiPolygon."""

    def ring(coords):
        array = np.asarray(coords, dtype=np.float64)[:, :2]
        return transform(array).tolist()

    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "Polygon":
        return {"type": "Polygon", "coordinates": [ring(r) for r in coords]}
    if kind == "MultiPolygon":
        return {
            "type": "MultiPolygon",
            "coordinates": [[ring(r) for r in polygon] for polygon in coords],
        }
    return geometry
