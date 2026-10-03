"""Candidate parcel reasoning for AI output (no authoritative parcel GIS).

    drone / ORI -> AI segmentation -> candidate spatial structures
                -> candidate parcel reasoning -> surveyor verification

A candidate parcel is a contiguous Field region of the segmentation, with
its relation to the nearest Road feature. This is the same definition the
project's existing ``candidate_parcels.geojson`` uses (there, road access
is "a road within 5 m").

These are spatial candidates for a surveyor to inspect. They are never
legal cadastral boundaries.
"""

from __future__ import annotations

from typing import Any

FIELD_CLASS_ID = 1
ROAD_CLASS_ID = 3
BOUNDARY_STATUS = "Unverified candidate"


def derive_candidate_parcels(features, road_access_distance_m: float = 5.0):
    """Build the candidate parcel layer from AI features.

    ``features`` is a GeoDataFrame in a metric CRS with at least
    ``class_id``, ``feature_id`` and the measured attribute columns.
    Returns a GeoDataFrame in the same CRS.
    """

    import numpy as np
    import shapely

    fields = features[features["class_id"] == FIELD_CLASS_ID].copy()
    fields = fields.reset_index(drop=True)

    fields["source_feature_id"] = fields["feature_id"]
    fields["parcel_id"] = [f"CAND-{index:06d}" for index in range(1, len(fields) + 1)]
    fields["boundary_status"] = BOUNDARY_STATUS
    fields = fields.drop(columns=["feature_id"])

    roads = features[features["class_id"] == ROAD_CLASS_ID]
    distances: list[Any] = [None] * len(fields)
    access: list[Any] = [None] * len(fields)

    if len(fields) and len(roads):
        road_geometries = np.asarray(roads.geometry.values)
        tree = shapely.STRtree(road_geometries)
        field_geometries = np.asarray(fields.geometry.values)
        (field_index, _road_index), nearest = tree.query_nearest(
            field_geometries, return_distance=True, all_matches=False
        )
        for position, distance in zip(field_index, nearest):
            distances[int(position)] = float(distance)
            access[int(position)] = bool(distance <= road_access_distance_m)

    # ``None`` means "no road features were detected", which is different
    # from "a road is far away"; it is kept as None rather than False.
    fields["nearest_road_distance_m"] = distances
    fields["road_access_candidate"] = access
    return fields
