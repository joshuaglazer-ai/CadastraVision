"""The AI pipeline end to end on a synthetic GeoTIFF with exact geometry.

Needs PyTorch, Rasterio, GeoPandas and Shapely. The trained network is
replaced by ``rasters.ColourModel`` so that every expected class, area,
confidence and entropy is known in advance; everything else (windowed
reading, NoData, the three output rasters, chunked polygonisation and seam
stitching, repair, metric measurement, QA, GeoJSON output) is the real code.
The trained checkpoint itself is covered by ``test_model`` and by
``python -m backend.scripts.selfcheck``.
"""

import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from backend.ai import pipeline
from backend.ai.pipeline import PipelineError
from backend.tests import rasters

try:
    import geopandas  # noqa: F401
    import rasterio
    import shapely  # noqa: F401
    import torch  # noqa: F401

    HAVE_STACK = True
    MISSING = ""
except Exception as exc:  # ImportError or a broken install
    HAVE_STACK = False
    MISSING = str(exc)

CONFIDENT = math.exp(8.0) / (math.exp(8.0) + 5.0)
UNSURE_SUM = math.exp(1.1) + math.exp(1.0) + 4.0
UNSURE_CONFIDENCE = math.exp(1.1) / UNSURE_SUM
_P = [math.exp(1.1) / UNSURE_SUM, math.exp(1.0) / UNSURE_SUM] + [1.0 / UNSURE_SUM] * 4
UNSURE_ENTROPY = -sum(p * math.log(p) for p in _P)


def run(input_path, output_dir, reports=None, **overrides):
    options = dict(
        model_path=Path("unused-in-tests.pth"), job_id="JOB-TEST", tile_size=256, overlap=32,
        chunk=256, sieve_min_pixels=8, model_loader=rasters.loader,
    )
    options.update(overrides)
    if reports is not None:
        options["report"] = lambda key, status, fraction=None, detail=None: reports.append((key, status, fraction, detail))
    return pipeline.run_pipeline(input_path, output_dir, **options)


