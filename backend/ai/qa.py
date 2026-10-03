"""Review prioritisation from measurable signals.

Nothing here decides whether a feature is right or wrong. It ranks features
for the surveyor's attention and says why, using model-derived uncertainty
(when it was recorded) and geometry checks. Thresholds for confidence and
entropy are the ones the original pipeline used.
"""

from __future__ import annotations

import math
from typing import Any

from backend.ai.classes import NUM_CLASSES

PRIORITIES = ("High", "Medium", "Low")
_RANK = {"Low": 0, "Medium": 1, "High": 2}

MAX_ENTROPY = math.log(NUM_CLASSES)

CONFIDENCE_HIGH_RISK = 0.60
CONFIDENCE_MEDIUM_RISK = 0.80
ENTROPY_HIGH_RISK = 1.20
ENTROPY_MEDIUM_RISK = 0.70

# Geometry complexity is only judged on features large enough to matter.
COMPLEXITY_MIN_AREA_M2 = 25.0
COMPACTNESS_MEDIUM = 0.02
COMPACTNESS_HIGH = 0.005
RINGS_MEDIUM = 50
RINGS_HIGH = 500


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def uncertainty_band(confidence: float | None, entropy: float | None) -> dict[str, Any]:
    """Human-readable reading of the model-derived uncertainty."""

    confidence = _num(confidence)
    entropy = _num(entropy)
    if confidence is None and entropy is None:
        return {
            "available": False,
            "label": "Model uncertainty not recorded",
            "level": None,
        }

    level = "low"
    if (confidence is not None and confidence < CONFIDENCE_HIGH_RISK) or (
        entropy is not None and entropy > ENTROPY_HIGH_RISK
    ):
        level = "high"
    elif (confidence is not None and confidence < CONFIDENCE_MEDIUM_RISK) or (
        entropy is not None and entropy > ENTROPY_MEDIUM_RISK
    ):
        level = "moderate"

    label = {
        "low": "High confidence · low uncertainty",
        "moderate": "Medium confidence · moderate uncertainty",
        "high": "Low confidence · high uncertainty",
    }[level]
    return {
        "available": True,
        "label": label,
        "level": level,
        "normalized_entropy": (
            round(min(max(entropy / MAX_ENTROPY, 0.0), 1.0), 4) if entropy is not None else None
        ),
    }


def assess(
    *,
    layer: str,
    area_m2: float | None,
    compactness: float | None = None,
    rings: int | None = None,
    confidence: float | None = None,
    entropy: float | None = None,
    geometry_status: str = "NOT_CHECKED",
    geometry_problems: list[str] | None = None,
    has_overlap: bool = False,
    road_access: bool | None = None,
    sliver_area_m2: float = 1.0,
    min_parcel_area_m2: float = 25.0,
    road_access_distance_m: float = 5.0,
) -> dict[str, Any]:
    """Return ``{"priority", "reasons", "flags"}`` for one feature."""

    priority = "Low"
    reasons: list[str] = []
    flags: list[str] = []

    def raise_to(level: str, reason: str, flag: str | None = None) -> None:
        nonlocal priority
        if _RANK[level] > _RANK[priority]:
            priority = level
        reasons.append(reason)
        if flag and flag not in flags:
            flags.append(flag)

    confidence = _num(confidence)
    entropy = _num(entropy)
    area = _num(area_m2)
    compactness = _num(compactness)

    # --- model-derived uncertainty -------------------------------------
    if confidence is None and entropy is None:
        flags.append("UNCERTAINTY_UNAVAILABLE")
    else:
        if confidence is not None:
            if confidence < CONFIDENCE_HIGH_RISK:
                raise_to("High", f"Low model confidence ({confidence:.2f})", "LOW_CONFIDENCE")
            elif confidence < CONFIDENCE_MEDIUM_RISK:
                raise_to("Medium", f"Moderate model confidence ({confidence:.2f})", "MODERATE_CONFIDENCE")
        if entropy is not None:
            if entropy > ENTROPY_HIGH_RISK:
                raise_to("High", f"High model entropy ({entropy:.2f})", "HIGH_ENTROPY")
            elif entropy > ENTROPY_MEDIUM_RISK:
                raise_to("Medium", f"Moderate model entropy ({entropy:.2f})", "MODERATE_ENTROPY")

    # --- geometry validity ---------------------------------------------
    if geometry_status == "INVALID":
        detail = f": {geometry_problems[0]}" if geometry_problems else ""
        raise_to("High", f"Geometry is invalid{detail}", "INVALID_GEOMETRY")
    elif geometry_status == "REPAIRED":
        raise_to("Medium", "Geometry was repaired automatically", "REPAIRED_GEOMETRY")

    if has_overlap:
        raise_to("Medium", "Overlaps another feature", "OVERLAP")

    # --- fragmentation --------------------------------------------------
    is_fragment = area is not None and area < sliver_area_m2
    if is_fragment:
        flags.append("FRAGMENT")
        reasons.append(
            f"Fragment: {area:.2f} m² is below the {sliver_area_m2:g} m² minimum mapping unit"
        )

    # --- suspicious shape -----------------------------------------------
    if area is not None and area >= COMPLEXITY_MIN_AREA_M2:
        if rings is not None and rings > RINGS_HIGH:
            raise_to(
                "High",
                f"Very complex boundary: {rings - 1:,} interior holes, likely several features merged",
                "COMPLEX_GEOMETRY",
            )
        elif rings is not None and rings > RINGS_MEDIUM:
            raise_to("Medium", f"Complex boundary: {rings - 1:,} interior holes", "COMPLEX_GEOMETRY")

        if compactness is not None and "COMPLEX_GEOMETRY" not in flags:
            if compactness < COMPACTNESS_HIGH:
                raise_to("High", "Highly irregular outline for its area", "IRREGULAR_SHAPE")
            elif compactness < COMPACTNESS_MEDIUM:
                raise_to("Medium", "Irregular outline for its area", "IRREGULAR_SHAPE")

    # --- parcel-specific -------------------------------------------------
    if layer == "parcels" and area is not None:
        if area < min_parcel_area_m2 and not is_fragment:
            flags.append("BELOW_PARCEL_MINIMUM")
            reasons.append(
                f"Smaller than the {min_parcel_area_m2:g} m² minimum for a candidate parcel"
            )
        elif area >= min_parcel_area_m2 and road_access is False:
            raise_to(
                "Medium",
                f"No road detected within {road_access_distance_m:g} m",
                "NO_ROAD_ACCESS",
            )

    return {"priority": priority, "reasons": reasons, "flags": flags}


def default_status(priority: str) -> str:
    """Verification status of a feature no surveyor has looked at yet."""

    return "REVIEW_REQUIRED" if priority in ("High", "Medium") else "AI_GENERATED"
