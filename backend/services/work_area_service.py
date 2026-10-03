"""Work areas a surveyor declares for themselves.

A work area is a boundary the surveyor draws on the map or uploads as
GeoJSON. It is labelled SELF-DECLARED WORK AREA everywhere and is never
presented as an official assignment; those come only from the registry file.

The owner is always the signed-in account. Another account's work area is
reported as not found, so its existence is not revealed. Every change is
written to the audit log.
"""

from __future__ import annotations

from typing import Any

from backend.config import Settings
from backend.core.store import Store
from backend.gis.geojson_io import GeoJSONError, crs_name, lonlat_transformer, reproject_geometry
from backend.gis.geometry import POLYGON_TYPES, analyse_geometry
from backend.services.assignment_service import (
    SurveyorContext,
    describe_assignment,
    describe_work_area,
    registry_assignments_for,
)

ORIGINS = ("drawn", "uploaded")
TEXT_FIELDS = ("name", "state", "district", "taluk", "village")
MAX_TEXT = 120
MIN_AREA_M2 = 1.0
MAX_AREA_M2 = 1_000_000_000.0  # 1,000 km²: far beyond any village survey
MAX_VERTICES = 20_000
DECIMALS = 7  # about 1 cm in longitude/latitude


class WorkAreaError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# ------------------------------------------------------------------ input
def _clean_text(payload: dict[str, Any], *, require_name: bool) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    for key in TEXT_FIELDS:
        if key not in payload:
            continue
        raw = payload[key]
        if raw is not None and not isinstance(raw, str):
            raise WorkAreaError(f"'{key}' must be text.")
        text = (raw or "").strip()
        if len(text) > MAX_TEXT:
            raise WorkAreaError(f"'{key}' is longer than {MAX_TEXT} characters.")
        values[key] = text or None
    if require_name and not values.get("name"):
        raise WorkAreaError("Give the work area a name.")
    if "name" in values and not values["name"]:
        raise WorkAreaError("The name of a work area cannot be empty.")
    return values


def _close_rings(geometry: dict[str, Any]) -> dict[str, Any]:
    def ring(coords):
        if isinstance(coords, list) and len(coords) >= 3 and coords[0] != coords[-1]:
            return [*coords, coords[0]]
        return coords

    if geometry["type"] == "Polygon":
        return {"type": "Polygon", "coordinates": [ring(r) for r in geometry["coordinates"] or []]}
    return {
        "type": "MultiPolygon",
        "coordinates": [[ring(r) for r in polygon] for polygon in geometry["coordinates"] or []],
    }


def _round(geometry: dict[str, Any]) -> dict[str, Any]:
    def ring(coords):
        return [[round(float(x), DECIMALS), round(float(y), DECIMALS)] for x, y, *_ in coords]

    if geometry["type"] == "Polygon":
        return {"type": "Polygon", "coordinates": [ring(r) for r in geometry["coordinates"]]}
    return {
        "type": "MultiPolygon",
        "coordinates": [[ring(r) for r in polygon] for polygon in geometry["coordinates"]],
    }


def extract_polygon(boundary: Any) -> tuple[dict[str, Any], str]:
    """The single Polygon / MultiPolygon in ``boundary`` and its declared CRS.

    ``boundary`` may be a geometry, a Feature, or a FeatureCollection that
    holds exactly one polygon feature (an uploaded file).
    """

    if not isinstance(boundary, dict):
        raise WorkAreaError("The boundary must be a GeoJSON object.")
    crs = crs_name(boundary)
    kind = boundary.get("type")
    if kind == "FeatureCollection":
        features = [f for f in boundary.get("features") or [] if isinstance(f, dict)]
        polygons = [f for f in features if (f.get("geometry") or {}).get("type") in POLYGON_TYPES]
        if len(polygons) != 1:
            raise WorkAreaError(
                f"The file must contain exactly one polygon feature; it contains {len(polygons)}"
                + (f" (and {len(features) - len(polygons)} other features)" if len(features) > len(polygons) else "")
                + "."
            )
        geometry = polygons[0]["geometry"]
    elif kind == "Feature":
        geometry = boundary.get("geometry")
    else:
        geometry = boundary
    if not isinstance(geometry, dict) or geometry.get("type") not in POLYGON_TYPES:
        found = geometry.get("type") if isinstance(geometry, dict) else "nothing"
        raise WorkAreaError(f"The boundary must be a Polygon or MultiPolygon, not {found}.")
    if not isinstance(geometry.get("coordinates"), list) or not geometry["coordinates"]:
        raise WorkAreaError("The boundary has no coordinates.")
    return geometry, crs


