"""Synthetic fixtures for the tests.

Everything here is test data with known, exact measurements (squares and
rectangles laid out in UTM zone 43N and converted to longitude/latitude).
None of it is shipped to the application or presented to a surveyor.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.gis.geometry import lonlat_to_utm, utm_to_lonlat

ZONE = 43
ORIGIN_LON, ORIGIN_LAT = 77.6250, 28.5590
SURVEYOR_EMAIL = "asha.surveyor@example.test"

CRS84 = {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}


def origin_utm() -> tuple[float, float]:
    easting, northing = lonlat_to_utm(ORIGIN_LON, ORIGIN_LAT, ZONE, True)
    return float(easting), float(northing)


def rect_utm(x: float, y: float, width: float, height: float) -> list[list[float]]:
    """Closed ring of a rectangle, in UTM metres, offset from the origin."""

    e0, n0 = origin_utm()
    return [
        [e0 + x, n0 + y],
        [e0 + x + width, n0 + y],
        [e0 + x + width, n0 + y + height],
        [e0 + x, n0 + y + height],
        [e0 + x, n0 + y],
    ]


def rect(x: float, y: float, width: float, height: float) -> dict[str, Any]:
    """Rectangle of known size as a longitude/latitude GeoJSON polygon."""

    ring = []
    for easting, northing in rect_utm(x, y, width, height):
        lon, lat = utm_to_lonlat(easting, northing, ZONE, True)
        ring.append([round(float(lon), 9), round(float(lat), 9)])
    ring[-1] = list(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def feature(geometry: dict[str, Any], **properties: Any) -> dict[str, Any]:
    return {"type": "Feature", "properties": properties, "geometry": geometry}


def collection(name: str, features: list[dict[str, Any]], crs: dict | None = CRS84) -> dict[str, Any]:
    data: dict[str, Any] = {"type": "FeatureCollection", "name": name}
    if crs is not None:
        data["crs"] = crs
    data["features"] = features
    return data


# (class_id, class_name, x, y, width, height) in metres from the origin.
LANDCOVER = [
    (1, "Field", 0, 0, 40, 30),      # FLD-000001  1200 m2
    (1, "Field", 60, 0, 20, 20),     # FLD-000002   400 m2
    (1, "Field", 100, 0, 0.5, 0.5),  # FLD-000003  0.25 m2 fragment
    (2, "Building", 5, 5, 10, 8),    # BLD-000004    80 m2, inside the first field
    (2, "Building", 65, 5, 6, 6),    # BLD-000005    36 m2, inside the second field
    (3, "Road", 0, -6, 100, 4),      # RD-000006    400 m2, 2 m south of the fields
    (4, "Water", 0, 50, 10, 10),     # WTR-000007   100 m2
    (5, "Other", 30, 50, 5, 5),      # OTH-000008    25 m2
    (1, "Field", 0, 200, 30, 30),    # FLD-000009   900 m2, far from any road
]

# (parcel_id, x, y, width, height, road_access)
PARCELS = [
    ("CAND-000001", 0, 0, 40, 30, True),
    ("CAND-000002", 60, 0, 20, 20, True),
    ("CAND-000003", 100, 0, 0.5, 0.5, True),
    ("CAND-000004", 0, 200, 30, 30, False),
]


def landcover_features() -> list[dict[str, Any]]:
    return [
        feature(rect(x, y, w, h), class_id=cid, class_name=name, feature_id=index)
        for index, (cid, name, x, y, w, h) in enumerate(LANDCOVER, start=1)
    ]


def parcel_features() -> list[dict[str, Any]]:
    return [
        feature(
            rect(x, y, w, h),
            class_id=1,
            class_name="Field",
            source_class="Field",
            feature_id=index,
            parcel_id=parcel_id,
            road_access_candidate=access,
            nearest_road_distance_m=2.0 if access else 190.0,
            boundary_status="Unverified candidate",
        )
        for index, (parcel_id, x, y, w, h, access) in enumerate(PARCELS, start=1)
    ]


def assignment_registry() -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "name": "surveyor_assignments",
        "features": [
            feature(
                rect(-50, -50, 300, 400),
                assignment_id="ASGN-TEST-DEMO",
                is_demo=True,
                assigned_to=[],
                assignment_status="active",
                district="Test District",
                taluk="Test Taluk",
                village="Test Village",
            ),
            feature(
                rect(-20, -20, 150, 100),
                assignment_id="ASGN-TEST-001",
                is_demo=False,
                assigned_to=[SURVEYOR_EMAIL],
                surveyor_id="SRV-TEST-7",
                assignment_status="active",
                district="Test District",
                taluk="Test Taluk",
                village="Assigned Village",
            ),
        ],
    }


def write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def write_fixture_data(data_dir: Path) -> None:
    write_json(
        data_dir / "parcels" / "candidate_parcels.geojson",
        collection("candidate_parcels", parcel_features()),
    )
    write_json(
        data_dir / "landcover" / "uplarshi_landcover_with_attributes.geojson",
        collection("landcover", landcover_features()),
    )
    write_json(data_dir / "surveyors" / "surveyor_assignments.geojson", assignment_registry())
