"""True ground measurement for Web Mercator rasters, the derived sieve, and
the GeoPackage export metadata.

Web Mercator (EPSG:3857) has metre units but scales every length by
1 / cos(latitude). At Uplarshi (about 28.56 N) a raw EPSG:3857 area is about
1.30 x the ground area. Everything the application measures must be the
ground figure.
"""

import json
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from backend.tests import rasters, support

try:
    import geopandas as gpd
    import rasterio
    from pyproj import Transformer
    from shapely.geometry import Polygon

    from backend.ai import pipeline
    from backend.ai.polygonize import polygonize_rasters
    from backend.ai.topology import run_topology_validation
    from backend.gis.metric import choose_metric_crs, crs_label, is_mercator, pixel_ground_size

    HAVE_STACK = True
    MISSING = ""
except Exception as exc:  # ImportError or a broken install
    HAVE_STACK = False
    MISSING = str(exc)

LAT = 28.56
SCALE = 1.0 / math.cos(math.radians(LAT))  # Web Mercator scale at Uplarshi


def ground_square_in_3857(side_m: float):
    """A square of known ground size laid out in UTM 43N at Uplarshi,
    expressed in EPSG:3857."""

    e0, n0 = support.origin_utm()
    ring = [(e0, n0), (e0 + side_m, n0), (e0 + side_m, n0 + side_m), (e0, n0 + side_m)]
    to_3857 = Transformer.from_crs("EPSG:32643", "EPSG:3857", always_xy=True)
    return Polygon([to_3857.transform(x, y) for x, y in ring])


@unittest.skipUnless(HAVE_STACK, f"GIS stack not installed: {MISSING}")
class WebMercatorMeasurementTests(unittest.TestCase):
    def test_a_known_square_in_3857_measures_its_true_size(self):
        square = ground_square_in_3857(100.0)
        frame = gpd.GeoDataFrame(geometry=[square], crs="EPSG:3857")
        # The trap: measured in its own CRS the square is about 1.30 x too big.
        self.assertAlmostEqual(frame.geometry.area.iloc[0] / 10000.0, SCALE**2, delta=0.01)

        metric = choose_metric_crs(frame.crs, tuple(frame.total_bounds))
        self.assertEqual(crs_label(metric), "EPSG:32643")
        measured = frame.to_crs(metric)
        self.assertAlmostEqual(measured.geometry.area.iloc[0], 10000.0, delta=10000.0 * 0.005)
        self.assertAlmostEqual(measured.geometry.length.iloc[0], 400.0, delta=400.0 * 0.005)

    def test_mercator_detection(self):
        self.assertTrue(is_mercator("EPSG:3857"))
        self.assertTrue(is_mercator("EPSG:3395"))
        self.assertFalse(is_mercator("EPSG:32643"))  # Transverse Mercator
        self.assertFalse(is_mercator("EPSG:4326"))
        # A metric CRS that is not Mercator is still used as is.
        self.assertEqual(crs_label(choose_metric_crs("EPSG:32643", (0, 0, 1, 1))), "EPSG:32643")

    def test_pixel_ground_size_of_a_3857_raster(self):
        from rasterio.transform import from_origin

        to_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        x, y = to_3857.transform(77.625, LAT)
        size = pixel_ground_size("EPSG:3857", from_origin(x, y, 0.03, 0.03), 1000, 1000)
        expected = 0.03 / SCALE
        self.assertAlmostEqual(size["x_m"], expected, delta=expected * 0.005)
        self.assertAlmostEqual(size["area_m2"], expected**2, delta=expected**2 * 0.01)
        self.assertEqual(size["metric_crs"], "EPSG:32643")


def write_orthoimage_3857(path: Path) -> Path:
    """The synthetic scene with the same ground size, georeferenced in EPSG:3857."""

    from rasterio.enums import ColorInterp
    from rasterio.transform import from_origin

    rgb, alpha = rasters.paint()
    west, north = rasters.origin()
    to_3857 = Transformer.from_crs("EPSG:32643", "EPSG:3857", always_xy=True)
    x, y = to_3857.transform(west, north)
    lon, lat = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True).transform(west, north)
    pixel = rasters.PIXEL / math.cos(math.radians(lat))
    profile = dict(
        driver="GTiff", width=rasters.WIDTH, height=rasters.HEIGHT, count=4, dtype="uint8",
        transform=from_origin(x, y, pixel, pixel), photometric="RGB", alpha="YES", crs="EPSG:3857",
    )
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(rgb, [1, 2, 3])
        dst.write(alpha, 4)
        dst.colorinterp = [ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha]
    return path