def validate_boundary(boundary: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Check, reproject and measure a boundary.

    Returns ``(geometry_lonlat, metrics)``. The area is measured here, in the
    local UTM zone; an area sent by the client is never used.
    """

    geometry, crs = extract_polygon(boundary)
    geometry = _close_rings(geometry)

    if crs not in ("OGC:CRS84", "EPSG:4326"):
        try:
            transform = lonlat_transformer(crs)
            if transform is not None:
                geometry = reproject_geometry(geometry, transform)
        except GeoJSONError as exc:
            raise WorkAreaError(f"The declared CRS could not be used: {exc}") from exc
        except Exception as exc:  # malformed coordinates for the transform
            raise WorkAreaError(f"The boundary could not be reprojected from {crs}: {exc}") from exc

    analysis = analyse_geometry(geometry)
    if analysis["fatal"] or analysis["problems"]:
        problems = "; ".join(analysis["problems"]) or "geometry is unusable"
        hint = ""
        if "coordinates fall outside longitude/latitude range" in analysis["problems"]:
            hint = " If the file is in a projected CRS, it must declare it in a 'crs' member."
        raise WorkAreaError(f"The boundary is not a valid polygon: {problems}.{hint}")

    # Self-intersection is a validity problem the structural checks above
    # cannot see. Shapely is in requirements.txt; without it the check is
    # skipped rather than guessed.
    try:
        from shapely.geometry import shape
        from shapely.validation import explain_validity, make_valid
    except ImportError:  # pragma: no cover
        shape = None
    if shape is not None:
        candidate = shape(geometry)
        if not candidate.is_valid:
            if make_valid(candidate).area == 0:
                raise WorkAreaError(
                    "The boundary encloses no area: its points lie on a line. Draw at least three "
                    "corners that are not in a straight line."
                )
            raise WorkAreaError(
                f"The boundary is not a valid polygon: {explain_validity(candidate)}. "
                "Edges must not cross each other."
            )

    metrics = analysis["metrics"]
    if metrics["vertices"] > MAX_VERTICES:
        raise WorkAreaError(
            f"The boundary has {metrics['vertices']:,} vertices; simplify it to at most {MAX_VERTICES:,}."
        )
    if metrics["area_m2"] < MIN_AREA_M2:
        raise WorkAreaError(
            f"The boundary encloses {metrics['area_m2']:.2f} m², which is too small to be a work area."
        )
    if metrics["area_m2"] > MAX_AREA_M2:
        raise WorkAreaError(
            f"The boundary encloses {metrics['area_m2'] / 1e6:,.0f} km²; a work area may be at most "
            f"{MAX_AREA_M2 / 1e6:,.0f} km²."
        )

    return _round(geometry), metrics


def measure(boundary: Any) -> dict[str, Any]:
    """Validate and measure a boundary without storing anything, so the form
    can show the server's measurement before the surveyor saves."""

    geometry, metrics = validate_boundary(boundary)
    _, _, notes = describe_assignment(
        {"type": "Feature", "properties": {}, "geometry": geometry}, "self_declared"
    )
    return {
        "geometry": geometry,
        "area_m2": round(metrics["area_m2"], 2),
        "area_ha": round(metrics["area_m2"] / 10000.0, 3),
        "perimeter_m": round(metrics["perimeter_m"], 2),
        "metric_crs": metrics["metric_crs"],
        "vertices": metrics["vertices"],
        "notes": [note for note in notes if note.startswith("The boundary is an axis-aligned")],
    }


# ------------------------------------------------------------- listing
def _work_area_entry(area: dict[str, Any], active_id: str | None) -> dict[str, Any]:
    assignment, boundary, notes = describe_work_area(area)
    assignment["is_current"] = assignment["assignment_id"] == active_id
    assignment["notes"] = notes
    assignment["geometry"] = boundary
    return assignment


def list_areas(context: SurveyorContext, settings: Settings, store: Store) -> dict[str, Any]:
    """Registry assignments for this account, then its own work areas."""

    current_id = context.assignment_id
    registry, error = registry_assignments_for(context.email, settings)
    items: list[dict[str, Any]] = []
    for feature in registry:
        assignment, boundary, notes = describe_assignment(feature, "assigned")
        assignment["is_current"] = assignment["assignment_id"] == current_id
        assignment["notes"] = notes
        assignment["geometry"] = boundary
        items.append(assignment)
    for area in store.list_work_areas(context.email):
        items.append(_work_area_entry(area, current_id))
    return {
        "items": items,
        "current": context.assignment,
        "current_id": current_id,
        "notes": context.notes,
        "registry_error": error,
        "limits": {"min_area_m2": MIN_AREA_M2, "max_area_m2": MAX_AREA_M2, "max_vertices": MAX_VERTICES},
    }


# ------------------------------------------------------------- changes
def _audit_view(area: dict[str, Any] | None) -> dict[str, Any] | None:
    if area is None:
        return None
    return {
        key: area.get(key)
        for key in ("area_id", "name", "state", "district", "taluk", "village", "area_m2", "origin", "is_active")
    } | {"geometry": area.get("geometry")}


def _audit(store: Store, context: SurveyorContext, action: str, area_id: str, before, after, reason=""):
    store.add_audit(
        actor_id=context.surveyor_id,
        actor_email=context.email,
        action=action,
        entity_type="work_area",
        entity_id=area_id,
        before=_audit_view(before),
        after=_audit_view(after),
        reason=reason,
    )


def _owned(store: Store, context: SurveyorContext, area_id: str) -> dict[str, Any]:
    area = store.get_work_area(area_id, owner_email=context.email)
    if area is None:
        # The same answer whether the area belongs to someone else or does not exist.
        raise WorkAreaError(f"Work area '{area_id}' not found.", 404)
    return area


def create_area(payload: dict[str, Any], context: SurveyorContext, store: Store) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise WorkAreaError("Send the work area as a JSON object.")
    fields = _clean_text(payload, require_name=True)
    origin = payload.get("origin") or "drawn"
    if origin not in ORIGINS:
        raise WorkAreaError(f"'origin' must be one of {', '.join(ORIGINS)}.")
    geometry, metrics = validate_boundary(payload.get("boundary"))
    area = store.create_work_area(
        owner_email=context.email,
        owner_surveyor_id=context.surveyor_id,
        geometry=geometry,
        area_m2=round(metrics["area_m2"], 2),
        origin=origin,
        **fields,
    )
    _audit(store, context, "work_area.create", area["area_id"], None, area)
    if payload.get("activate", True):
        store.activate_work_area(area["area_id"], context.email)
        _audit(store, context, "work_area.activate", area["area_id"], None, None, "Activated on creation")
        area = store.get_work_area(area["area_id"], context.email)
    return _work_area_entry(area, area["area_id"] if area["is_active"] else context.assignment_id)


def update_area(area_id: str, payload: dict[str, Any], context: SurveyorContext, store: Store) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise WorkAreaError("Send the changes as a JSON object.")
    before = _owned(store, context, area_id)
    changes: dict[str, Any] = _clean_text(payload, require_name=False)
    if "boundary" in payload:
        geometry, metrics = validate_boundary(payload["boundary"])
        changes["geometry"] = geometry
        changes["area_m2"] = round(metrics["area_m2"], 2)
        origin = payload.get("origin") or before["origin"]
        if origin not in ORIGINS:
            raise WorkAreaError(f"'origin' must be one of {', '.join(ORIGINS)}.")
        changes["origin"] = origin
    if not changes:
        raise WorkAreaError("Nothing to change: send a name, place fields or a boundary.")
    after = store.update_work_area(area_id, context.email, **changes)
    _audit(store, context, "work_area.update", area_id, before, after, str(payload.get("reason") or ""))
    return _work_area_entry(after, after["area_id"] if after["is_active"] else context.assignment_id)


def delete_area(area_id: str, context: SurveyorContext, store: Store) -> dict[str, Any]:
    before = _owned(store, context, area_id)
    store.delete_work_area(area_id, context.email)
    _audit(store, context, "work_area.delete", area_id, before, None)
    return {"deleted": area_id, "was_active": before["is_active"]}


def activate(assignment_id: str, context: SurveyorContext, settings: Settings, store: Store) -> dict[str, Any]:
    """Make one of the account's own work areas, or one of its registry
    assignments, the current area."""

    area = store.get_work_area(assignment_id, owner_email=context.email)
    if area is not None:
        store.activate_work_area(assignment_id, context.email)
        _audit(store, context, "work_area.activate", assignment_id, None, None, "Activated")
        return {"activated": assignment_id, "kind": "work_area"}

    registry, _ = registry_assignments_for(context.email, settings)
    ids = {str((f.get("properties") or {}).get("assignment_id")) for f in registry}
    if assignment_id not in ids:
        raise WorkAreaError(f"Assignment '{assignment_id}' not found.", 404)
    store.prefer_registry_assignment(context.email, assignment_id)
    store.add_audit(
        actor_id=context.surveyor_id,
        actor_email=context.email,
        action="assignment.activate",
        entity_type="assignment",
        entity_id=assignment_id,
        reason="Registry assignment made current",
    )
    return {"activated": assignment_id, "kind": "registry"}
