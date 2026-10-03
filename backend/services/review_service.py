"""Surveyor review workflow: AI proposes -> GIS validates -> surveyor verifies.

The queue is built from the indexed layers and the persisted review
records. Every decision is stored with who made it, when, the model's
confidence and entropy for the feature at that time, and (for edits) the
geometry before and after. The audit log is written in the same
transaction as the review.
"""

from __future__ import annotations

from typing import Any

from backend.ai import qa
from backend.config import Settings
from backend.core import runtime
from backend.core.store import ACTION_STATUS, Store
from backend.gis.geometry import analyse_geometry
from backend.services import map_service
from backend.services.assignment_service import SurveyorContext

QUEUE_GROUPS = ("high", "medium", "low", "verified", "flagged")
OBSERVED_CLASSES = ("Field", "Building", "Road", "Water", "Other", "Background")


class ReviewError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _queue_layers(source: str) -> list[tuple[str, list[str] | None]]:
    """Layers that take part in review.

    Field features share their geometry with the candidate parcels derived
    from them, so they are reviewed once, as parcels.
    """

    layers = runtime.get_layers()
    result: list[tuple[str, list[str] | None]] = []
    has_parcels = layers.layer(source, "parcels") is not None
    if has_parcels:
        result.append(("parcels", None))
    if layers.layer(source, "landcover") is not None:
        classes = ["building", "road", "water", "other"]
        if not has_parcels:
            classes.append("field")
        result.append(("landcover", classes))
    return result


def _issue(properties: dict[str, Any]) -> str:
    reasons = properties.get("review_reasons") or []
    if reasons:
        return reasons[0]
    if "UNCERTAINTY_UNAVAILABLE" in (properties.get("qa_flags") or []):
        return "No model uncertainty recorded for this layer; routine check"
    return "Routine check"


