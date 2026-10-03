"""The indexed layer cache: ingest, spatial filtering, statistics."""

import os
import tempfile
import unittest
from pathlib import Path

from backend.gis.geojson_io import GeoJSONError
from backend.gis.layers import LayerStore
from backend.tests import support


class LayerStoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.layers = LayerStore(self.dir / "cache" / "layers.db")
        self.landcover = support.write_json(
            self.dir / "landcover.geojson", support.collection("landcover", support.landcover_features())
        )
        self.parcels = support.write_json(
            self.dir / "parcels.geojson", support.collection("parcels", support.parcel_features())
        )

    def ingest(self):
        self.layers.ensure("existing", "landcover", self.landcover)
        self.layers.ensure("existing", "parcels", self.parcels)


class IngestTests(LayerStoreCase):
    def test_missing_file_is_data_unavailable_not_an_error(self):
        self.assertIsNone(self.layers.ensure("existing", "parcels", self.dir / "absent.geojson"))
        self.assertIsNone(self.layers.layer("existing", "parcels"))
        self.assertIsNone(self.layers.stats("existing", "parcels"))

    def test_layer_record(self):
        record = self.layers.ensure("existing", "landcover", self.landcover)
        self.assertEqual(record["feature_count"], len(support.LANDCOVER))
        self.assertEqual(record["skipped_count"], 0)
        self.assertEqual(record["crs"], "OGC:CRS84")
        minx, miny, maxx, maxy = record["bbox"]
        self.assertLess(minx, maxx)
        self.assertLess(miny, maxy)

    def test_identifiers_follow_the_class(self):
        self.ingest()
        features, total = self.layers.query("existing", "landcover", geometry="none", order="uid")
        self.assertEqual(total, len(support.LANDCOVER))
        ids = {feature["id"] for feature in features}
        self.assertEqual(
            ids,
            {"FLD-000001", "FLD-000002", "FLD-000003", "BLD-000004", "BLD-000005",
             "RD-000006", "WTR-000007", "OTH-000008", "FLD-000009"},
        )
        parcels, _ = self.layers.query("existing", "parcels", geometry="none", order="uid")
        self.assertEqual([p["id"] for p in parcels], [p[0] for p in support.PARCELS])

    def test_measurements_are_metric(self):
        self.ingest()
        field = self.layers.get("existing", "FLD-000001")
        properties = field["properties"]
        self.assertAlmostEqual(properties["area_m2"], 1200.0, delta=0.05)
        self.assertAlmostEqual(properties["perimeter_m"], 140.0, delta=0.01)
        self.assertEqual(properties["metric_crs"], "EPSG:32643")
        self.assertEqual(properties["metrics_source"], "computed")
        self.assertEqual(field["geometry"]["type"], "Polygon")

    def test_supplied_attributes_are_kept_and_the_measurement_recorded_beside_them(self):
        features = support.parcel_features()
        features[0]["properties"]["area_m2"] = 1234.5
        path = support.write_json(self.dir / "attr.geojson", support.collection("p", features))
        self.layers.ensure("existing", "parcels", path)
        properties = self.layers.get("existing", "CAND-000001")["properties"]
        self.assertEqual(properties["area_m2"], 1234.5)
        self.assertEqual(properties["metrics_source"], "attribute")
        self.assertAlmostEqual(properties["measured_area_m2"], 1200.0, delta=0.05)

    def test_no_model_uncertainty_is_recorded_as_absent(self):
        self.ingest()
        properties = self.layers.get("existing", "BLD-000004")["properties"]
        self.assertIsNone(properties["confidence"])
        self.assertIsNone(properties["entropy"])
        self.assertIn("UNCERTAINTY_UNAVAILABLE", properties["qa_flags"])

    def test_model_uncertainty_drives_priority_when_present(self):
        features = support.landcover_features()
        features[3]["properties"].update(confidence=0.42, entropy=1.4)
        path = support.write_json(self.dir / "conf.geojson", support.collection("c", features))
        self.layers.ensure("job:JOB-T", "landcover", path)
        properties = self.layers.get("job:JOB-T", "BLD-000004")["properties"]
        self.assertEqual(properties["review_priority"], "High")
        self.assertEqual(properties["confidence"], 0.42)

    def test_parcel_without_road_access_needs_review(self):
        self.ingest()
        isolated = self.layers.get("existing", "CAND-000004")["properties"]
        self.assertEqual(isolated["review_priority"], "Medium")
        self.assertIn("NO_ROAD_ACCESS", isolated["qa_flags"])
        self.assertEqual(self.layers.get("existing", "CAND-000001")["properties"]["review_priority"], "Low")

    def test_unusable_features_are_counted_never_silently_dropped(self):
        features = support.landcover_features()[:2]
        features.append(support.feature({"type": "Polygon", "coordinates": [[[77.0, 28.0], [77.1, 28.1]]]}, class_id=2))
        features.append(support.feature(None, class_id=3))
        features.append({"type": "NotAFeature"})
        path = support.write_json(self.dir / "mixed.geojson", support.collection("m", features))
        record = self.layers.ensure("existing", "landcover", path)
        self.assertEqual(record["feature_count"], 2)
        self.assertEqual(record["skipped_count"], 3)
        self.assertEqual(len(record["meta"]["skipped_examples"]), 3)

    def test_projected_layer_is_reprojected_using_its_declared_crs(self):
        crs = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32643"}}
        projected = support.feature(
            {"type": "Polygon", "coordinates": [support.rect_utm(0, 0, 40, 30)]}, class_id=1, class_name="Field"
        )
        path = support.write_json(self.dir / "utm.geojson", support.collection("utm", [projected], crs=crs))
        record = self.layers.ensure("existing", "landcover", path)
        self.assertEqual(record["crs"], "EPSG:32643")
        feature = self.layers.get("existing", "FLD-000001")
        self.assertAlmostEqual(feature["properties"]["area_m2"], 1200.0, delta=0.05)
        lon, lat = feature["geometry"]["coordinates"][0][0]
        self.assertAlmostEqual(lon, support.ORIGIN_LON, places=6)
        self.assertAlmostEqual(lat, support.ORIGIN_LAT, places=6)

    def test_broken_file_raises_a_useful_error(self):
        path = self.dir / "broken.geojson"
        path.write_text('{"type": "FeatureCollection", "features": [', encoding="utf-8")
        with self.assertRaises(GeoJSONError):
            self.layers.ensure("existing", "landcover", path)

    def test_cache_is_reused_until_the_file_changes(self):
        first = self.layers.ensure("existing", "landcover", self.landcover)
        again = self.layers.ensure("existing", "landcover", self.landcover)
        self.assertEqual(first["ingested_at"], again["ingested_at"])
        self.assertEqual(self.layers.query("existing", "landcover", geometry="none")[1], len(support.LANDCOVER))

        support.write_json(self.landcover, support.collection("landcover", support.landcover_features()[:3]))
        stat = self.landcover.stat()
        os.utime(self.landcover, (stat.st_atime, stat.st_mtime + 5))
        changed = self.layers.ensure("existing", "landcover", self.landcover)
        self.assertEqual(changed["feature_count"], 3)
        self.assertEqual(self.layers.query("existing", "landcover", geometry="none")[1], 3)

    def test_index_survives_a_restart(self):
        self.ingest()
        reopened = LayerStore(self.layers.db_path)
        self.assertEqual(reopened.layer("existing", "parcels")["feature_count"], len(support.PARCELS))
        self.assertIsNotNone(reopened.get("existing", "CAND-000002"))