@unittest.skipUnless(HAVE_STACK, f"AI / raster stack not installed: {MISSING}")
class PipelineOnWebMercatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.summary = pipeline.run_pipeline(
            write_orthoimage_3857(root / "ortho_3857.tif"), root / "out",
            model_path=Path("unused-in-tests.pth"), job_id="JOB-3857", tile_size=256, overlap=32,
            chunk=256, model_loader=rasters.loader,
        )
        cls.features = json.loads((root / "out" / "ai_features.geojson").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_class_areas_are_ground_areas(self):
        self.assertEqual(self.summary["metric_crs"], "EPSG:32643")
        classes = self.summary["classes"]
        # Field: 280 m2 of green plus the 15 m2 ambiguous patch the stand-in
        # model also calls Field (as the same scene in UTM measures).
        for name, expected in (("Field", 295.0), ("Building", 20.0), ("Road", 105.0), ("Water", 25.0)):
            with self.subTest(name=name):
                self.assertAlmostEqual(classes[name]["area_m2"], expected, delta=expected * 0.005)
        pixels = self.summary["segmentation"]["class_pixels"]["Field"]["approx_area_m2"]
        self.assertAlmostEqual(pixels, 295.0, delta=295.0 * 0.005)

    def test_every_feature_area_is_the_ground_area(self):
        from backend.gis.geometry import analyse_geometry

        for feature in self.features["features"]:
            measured = analyse_geometry(feature["geometry"])["metrics"]["area_m2"]
            self.assertAlmostEqual(feature["properties"]["area_m2"], measured, delta=max(measured * 0.005, 1e-6))

    def test_the_raster_is_described_honestly(self):
        raster = self.summary["raster"]
        self.assertTrue(raster["crs_is_mercator"])
        self.assertAlmostEqual(raster["resolution_m"][0], rasters.PIXEL, delta=rasters.PIXEL * 0.005)
        self.assertTrue(any("Mercator" in w for w in self.summary["warnings"]))

    def test_reprojection_noise_is_not_reported_as_overlap(self):
        # Neighbouring regions share edges; reprojected from EPSG:3857 to UTM
        # they may differ by floating-point noise, which is not an overlap.
        self.assertEqual(self.summary["topology"]["overlap_pairs"], 0)

    def test_sieve_is_derived_from_the_minimum_mapping_unit(self):
        sieve = self.summary["sieve"]
        # 1 m2 / (0.05 m)^2 = 400 pixels.
        self.assertAlmostEqual(sieve["min_pixels"], 400, delta=4)
        self.assertIn("minimum mapping unit", sieve["rule"])
        self.assertIn("POLYGONIZE", self.summary["stage_seconds"])


@unittest.skipUnless(HAVE_STACK, f"AI / raster stack not installed: {MISSING}")
class SieveTests(unittest.TestCase):
    def test_threshold_rules(self):
        self.assertEqual(pipeline.sieve_threshold(None, 1.0, 0.0025)[0], 400)
        self.assertEqual(pipeline.sieve_threshold(12, 1.0, 0.0025), (12, "SIEVE_MIN_PIXELS override"))
        self.assertEqual(pipeline.sieve_threshold(None, 1.0, None)[0], 8)

    def test_reassigned_pixels_are_counted_per_class(self):
        from rasterio.transform import from_origin

        prediction = np.full((64, 64), 1, dtype=np.uint8)  # all Field
        prediction[10:13, 10:13] = 2                         # a 9-pixel Building speck
        prediction[40:60, 40:60] = 3                         # a 400-pixel Road block
        with tempfile.TemporaryDirectory() as folder:
            paths = {}
            for name, array, dtype in (
                ("prediction", prediction, "uint8"),
                ("confidence", np.full((64, 64), 0.9, dtype="float32"), "float32"),
                ("entropy", np.full((64, 64), 0.1, dtype="float32"), "float32"),
            ):
                paths[name] = Path(folder) / f"{name}.tif"
                with rasterio.open(
                    paths[name], "w", driver="GTiff", width=64, height=64, count=1, dtype=dtype,
                    crs="EPSG:32643", transform=from_origin(500000, 3160000, 0.05, 0.05),
                ) as dst:
                    dst.write(array, 1)
            regions, info = polygonize_rasters(
                paths["prediction"], paths["confidence"], paths["entropy"], chunk=32, sieve_min_pixels=20
            )
        moved = info["sieve_reassigned"]
        self.assertEqual(moved["Building"]["removed_pixels"], 9)
        self.assertEqual(moved["Field"]["gained_pixels"], 9)
        self.assertNotIn("Road", moved)  # 400 px, kept although cut by chunk borders
        self.assertEqual(sorted({r.class_id for r in regions}), [1, 3])
        self.assertEqual(sum(r.pixels for r in regions), 64 * 64)  # nothing disappears


@unittest.skipUnless(HAVE_STACK, f"GIS stack not installed: {MISSING}")
class RepairProgressTests(unittest.TestCase):
    def test_repair_reports_progress(self):
        squares = [Polygon([(i * 10, 0), (i * 10 + 5, 0), (i * 10 + 5, 5), (i * 10, 5)]) for i in range(1200)]
        frame = gpd.GeoDataFrame({"area_m2": [25.0] * 1200}, geometry=squares, crs="EPSG:32643")
        calls = []
        run_topology_validation(frame, area_column="area_m2", progress=lambda f, d: calls.append((f, d)))
        fractions = [f for f, _ in calls]
        self.assertGreaterEqual(len(calls), 3)
        self.assertEqual(fractions, sorted(fractions))
        self.assertTrue(any("geometries" in d for _, d in calls))


if __name__ == "__main__":
    unittest.main()