@unittest.skipUnless(HAVE_STACK, f"AI / raster stack not installed: {MISSING}")
class PipelineEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.input = rasters.write_orthoimage(root / "ortho.tif")
        cls.out = root / "out"
        cls.reports = []
        cls.summary = run(cls.input, cls.out, cls.reports)
        cls.features = json.loads((cls.out / "ai_features.geojson").read_text())
        cls.parcels = json.loads((cls.out / "candidate_parcels.geojson").read_text())
        cls.by_id = {f["properties"]["feature_id"]: f["properties"] for f in cls.features["features"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # ------------------------------------------------------------ stages
    def test_every_stage_reports_in_order(self):
        done = [key for key, status, _, _ in self.reports if status == "done"]
        self.assertEqual(done, [key for key, _ in pipeline.STAGES[1:]])
        fractions = [f for key, status, f, _ in self.reports if key == "RUN_SEGMENTATION" and status == "running"]
        self.assertEqual(fractions, sorted(fractions))
        self.assertEqual(fractions[-1], 1.0)

    def test_tiling_and_nodata(self):
        segmentation = self.summary["segmentation"]
        self.assertEqual(segmentation["tiles"], 9)            # 3 x 3 tiles of 256 px
        self.assertEqual(segmentation["skipped_tiles"], 3)    # bottom row is all NoData
        self.assertEqual(segmentation["total_pixels"], rasters.WIDTH * rasters.HEIGHT)
        self.assertEqual(segmentation["valid_pixels"], rasters.WIDTH * 500)
        self.assertIn("alpha", segmentation["nodata_rule"])

    def test_class_pixel_counts_are_exact(self):
        pixels = {name: entry["pixels"] for name, entry in self.summary["segmentation"]["class_pixels"].items()}
        self.assertEqual(pixels["Building"], 100 * 80)
        self.assertEqual(pixels["Field"], 400 * 300 - 100 * 80 + 100 * 60)
        self.assertEqual(pixels["Road"], 700 * 60)
        self.assertEqual(pixels["Water"], 100 * 100)
        self.assertEqual(pixels["Other"], 0)
        self.assertEqual(sum(pixels.values()), rasters.WIDTH * 500)

    # ------------------------------------------------------------ rasters
    def test_prediction_confidence_and_entropy_rasters(self):
        with rasterio.open(self.input) as src:
            source_crs, source_transform = src.crs, src.transform
        with rasterio.open(self.out / "prediction.tif") as pred:
            self.assertEqual((pred.width, pred.height), (rasters.WIDTH, rasters.HEIGHT))
            self.assertEqual(pred.crs, source_crs)
            self.assertEqual(pred.transform, source_transform)
            self.assertEqual(pred.nodata, 255)
            classes = pred.read(1)
        self.assertEqual(classes[140, 150], 2)   # building
        self.assertEqual(classes[30, 30], 1)     # field
        self.assertEqual(classes[390, 350], 3)   # road
        self.assertEqual(classes[100, 550], 4)   # water
        self.assertEqual(classes[10, 10], 0)     # background
        self.assertTrue((classes[500:, :] == 255).all())
        # Tile borders (multiples of 256) leave no seam in the field.
        self.assertTrue((classes[250:262, 250:262] == 1).all())

        with rasterio.open(self.out / "confidence.tif") as conf:
            confidence = conf.read(1)
            self.assertEqual(conf.nodata, -1.0)
        with rasterio.open(self.out / "entropy.tif") as ent:
            entropy = ent.read(1)
        self.assertAlmostEqual(float(confidence[30, 30]), CONFIDENT, places=4)
        self.assertAlmostEqual(float(confidence[200, 550]), UNSURE_CONFIDENCE, places=4)
        self.assertAlmostEqual(float(entropy[200, 550]), UNSURE_ENTROPY, places=3)
        self.assertLess(float(entropy[30, 30]), 0.05)
        self.assertTrue((confidence[500:, :] == -1.0).all())
        self.assertTrue((entropy[500:, :] == -1.0).all())

    # ----------------------------------------------------------- features
    def test_one_feature_per_region_despite_tile_and_chunk_borders(self):
        self.assertEqual(self.summary["feature_count"], 5)
        self.assertEqual(
            sorted(self.by_id), ["BLD-000001", "FLD-000001", "FLD-000002", "RD-000001", "WTR-000001"]
        )
        qa = json.loads((self.out / "qa_report.json").read_text())
        self.assertGreaterEqual(qa["polygonize"]["seam_merges"], 2)  # field and road cross chunk borders
        self.assertGreater(qa["polygonize"]["regions_before_seam_merge"], 5)

    def test_measurements_are_metric_and_exact(self):
        field = self.by_id["FLD-000001"]
        self.assertAlmostEqual(field["area_m2"], 20 * 15 - 5 * 4, delta=0.01)
        self.assertAlmostEqual(field["perimeter_m"], 2 * (20 + 15) + 2 * (5 + 4), delta=0.01)
        self.assertAlmostEqual(field["width_m"], 20.0, delta=0.01)
        self.assertAlmostEqual(field["length_m"], 15.0, delta=0.01)
        self.assertEqual(field["hole_count"], 1)
        self.assertEqual(field["pixel_count"], 400 * 300 - 100 * 80)
        self.assertEqual(field["metric_crs"], "EPSG:32643")

        self.assertAlmostEqual(self.by_id["BLD-000001"]["area_m2"], 20.0, delta=0.01)
        self.assertAlmostEqual(self.by_id["RD-000001"]["area_m2"], 105.0, delta=0.01)
        self.assertAlmostEqual(self.by_id["WTR-000001"]["area_m2"], 25.0, delta=0.01)
        self.assertAlmostEqual(self.by_id["FLD-000002"]["area_m2"], 15.0, delta=0.01)

    def test_every_feature_carries_the_required_attributes(self):
        required = {
            "feature_id", "class_id", "class_name", "area_m2", "perimeter_m", "confidence", "entropy",
            "review_priority", "source_dataset", "processing_job_id", "generated_at",
            "geometry_status", "verification_status", "label",
        }
        for properties in self.by_id.values():
            self.assertTrue(required <= set(properties), required - set(properties))
            self.assertEqual(properties["processing_job_id"], "JOB-TEST")
            self.assertEqual(properties["source_dataset"], "ortho.tif")
            self.assertEqual(properties["geometry_status"], "VALID")
            self.assertEqual(properties["label"], "AI GENERATED / PRELIMINARY")
            self.assertIn(properties["verification_status"], ("AI_GENERATED", "REVIEW_REQUIRED"))

    def test_uncertainty_drives_review_priority(self):
        confident = self.by_id["BLD-000001"]
        self.assertAlmostEqual(confident["confidence"], CONFIDENT, places=4)
        self.assertEqual(confident["review_priority"], "Low")
        self.assertEqual(confident["verification_status"], "AI_GENERATED")

        unsure = self.by_id["FLD-000002"]
        self.assertAlmostEqual(unsure["confidence"], UNSURE_CONFIDENCE, places=4)
        self.assertAlmostEqual(unsure["entropy"], UNSURE_ENTROPY, places=3)
        self.assertEqual(unsure["review_priority"], "High")
        self.assertEqual(unsure["verification_status"], "REVIEW_REQUIRED")
        self.assertIn("LOW_CONFIDENCE", unsure["qa_flags"])
        self.assertEqual(self.summary["priority_counts"], {"High": 1, "Medium": 0, "Low": 4})

    def test_output_is_lonlat_geojson_labelled_preliminary(self):
        self.assertEqual(self.features["type"], "FeatureCollection")
        self.assertIn("CRS84", self.features["crs"]["properties"]["name"])
        metadata = self.features["metadata"]
        self.assertEqual(metadata["status"], "AI GENERATED / PRELIMINARY")
        self.assertIn("not legal cadastral ownership records", metadata["disclaimer"])
        self.assertEqual(metadata["source_crs"], "EPSG:32643")
        lon, lat = self.features["features"][0]["geometry"]["coordinates"][0][0]
        self.assertAlmostEqual(lon, 77.625, delta=0.01)
        self.assertAlmostEqual(lat, 28.559, delta=0.01)

    def test_candidate_parcels(self):
        self.assertEqual(self.summary["candidate_parcel_count"], 2)
        parcels = {f["properties"]["parcel_id"]: f["properties"] for f in self.parcels["features"]}
        self.assertEqual(sorted(parcels), ["CAND-000001", "CAND-000002"])

        large = parcels["CAND-000001"]
        self.assertEqual(large["source_feature_id"], "FLD-000001")
        self.assertAlmostEqual(large["nearest_road_distance_m"], 2.0, delta=0.01)
        self.assertTrue(large["road_access_candidate"])
        self.assertEqual(large["boundary_status"], "Unverified candidate")
        self.assertEqual(large["label"], "CANDIDATE PARCEL / AI GENERATED / PRELIMINARY")

        small = parcels["CAND-000002"]
        self.assertAlmostEqual(small["nearest_road_distance_m"], 6.0, delta=0.01)
        self.assertFalse(small["road_access_candidate"])
        self.assertIn("CANDIDATE PARCEL", self.parcels["metadata"]["status"])

    def test_outputs_open_in_the_application_layer_index(self):
        from backend.gis.layers import LayerStore

        with tempfile.TemporaryDirectory() as folder:
            layers = LayerStore(Path(folder) / "layers.db")
            record = layers.ensure("job:JOB-TEST", "landcover", self.out / "ai_features.geojson")
            self.assertEqual((record["feature_count"], record["skipped_count"]), (5, 0))
            layers.ensure("job:JOB-TEST", "parcels", self.out / "candidate_parcels.geojson")
            unsure = layers.get("job:JOB-TEST", "FLD-000002")["properties"]
            self.assertEqual(unsure["review_priority"], "High")
            stats = layers.stats("job:JOB-TEST", "landcover")
            self.assertEqual(stats["totals"]["with_confidence"], 5)
            self.assertEqual(layers.get("job:JOB-TEST", "CAND-000001")["properties"]["layer"], "parcels")

    def test_run_summary_is_written(self):
        saved = json.loads((self.out / "run_summary.json").read_text())
        self.assertEqual(saved["feature_count"], 5)
        self.assertEqual(saved["status"], "AI GENERATED / PRELIMINARY")
        self.assertNotIn("crs_wkt", saved["raster"])
        for name in ("prediction.tif", "confidence.tif", "entropy.tif", "topology_report.json"):
            self.assertTrue((self.out / name).exists(), name)


@unittest.skipUnless(HAVE_STACK, f"AI / raster stack not installed: {MISSING}")
class PipelineInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_black_pixels_are_nodata_when_nothing_else_is_declared(self):
        path = rasters.write_orthoimage(self.dir / "rgb.tif", with_alpha=False)
        summary = run(path, self.dir / "out")
        self.assertEqual(summary["segmentation"]["valid_pixels"], rasters.WIDTH * 500)
        self.assertIn("black", summary["segmentation"]["nodata_rule"])
        self.assertTrue(any("pure black" in warning for warning in summary["warnings"]))
        self.assertEqual(summary["feature_count"], 5)

    def test_untiled_run_gives_the_same_features(self):
        path = rasters.write_orthoimage(self.dir / "ortho.tif")
        summary = run(path, self.dir / "out", tile_size=1024, overlap=0, chunk=4096)
        self.assertEqual(summary["segmentation"]["tiles"], 1)
        self.assertEqual(summary["feature_count"], 5)
        self.assertAlmostEqual(summary["classes"]["Field"]["area_m2"], 280.0 + 15.0, delta=0.02)

    def test_raster_without_crs_is_refused(self):
        path = rasters.write_orthoimage(self.dir / "nocrs.tif", crs=None)
        with self.assertRaises(PipelineError) as caught:
            run(path, self.dir / "out")
        self.assertIn("coordinate reference system", str(caught.exception))

    def test_missing_input(self):
        with self.assertRaises(PipelineError):
            run(self.dir / "absent.tif", self.dir / "out")

    def test_file_that_is_not_a_raster(self):
        path = self.dir / "junk.tif"
        path.write_bytes(b"II*\x00 not a real raster")
        with self.assertRaises(PipelineError):
            run(path, self.dir / "out")

    def test_all_nodata_raster(self):
        from rasterio.transform import from_origin

        path = self.dir / "empty.tif"
        west, north = rasters.origin()
        with rasterio.open(
            path, "w", driver="GTiff", width=128, height=128, count=3, dtype="uint8",
            crs="EPSG:32643", transform=from_origin(west, north, 0.05, 0.05),
        ) as dst:
            dst.write(np.zeros((3, 128, 128), dtype=np.uint8))
        with self.assertRaises(PipelineError) as caught:
            run(path, self.dir / "out")
        self.assertIn("no valid pixels", str(caught.exception))

    def test_background_only_image_produces_no_invented_features(self):
        from rasterio.transform import from_origin

        path = self.dir / "white.tif"
        west, north = rasters.origin()
        with rasterio.open(
            path, "w", driver="GTiff", width=128, height=128, count=3, dtype="uint8",
            crs="EPSG:32643", transform=from_origin(west, north, 0.05, 0.05),
        ) as dst:
            dst.write(np.full((3, 128, 128), 255, dtype=np.uint8))
        with self.assertRaises(PipelineError) as caught:
            run(path, self.dir / "out")
        self.assertIn("only background", str(caught.exception))

    def test_inspection_reports_georeferencing(self):
        from backend.ai.raster import inspect_raster, validate_for_inference

        meta = inspect_raster(rasters.write_orthoimage(self.dir / "ortho.tif"))
        self.assertEqual((meta["width"], meta["height"], meta["band_count"]), (700, 600, 4))
        self.assertEqual(meta["crs"], "EPSG:32643")
        self.assertEqual(meta["rgb_bands"], [1, 2, 3])
        self.assertEqual(meta["alpha_band"], 4)
        self.assertTrue(meta["is_north_up"])
        np.testing.assert_allclose(meta["resolution_m"], [0.05, 0.05], atol=1e-6)
        west, south, east, north = meta["bounds_lonlat"]
        self.assertTrue(77.62 < west < east < 77.63)
        self.assertTrue(28.55 < south < north < 28.57)
        self.assertEqual(validate_for_inference(meta)["errors"], [])


@unittest.skipUnless(HAVE_STACK, f"AI / raster stack not installed: {MISSING}")
class PolygonizeAndTopologyTests(unittest.TestCase):
    def test_chunk_regions_with_statistics(self):
        from backend.ai.polygonize import polygonize_chunk

        prediction = np.zeros((8, 8), dtype=np.uint8)
        prediction[1:5, 1:5] = 2      # 16 px building
        prediction[6:8, 0:8] = 3      # 16 px road
        prediction[0, 7] = 255        # NoData
        confidence = np.full((8, 8), 0.5, dtype=np.float32)
        confidence[1:5, 1:5] = 0.9
        entropy = np.full((8, 8), 0.2, dtype=np.float32)

        regions = polygonize_chunk(prediction, confidence, entropy, classes={1, 2, 3, 4, 5})
        by_class = {region.class_id: region for region in regions}
        self.assertEqual(set(by_class), {2, 3})
        self.assertEqual(by_class[2].pixels, 16)
        self.assertAlmostEqual(by_class[2].geometry.area, 16.0)
        self.assertAlmostEqual(by_class[2].mean_confidence, 0.9, places=5)
        self.assertAlmostEqual(by_class[3].mean_confidence, 0.5, places=5)
        self.assertAlmostEqual(by_class[3].mean_entropy, 0.2, places=5)
        self.assertEqual(by_class[2].geometry.bounds, (1.0, 1.0, 5.0, 5.0))

    def test_regions_cut_by_a_chunk_border_are_stitched(self):
        from backend.ai.polygonize import merge_seam_regions, polygonize_chunk

        full = np.zeros((8, 16), dtype=np.uint8)
        full[2:6, 4:12] = 1           # one 32 px field across the border at column 8
        full[0:2, 14:16] = 1          # a separate field entirely in the right chunk
        ones = np.ones((8, 8), dtype=np.float32)
        left = polygonize_chunk(full[:, :8], ones, ones, classes={1}, col_off=0,
                                seam_sides=(False, False, True, False))
        right = polygonize_chunk(full[:, 8:], ones, ones, classes={1}, col_off=8,
                                 seam_sides=(True, False, False, False))
        self.assertEqual(len(left) + len(right), 3)

        merged = merge_seam_regions(left + right)
        self.assertEqual(len(merged), 2)
        big = max(merged, key=lambda region: region.pixels)
        self.assertEqual(big.pixels, 32)
        self.assertEqual(big.merged_from, 2)
        self.assertEqual(big.geometry.geom_type, "Polygon")
        self.assertAlmostEqual(big.geometry.area, 32.0)

    def test_regions_touching_only_at_a_corner_stay_separate(self):
        from backend.ai.polygonize import merge_seam_regions, polygonize_chunk

        full = np.zeros((8, 16), dtype=np.uint8)
        full[0:4, 4:8] = 1
        full[4:8, 8:12] = 1           # diagonal neighbour across the border
        ones = np.ones((8, 8), dtype=np.float32)
        left = polygonize_chunk(full[:, :8], ones, ones, classes={1}, seam_sides=(False, False, True, False))
        right = polygonize_chunk(full[:, 8:], ones, ones, classes={1}, col_off=8, seam_sides=(True, False, False, False))
        self.assertEqual(len(merge_seam_regions(left + right)), 2)

    def test_geometry_repair_statuses(self):
        from shapely.geometry import Polygon

        from backend.ai.topology import repair_geometry

        square = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)])
        self.assertEqual(repair_geometry(square)[1], "VALID")

        bow_tie = Polygon([(0, 0), (4, 4), (4, 0), (0, 4)])
        self.assertFalse(bow_tie.is_valid)
        repaired, status = repair_geometry(bow_tie)
        self.assertEqual(status, "REPAIRED")
        self.assertTrue(repaired.is_valid)
        self.assertIn(repaired.geom_type, ("Polygon", "MultiPolygon"))
        self.assertAlmostEqual(repaired.area, 8.0)

        self.assertEqual(repair_geometry(Polygon())[1], "EMPTY")
        self.assertEqual(repair_geometry(None)[1], "EMPTY")
        line_like = Polygon([(0, 0), (4, 0), (8, 0)])
        self.assertEqual(repair_geometry(line_like)[1], "EMPTY")

    def test_topology_report_counts_everything(self):
        import geopandas as gpd
        from shapely.geometry import Polygon, box

        from backend.ai.topology import run_topology_validation

        frame = gpd.GeoDataFrame(
            {"feature_id": ["A", "B", "C", "D", "E"], "class_name": ["Field"] * 5},
            geometry=[
                box(0, 0, 10, 10),
                box(8, 8, 20, 20),                                  # overlaps A by 4 m2
                Polygon([(30, 0), (34, 4), (34, 0), (30, 4)]),      # bow-tie
                box(50, 0, 50.5, 0.5),                              # 0.25 m2 sliver
                Polygon([(60, 0), (64, 0), (68, 0)]),               # no area
            ],
            crs="EPSG:32643",
        )
        checked, report = run_topology_validation(frame, sliver_area_threshold=1.0)
        self.assertEqual(report["total_features"], 4)
        self.assertEqual(report["repaired"], 1)
        self.assertEqual(report["empty_geometries"], 1)
        self.assertEqual(report["removed"][0]["feature"], "E")
        self.assertEqual(report["overlap_pairs"], 1)
        self.assertEqual(report["features_with_overlap"], 2)
        self.assertEqual(report["sliver_features"], 1)

        status = dict(zip(checked["feature_id"], checked["geometry_status"]))
        self.assertEqual(status, {"A": "VALID", "B": "VALID", "C": "REPAIRED", "D": "VALID"})
        overlap = dict(zip(checked["feature_id"], checked["has_overlap"]))
        self.assertEqual(overlap, {"A": True, "B": True, "C": False, "D": False})
        self.assertTrue(checked.geometry.is_valid.all())


if __name__ == "__main__":
    unittest.main()