class QueryTests(LayerStoreCase):
    def setUp(self):
        super().setUp()
        self.ingest()

    def test_filter_by_class(self):
        features, total = self.layers.query("existing", "landcover", classes=["building"], geometry="none")
        self.assertEqual(total, 2)
        self.assertEqual({f["properties"]["class_name"] for f in features}, {"Building"})

    def test_filter_by_bounding_box(self):
        # A box over the second field only (x 62..78 m, y 3..17 m). It is kept
        # well inside the field because UTM grid north is about 1.3 degrees
        # off true north here, so lon/lat boxes of neighbours are a little
        # larger than their UTM footprints.
        box = support.rect(62, 3, 16, 14)["coordinates"][0]
        lons = [p[0] for p in box]
        lats = [p[1] for p in box]
        bbox = (min(lons), min(lats), max(lons), max(lats))
        features, total = self.layers.query("existing", "landcover", bbox=bbox, geometry="none", order="uid")
        self.assertEqual(total, 2)
        self.assertEqual([f["id"] for f in features], ["BLD-000005", "FLD-000002"])

        nothing, total = self.layers.query("existing", "landcover", bbox=(10.0, 10.0, 11.0, 11.0))
        self.assertEqual((nothing, total), ([], 0))

    def test_area_filter_limit_and_total(self):
        features, total = self.layers.query("existing", "landcover", min_area=1.0, geometry="none", limit=3)
        self.assertEqual(total, len(support.LANDCOVER) - 1)  # the 0.25 m2 fragment is excluded
        self.assertEqual(len(features), 3)
        areas = [f["properties"]["area_m2"] for f in features]
        self.assertEqual(areas, sorted(areas, reverse=True))

    def test_overview_leaves_out_features_too_small_to_draw(self):
        overview, total = self.layers.query("existing", "parcels", geometry="overview")
        self.assertEqual(total, 3)
        self.assertNotIn("CAND-000003", [f["id"] for f in overview])
        self.assertTrue(all(f["geometry"] for f in overview))

    def test_lookup_by_uid_and_priority(self):
        features, total = self.layers.query("existing", "parcels", uids=["CAND-000002", "CAND-000004"], geometry="none")
        self.assertEqual(total, 2)
        medium, total = self.layers.query("existing", "parcels", priorities=["Medium"], geometry="none")
        self.assertEqual([f["id"] for f in medium], ["CAND-000004"])
        self.assertIsNone(self.layers.get("existing", "CAND-999999"))

    def test_full_resolution_stream_for_export(self):
        exported = list(self.layers.iter_full("existing", "landcover", batch=2))
        self.assertEqual(len(exported), len(support.LANDCOVER))
        self.assertTrue(all(f["geometry"]["coordinates"] for f in exported))

    def test_statistics_come_from_the_data(self):
        stats = self.layers.stats("existing", "landcover")
        self.assertEqual(stats["totals"]["features"], len(support.LANDCOVER))
        self.assertEqual(stats["totals"]["fragments"], 1)
        self.assertEqual(stats["totals"]["with_confidence"], 0)
        self.assertEqual(stats["classes"]["building"]["count"], 2)
        self.assertAlmostEqual(stats["classes"]["building"]["area_m2"], 116.0, delta=0.05)
        self.assertAlmostEqual(stats["classes"]["road"]["area_m2"], 400.0, delta=0.05)
        self.assertIsNone(stats["classes"]["road"]["mean_confidence"])
        self.assertNotIn("background", stats["classes"])

    def test_drop_removes_a_source(self):
        self.layers.drop("existing", "parcels")
        self.assertIsNone(self.layers.layer("existing", "parcels"))
        self.assertIsNotNone(self.layers.layer("existing", "landcover"))


if __name__ == "__main__":
    unittest.main()
