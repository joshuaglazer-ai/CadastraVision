"""Projection, measurement and validation of longitude/latitude polygons."""

import unittest

import numpy as np

from backend.gis import geometry
from backend.tests import support


class UtmProjectionTests(unittest.TestCase):
    def test_zone_and_epsg_for_the_survey_area(self):
        self.assertEqual(geometry.utm_zone(77.6254, 28.559), (43, True))
        self.assertEqual(geometry.utm_epsg(43, True), 32643)
        self.assertEqual(geometry.utm_epsg(43, False), 32743)

    def test_round_trip_is_sub_millimetre(self):
        lon = np.array([72.1, 77.6254, 77.99])
        lat = np.array([8.5, 28.559, 34.0])
        easting, northing = geometry.lonlat_to_utm(lon, lat, 43, True)
        back_lon, back_lat = geometry.utm_to_lonlat(easting, northing, 43, True)
        # 1e-9 degrees is about 0.1 mm on the ground.
        np.testing.assert_allclose(back_lon, lon, atol=1e-9)
        np.testing.assert_allclose(back_lat, lat, atol=1e-9)

    def test_central_meridian_has_false_easting(self):
        easting, northing = geometry.lonlat_to_utm(75.0, 0.0, 43, True)
        self.assertAlmostEqual(float(easting), 500000.0, places=3)
        self.assertAlmostEqual(float(northing), 0.0, places=3)


class MeasurementTests(unittest.TestCase):
    def test_rectangle_is_measured_in_metres_not_degrees(self):
        metrics = geometry.polygon_metrics(support.rect(0, 0, 40, 30))
        self.assertAlmostEqual(metrics["area_m2"], 1200.0, delta=0.05)
        self.assertAlmostEqual(metrics["perimeter_m"], 140.0, delta=0.01)
        self.assertAlmostEqual(metrics["width_m"], 40.0, delta=0.01)
        self.assertAlmostEqual(metrics["length_m"], 30.0, delta=0.01)
        self.assertEqual(metrics["metric_crs"], "EPSG:32643")
        self.assertEqual((metrics["rings"], metrics["holes"]), (1, 0))

    def test_hole_is_subtracted_from_the_area(self):
        outer = support.rect(0, 0, 40, 30)["coordinates"][0]
        hole = support.rect(10, 10, 10, 5)["coordinates"][0]
        metrics = geometry.polygon_metrics({"type": "Polygon", "coordinates": [outer, hole]})
        self.assertAlmostEqual(metrics["area_m2"], 1200.0 - 50.0, delta=0.05)
        self.assertEqual(metrics["holes"], 1)

    def test_multipolygon_parts_are_summed(self):
        geom = {
            "type": "MultiPolygon",
            "coordinates": [
                support.rect(0, 0, 10, 10)["coordinates"],
                support.rect(50, 0, 20, 10)["coordinates"],
            ],
        }
        self.assertAlmostEqual(geometry.polygon_metrics(geom)["area_m2"], 300.0, delta=0.05)

    def test_sub_square_metre_fragment_is_not_reported_as_zero_area(self):
        metrics = geometry.polygon_metrics(support.rect(100, 0, 0.5, 0.5))
        self.assertAlmostEqual(metrics["area_m2"], 0.25, delta=0.001)

    def test_bbox(self):
        geom = support.rect(0, 0, 40, 30)
        minx, miny, maxx, maxy = geometry.geometry_bbox(geom)
        self.assertLess(minx, maxx)
        self.assertLess(miny, maxy)
        self.assertAlmostEqual(minx, support.ORIGIN_LON, places=6)


class ValidationTests(unittest.TestCase):
    def test_sound_polygon_has_no_problems(self):
        self.assertEqual(geometry.validate_geometry(support.rect(0, 0, 40, 30)), [])

    def test_missing_and_unsupported_geometry(self):
        self.assertEqual(geometry.validate_geometry(None), ["geometry is missing"])
        self.assertIn(
            "unsupported geometry type 'Point'",
            geometry.validate_geometry({"type": "Point", "coordinates": [0, 0]}),
        )

    def test_too_few_positions_is_fatal(self):
        analysis = geometry.analyse_geometry(
            {"type": "Polygon", "coordinates": [[[77.0, 28.0], [77.001, 28.001]]]}
        )
        self.assertTrue(analysis["fatal"])
        self.assertIn("exterior ring has fewer than 4 positions", analysis["problems"])

    def test_unclosed_ring_is_reported_but_measurable(self):
        ring = support.rect(0, 0, 40, 30)["coordinates"][0][:-1]
        analysis = geometry.analyse_geometry({"type": "Polygon", "coordinates": [ring]})
        self.assertIn("ring is not closed", analysis["problems"])
        self.assertFalse(analysis["fatal"])
        self.assertAlmostEqual(analysis["metrics"]["area_m2"], 1200.0, delta=0.05)

    def test_coordinates_outside_lonlat_range_are_fatal(self):
        ring = [[756000.0, 3161000.0], [756100.0, 3161000.0], [756100.0, 3161100.0], [756000.0, 3161000.0]]
        analysis = geometry.analyse_geometry({"type": "Polygon", "coordinates": [ring]})
        self.assertTrue(analysis["fatal"])
        self.assertIn("coordinates fall outside longitude/latitude range", analysis["problems"])


class GeneralisationTests(unittest.TestCase):
    def test_collinear_vertices_are_removed_and_area_kept(self):
        # A 40 x 30 m rectangle with an extra vertex every metre on each side.
        e0, n0 = support.origin_utm()
        points = (
            [(x, 0) for x in range(0, 40)]
            + [(40, y) for y in range(0, 30)]
            + [(x, 30) for x in range(40, 0, -1)]
            + [(0, y) for y in range(30, 0, -1)]
        )
        ring = []
        for x, y in points:
            lon, lat = geometry.utm_to_lonlat(e0 + x, n0 + y, support.ZONE, True)
            ring.append([float(lon), float(lat)])
        ring.append(ring[0])
        dense = {"type": "Polygon", "coordinates": [ring]}

        simple = geometry.simplify_geometry(dense, tolerance_m=0.05)
        self.assertIsNotNone(simple)
        self.assertLess(len(simple["coordinates"][0]), 10)
        self.assertEqual(simple["coordinates"][0][0], simple["coordinates"][0][-1])
        self.assertAlmostEqual(geometry.polygon_metrics(simple)["area_m2"], 1200.0, delta=0.5)

    def test_feature_smaller_than_the_tolerance_disappears(self):
        tiny = support.rect(0, 0, 0.1, 0.1)
        self.assertIsNone(geometry.simplify_geometry(tiny, tolerance_m=0.4, min_part_area_m2=1.0))


class ContainmentTests(unittest.TestCase):
    def test_point_in_geometry_respects_holes(self):
        outer = support.rect(0, 0, 40, 30)["coordinates"][0]
        hole = support.rect(10, 10, 10, 5)["coordinates"][0]
        geom = {"type": "Polygon", "coordinates": [outer, hole]}
        e0, n0 = support.origin_utm()

        def lonlat(x, y):
            lon, lat = geometry.utm_to_lonlat(e0 + x, n0 + y, support.ZONE, True)
            return float(lon), float(lat)

        self.assertTrue(geometry.point_in_geometry(*lonlat(2, 2), geom))
        self.assertFalse(geometry.point_in_geometry(*lonlat(15, 12), geom))  # in the hole
        self.assertFalse(geometry.point_in_geometry(*lonlat(60, 2), geom))


if __name__ == "__main__":
    unittest.main()
