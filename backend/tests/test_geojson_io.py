"""GeoJSON validation: structure, CRS detection and reprojection."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from backend.gis import geojson_io
from backend.gis.geojson_io import GeoJSONError
from backend.gis.geometry import polygon_metrics
from backend.tests import support


class GeoJSONReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_valid_collection_is_walked_in_order(self):
        path = support.write_json(
            self.dir / "ok.geojson", support.collection("ok", support.landcover_features())
        )
        seen = []
        header = geojson_io.scan_feature_collection(path, seen.append)
        self.assertEqual(header["type"], "FeatureCollection")
        self.assertEqual(header["feature_count"], len(support.LANDCOVER))
        self.assertEqual([f["properties"]["feature_id"] for f in seen], list(range(1, len(seen) + 1)))
        self.assertEqual(geojson_io.crs_name(header), "OGC:CRS84")

    def test_missing_file(self):
        with self.assertRaises(GeoJSONError):
            geojson_io.read_header(self.dir / "absent.geojson")

    def test_invalid_json_is_rejected_with_a_position(self):
        path = self.write("broken.geojson", '{"type":"FeatureCollection","features":[{"type":"Feature",]}')
        with self.assertRaises(GeoJSONError) as caught:
            geojson_io.read_header(path)
        self.assertIn("not valid JSON", str(caught.exception))

    def test_not_a_json_object(self):
        with self.assertRaises(GeoJSONError):
            geojson_io.read_header(self.write("list.geojson", "[1, 2, 3]"))

    def test_single_feature_is_not_a_collection(self):
        path = self.write("feature.geojson", json.dumps(support.landcover_features()[0]))
        with self.assertRaises(GeoJSONError) as caught:
            geojson_io.read_header(path)
        self.assertIn("FeatureCollection", str(caught.exception))

    def test_collection_without_features_member(self):
        with self.assertRaises(GeoJSONError):
            geojson_io.read_header(self.write("empty.geojson", '{"type":"FeatureCollection"}'))


class CrsTests(unittest.TestCase):
    def test_no_crs_member_means_wgs84_lonlat(self):
        self.assertEqual(geojson_io.crs_name({}), "OGC:CRS84")

    def test_named_crs_forms(self):
        def named(name):
            return {"crs": {"type": "name", "properties": {"name": name}}}

        self.assertEqual(geojson_io.crs_name(named("urn:ogc:def:crs:OGC:1.3:CRS84")), "OGC:CRS84")
        self.assertEqual(geojson_io.crs_name(named("urn:ogc:def:crs:EPSG::32643")), "EPSG:32643")
        self.assertEqual(geojson_io.crs_name(named("EPSG:3857")), "EPSG:3857")

    def test_lonlat_needs_no_transform(self):
        self.assertIsNone(geojson_io.lonlat_transformer("OGC:CRS84"))
        self.assertIsNone(geojson_io.lonlat_transformer("EPSG:4326"))

    def test_utm_layer_is_reprojected_not_assumed_to_be_wgs84(self):
        ring = support.rect_utm(0, 0, 40, 30)
        projected = {"type": "Polygon", "coordinates": [ring]}
        transform = geojson_io.lonlat_transformer("EPSG:32643")
        self.assertIsNotNone(transform)
        lonlat = geojson_io.reproject_geometry(projected, transform)

        first = lonlat["coordinates"][0][0]
        self.assertAlmostEqual(first[0], support.ORIGIN_LON, places=6)
        self.assertAlmostEqual(first[1], support.ORIGIN_LAT, places=6)
        self.assertAlmostEqual(polygon_metrics(lonlat)["area_m2"], 1200.0, delta=0.05)

    def test_web_mercator_origin(self):
        transform = geojson_io.lonlat_transformer("EPSG:3857")
        out = transform(np.array([[0.0, 0.0]]))
        np.testing.assert_allclose(out, [[0.0, 0.0]], atol=1e-9)


if __name__ == "__main__":
    unittest.main()