def _item(feature: dict[str, Any], status: str, state: dict[str, Any] | None) -> dict[str, Any]:
    properties = feature["properties"]
    box = feature.get("bbox")
    return {
        "feature_id": feature["id"],
        "layer": properties.get("layer"),
        "class_name": properties.get("class_name"),
        "class_key": properties.get("class_key"),
        "area_m2": properties.get("area_m2"),
        "priority": properties.get("review_priority"),
        "issue": _issue(properties),
        "reasons": properties.get("review_reasons") or [],
        "flags": properties.get("qa_flags") or [],
        "confidence": properties.get("confidence"),
        "entropy": properties.get("entropy"),
        "uncertainty": qa.uncertainty_band(properties.get("confidence"), properties.get("entropy")),
        "status": status,
        "review_count": state["review_count"] if state else 0,
        "last_review_at": state["last_review_at"] if state else None,
        "bbox": box,
        "center": [(box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0] if box else None,
    }


def queue(
    context: SurveyorContext,
    settings: Settings,
    store: Store,
    *,
    source: str,
    group: str = "high",
    include_fragments: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """One page of the review queue plus the counts of every group."""

    if group not in QUEUE_GROUPS:
        raise ReviewError(f"Unknown queue group '{group}'.")

    layers = runtime.get_layers()
    runtime.ensure_source(source)
    queue_layers = _queue_layers(source)
    states = store.feature_states(source)
    area = context.bbox
    min_area = None if include_fragments else settings.sliver_area_m2

    reviewed_uids = {uid for (src, uid) in states if src == source}
    decided: dict[str, list[str]] = {"verified": [], "flagged": [], "reviewed": []}
    for (src, uid), state in states.items():
        status = state["verification_status"]
        if status in ("SURVEYOR_VERIFIED", "EDITED"):
            decided["verified"].append(uid)
        elif status in ("FLAGGED", "REJECTED"):
            decided["flagged"].append(uid)
        else:
            decided["reviewed"].append(uid)

    counts = {name: 0 for name in QUEUE_GROUPS}
    fragments_hidden = 0
    for kind, classes in queue_layers:
        by_priority = {}
        for priority in qa.PRIORITIES:
            _, total = layers.query(
                source, kind, classes=classes, bbox=area, min_area=min_area,
                priorities=[priority], geometry="none", limit=0,
            )
            by_priority[priority] = total
        if not include_fragments:
            _, hidden = layers.query(
                source, kind, classes=classes, bbox=area, max_area=settings.sliver_area_m2,
                geometry="none", limit=0,
            )
            fragments_hidden += hidden
        counts["high"] += by_priority["High"]
        counts["medium"] += by_priority["Medium"]
        counts["low"] += by_priority["Low"]

    # Decided features leave the priority groups they would otherwise be in.
    decided_uids = decided["verified"] + decided["flagged"]
    if decided_uids:
        for kind, classes in queue_layers:
            found, _ = layers.query(
                source, kind, classes=classes, uids=decided_uids, geometry="none",
                limit=len(decided_uids),
            )
            for feature in found:
                area_m2 = feature["properties"].get("area_m2") or 0.0
                if min_area is not None and area_m2 < min_area:
                    continue
                key = (feature["properties"].get("review_priority") or "Low").lower()
                counts[key] = max(counts[key] - 1, 0)
    counts["verified"] = len(decided["verified"])
    counts["flagged"] = len(decided["flagged"])

    items: list[dict[str, Any]] = []
    total = counts[group]

    if group in ("verified", "flagged"):
        wanted = decided[group]
        page = wanted[offset : offset + limit]
        if page:
            for kind, classes in queue_layers:
                found, _ = layers.query(
                    source, kind, uids=page, geometry="none", limit=len(page)
                )
                for feature in found:
                    state = states.get((source, feature["id"]))
                    items.append(_item(feature, map_service.status_for(feature["properties"], state), state))
            items.sort(key=lambda item: item["last_review_at"] or "", reverse=True)
    else:
        priority = group.capitalize()
        # Over-fetch so that features already decided can be skipped while
        # still filling the page.
        skip = set(decided_uids)
        remaining_offset = offset
        for kind, classes in queue_layers:
            if len(items) >= limit:
                break
            cursor = 0
            while len(items) < limit:
                found, _ = layers.query(
                    source, kind, classes=classes, bbox=area, min_area=min_area,
                    priorities=[priority], geometry="none", order="confidence",
                    limit=limit + len(skip) + 1, offset=cursor,
                )
                if not found:
                    break
                for feature in found:
                    if feature["id"] in skip:
                        continue
                    if remaining_offset > 0:
                        remaining_offset -= 1
                        continue
                    state = states.get((source, feature["id"]))
                    items.append(_item(feature, map_service.status_for(feature["properties"], state), state))
                    if len(items) >= limit:
                        break
                cursor += len(found)
                if len(found) < limit + len(skip) + 1:
                    break

    return {
        "source": source,
        "group": group,
        "counts": counts,
        "review_required": counts["high"] + counts["medium"],
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": items,
        "fragments_hidden": fragments_hidden,
        "include_fragments": include_fragments,
        "reviewed_features": len(reviewed_uids),
        "note": (
            "Priority comes from model-derived uncertainty and geometry checks. "
            "It ranks features for attention; it is not proof that a feature is wrong."
        ),
    }


def submit(
    context: SurveyorContext,
    settings: Settings,
    store: Store,
    *,
    source: str,
    feature_id: str,
    action: str,
    comment: str = "",
    edited_geometry: dict[str, Any] | None = None,
    ground_truth: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a surveyor decision. Identity and model signals come from the
    server, never from the request body."""

    if action not in ACTION_STATUS:
        raise ReviewError(
            f"Invalid action '{action}'. Use one of: {', '.join(sorted(ACTION_STATUS))}."
        )

    layers = runtime.get_layers()
    runtime.ensure_source(source)
    feature = layers.get(source, feature_id, geometry="detail")
    if feature is None:
        raise ReviewError(f"Feature '{feature_id}' was not found in this source.", 404)
    properties = feature["properties"]

    comment = (comment or "").strip()
    if action in ("flag", "reject") and not comment:
        raise ReviewError("Give a reason when flagging or rejecting a feature.")

    original_geometry = None
    edit_metrics = None
    if action == "edit":
        if not edited_geometry:
            raise ReviewError("An edit must include the corrected geometry.")
        analysis = analyse_geometry(edited_geometry)
        if analysis["fatal"] or analysis["metrics"] is None:
            raise ReviewError(
                "The corrected geometry is not usable: "
                + ("; ".join(analysis["problems"]) or "invalid polygon")
                + "."
            )
        if analysis["metrics"]["area_m2"] <= 0:
            raise ReviewError("The corrected geometry has no area.")
        blocking = [p for p in analysis["problems"] if p != "ring is not closed"]
        if blocking:
            raise ReviewError("The corrected geometry is not usable: " + "; ".join(blocking) + ".")
        state = store.feature_states(source).get((source, feature_id))
        original_geometry = (state or {}).get("edited_geometry") or feature["geometry"]
        edit_metrics = {
            "area_m2": round(analysis["metrics"]["area_m2"], 3),
            "perimeter_m": round(analysis["metrics"]["perimeter_m"], 3),
            "metric_crs": analysis["metrics"]["metric_crs"],
        }

    truth = None
    if action == "add_ground_truth":
        truth = _clean_ground_truth(ground_truth)
        truth["predicted_class"] = properties.get("class_name")

    review = store.add_review(
        feature_id=feature_id,
        layer=properties.get("layer") or "landcover",
        source=source,
        surveyor_id=context.surveyor_id,
        surveyor_email=context.email,
        assignment_id=context.assignment_id,
        action=action,
        comment=comment,
        model_confidence=properties.get("confidence"),
        model_entropy=properties.get("entropy"),
        original_geometry=original_geometry,
        edited_geometry=edited_geometry if action == "edit" else None,
        ground_truth=truth,
    )
    review["feature_status"] = store.effective_status(feature_id, source)
    if edit_metrics:
        review["edited_metrics"] = edit_metrics
        review["original_area_m2"] = properties.get("area_m2")
    return review


def _clean_ground_truth(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ReviewError("Ground truth needs at least an observed class or a comment.")

    truth: dict[str, Any] = {"kind": "GROUND_TRUTH", "source": "Surveyor field observation"}

    observed = payload.get("observed_class")
    if observed not in (None, ""):
        if observed not in OBSERVED_CLASSES:
            raise ReviewError(
                f"Observed class must be one of: {', '.join(OBSERVED_CLASSES)}."
            )
        truth["observed_class"] = observed

    latitude, longitude = payload.get("latitude"), payload.get("longitude")
    if (latitude in (None, "")) != (longitude in (None, "")):
        raise ReviewError("Give both latitude and longitude for a GNSS position, or neither.")
    if latitude not in (None, ""):
        try:
            latitude, longitude = float(latitude), float(longitude)
        except (TypeError, ValueError) as exc:
            raise ReviewError("Latitude and longitude must be decimal degrees.") from exc
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ReviewError("The GNSS position is outside the valid range.")
        truth["latitude"] = latitude
        truth["longitude"] = longitude

    accuracy = payload.get("accuracy_m")
    if accuracy not in (None, ""):
        try:
            accuracy = float(accuracy)
        except (TypeError, ValueError) as exc:
            raise ReviewError("GNSS accuracy must be a number of metres.") from exc
        if accuracy < 0:
            raise ReviewError("GNSS accuracy cannot be negative.")
        truth["accuracy_m"] = accuracy

    for key in ("observation", "device", "photo_reference", "observed_at"):
        value = payload.get(key)
        if value not in (None, ""):
            truth[key] = str(value)[:500]

    if not any(k in truth for k in ("observed_class", "latitude", "observation")):
        raise ReviewError("Ground truth needs an observed class, a position or an observation note.")
    return truth


def update(
    context: SurveyorContext,
    store: Store,
    review_id: str,
    *,
    comment: str | None = None,
    action: str | None = None,
) -> dict[str, Any]:
    current = store.get_review(review_id)
    if current is None:
        raise ReviewError(f"Review '{review_id}' not found.", 404)
    if current["surveyor_id"] != context.surveyor_id:
        raise ReviewError("Only the surveyor who recorded a review can change it.", 403)
    try:
        return store.update_review(
            review_id,
            actor_id=context.surveyor_id,
            actor_email=context.email,
            comment=comment,
            action=action,
        )
    except ValueError as exc:
        raise ReviewError(str(exc)) from exc
