"""Surveyor identity and assignment context.

The surveyor is whoever the access token belongs to. The assignment comes
from the registry file ``data/surveyors/surveyor_assignments.geojson``:

1. an assignment whose ``assigned_to`` lists the signed-in e-mail, else
2. the registry's demo assignment (``"is_demo": true``) when
   ``CADASTRA_ALLOW_DEMO_ASSIGNMENT`` is on, clearly labelled DEMO, else
3. no assignment ("Requires administrator input").

Nothing about the assignment is invented here: area is measured from the
boundary geometry, and disagreements with the declared values are reported
as notes instead of being hidden.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.config import Settings
from backend.core.auth import AuthUser
from backend.gis.geometry import analyse_geometry


@dataclass
class SurveyorContext:
    surveyor: dict[str, Any]
    assignment: dict[str, Any] | None
    boundary: dict[str, Any] | None = None  # GeoJSON geometry, lon/lat
    notes: list[str] = field(default_factory=list)

    @property
    def surveyor_id(self) -> str:
        return self.surveyor["surveyor_id"]

    @property
    def email(self) -> str:
        return self.surveyor["email"]

    @property
    def assignment_id(self) -> str | None:
        return self.assignment["assignment_id"] if self.assignment else None

    @property
    def bbox(self) -> tuple[float, float, float, float] | None:
        if self.assignment and self.assignment.get("bbox"):
            box = self.assignment["bbox"]
            return (box[0], box[1], box[2], box[3])
        return None


def surveyor_id_for(user: AuthUser) -> str:
    """Stable identifier derived from the authenticated account."""

    digest = hashlib.sha1(user.user_id.encode("utf-8")).hexdigest()[:8].upper()
    return f"SRV-{digest}"


def _display_name(user: AuthUser) -> str:
    if user.name:
        return user.name
    local = user.email.split("@", 1)[0]
    parts = [p for p in local.replace("_", ".").replace("-", ".").split(".") if p]
    return " ".join(part.capitalize() for part in parts) or user.email


def load_registry(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    """Return ``(features, error)``. A broken registry is an error state the
    UI reports; it never falls back to made-up data."""

    if not path.exists():
        return [], "No assignment registry file is present."
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        return [], f"The assignment registry could not be read: {exc}"
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        return [], "The assignment registry is not a GeoJSON FeatureCollection."
    features = [f for f in data.get("features") or [] if isinstance(f, dict)]
    return features, None


def _emails(properties: dict[str, Any]) -> set[str]:
    values: list[Any] = []
    assigned = properties.get("assigned_to")
    if isinstance(assigned, list):
        values.extend(assigned)
    elif isinstance(assigned, str):
        values.append(assigned)
    for key in ("surveyor_email", "email"):
        if properties.get(key):
            values.append(properties[key])
    return {str(v).strip().lower() for v in values if str(v).strip()}


def _is_axis_aligned_rectangle(geometry: dict[str, Any]) -> bool:
    if geometry.get("type") != "Polygon":
        return False
    rings = geometry.get("coordinates") or []
    if len(rings) != 1 or len(rings[0]) != 5:
        return False
    xs = {round(float(p[0]), 9) for p in rings[0]}
    ys = {round(float(p[1]), 9) for p in rings[0]}
    return len(xs) == 2 and len(ys) == 2


def describe_assignment(feature: dict[str, Any], mode: str) -> tuple[dict[str, Any], dict | None, list[str]]:
    """Normalise one registry feature. Returns ``(assignment, boundary, notes)``."""

    properties = dict(feature.get("properties") or {})
    geometry = feature.get("geometry")
    notes: list[str] = []

    is_demo = bool(properties.get("is_demo")) or mode == "demo"
    assignment: dict[str, Any] = {
        "assignment_id": str(properties.get("assignment_id") or "UNASSIGNED"),
        "assignment_status": str(properties.get("assignment_status") or "unknown"),
        "assigned_date": properties.get("assigned_date"),
        "survey_type": properties.get("survey_type"),
        "priority": properties.get("priority"),
        "state": properties.get("state"),
        "district": properties.get("district"),
        "taluk": properties.get("taluk"),
        "village": properties.get("village"),
        "department": properties.get("department"),
        "is_demo": is_demo,
        "mode": mode,
        "label": "DEMO ASSIGNMENT" if is_demo else "ASSIGNED",
        "boundary_source": properties.get("boundary_source"),
        "declared_area_ha": None,
        "area_ha": None,
        "area_m2": None,
        "bbox": None,
    }

    declared = properties.get("declared_area_ha", properties.get("total_area_ha"))
    if isinstance(declared, (int, float)) and not isinstance(declared, bool):
        assignment["declared_area_ha"] = float(declared)

    boundary = None
    analysis = analyse_geometry(geometry)
    if analysis["fatal"]:
        notes.append(
            "The assignment has no usable boundary geometry: "
            + ("; ".join(analysis["problems"]) or "geometry missing")
            + "."
        )
    else:
        boundary = geometry
        metrics = analysis["metrics"]
        assignment["area_m2"] = round(metrics["area_m2"], 2)
        assignment["area_ha"] = round(metrics["area_m2"] / 10000.0, 3)
        assignment["perimeter_m"] = round(metrics["perimeter_m"], 2)
        assignment["metric_crs"] = metrics["metric_crs"]
        assignment["bbox"] = list(analysis["bbox"])
        for problem in analysis["problems"]:
            notes.append(f"Boundary geometry issue: {problem}.")

        declared_ha = assignment["declared_area_ha"]
        if declared_ha and assignment["area_ha"]:
            ratio = assignment["area_ha"] / declared_ha
            if ratio > 1.1 or ratio < 0.9:
                notes.append(
                    f"Declared area ({declared_ha:g} ha) does not match the boundary geometry "
                    f"({assignment['area_ha']:,.1f} ha measured in {metrics['metric_crs']})."
                )
        if _is_axis_aligned_rectangle(geometry):
            notes.append("The boundary is an axis-aligned rectangle, not a surveyed outline.")

    if is_demo:
        notes.insert(
            0,
            "Demo assignment: this boundary is not an authoritative survey assignment.",
        )
    if properties.get("boundary_source"):
        notes.append(str(properties["boundary_source"]))

    return assignment, boundary, notes


def resolve_context(user: AuthUser, settings: Settings) -> SurveyorContext:
    """Build the surveyor + assignment context for an authenticated user."""

    surveyor: dict[str, Any] = {
        "surveyor_id": surveyor_id_for(user),
        "name": _display_name(user),
        "email": user.email,
        "auth_user_id": user.user_id,
        "is_dev_session": user.is_dev,
        "designation": None,
        "department": None,
    }

    features, error = load_registry(settings.assignments_file)
    if error:
        return SurveyorContext(surveyor=surveyor, assignment=None, notes=[error])

    matched = None
    for feature in features:
        if user.email in _emails(feature.get("properties") or {}):
            matched = feature
            break

    mode = "assigned"
    if matched is None and settings.allow_demo_assignment:
        matched = next(
            (f for f in features if (f.get("properties") or {}).get("is_demo")), None
        )
        mode = "demo"

    if matched is None:
        return SurveyorContext(
            surveyor=surveyor,
            assignment=None,
            notes=[
                "No survey assignment is registered for this account. "
                "Requires administrator input."
            ],
        )

    properties = matched.get("properties") or {}
    if mode == "assigned":
        # Registry details about the person apply only to a real match.
        if properties.get("surveyor_id"):
            surveyor["surveyor_id"] = str(properties["surveyor_id"])
        if properties.get("surveyor_name") and not user.name:
            surveyor["name"] = str(properties["surveyor_name"])
        surveyor["designation"] = properties.get("designation")
    surveyor["department"] = properties.get("department")

    assignment, boundary, notes = describe_assignment(matched, mode)
    return SurveyorContext(surveyor=surveyor, assignment=assignment, boundary=boundary, notes=notes)


def boundary_feature_collection(context: SurveyorContext) -> dict[str, Any]:
    features = []
    if context.assignment and context.boundary:
        properties = {
            key: context.assignment.get(key)
            for key in (
                "assignment_id",
                "assignment_status",
                "district",
                "taluk",
                "village",
                "area_ha",
                "declared_area_ha",
                "is_demo",
                "label",
            )
        }
        features.append({"type": "Feature", "properties": properties, "geometry": context.boundary})
    return {"type": "FeatureCollection", "features": features}
