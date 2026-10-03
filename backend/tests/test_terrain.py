"""Building height is DSM minus DTM, measured from rasters, or it is absent.

Needs Rasterio, pyproj and Shapely.
"""

import shutil
import unittest

from backend.config import settings
from backend.core import runtime
from backend.services import terrain_service
from backend.tests import rasters

try:
    import pyproj  # noqa: F401
    import rasterio  # noqa: F401
    import shapely  # noqa: F401

    HAVE_STACK = True
    MISSING = ""
except Exception as exc:
    HAVE_STACK = False
    MISSING = str(exc)


class NoElevationDataTests(unittest.TestCase):
    def test_without_rasters_nothing_is_estimated(self):
        state = terrain_service.status(settings)
        self.assertFalse(state["heights_available"])
        self.assertEqual(state["message"], terrain_service.REQUIRED_MESSAGE)
        heights = terrain_service.building_heights(settings, runtime.EXISTING_SOURCE)
        self.assertEqual(heights, {"available": False, "message": terrain_service.REQUIRED_MESSAGE, "buildings": []})
        with self.assertRaises(terrain_service.TerrainError) as caught:
            terrain_service.grid(settings, (77.62, 28.55, 77.63, 28.57), surface="dtm")
        self.assertEqual(caught.exception.status_code, 404)


@unittest.skipUnless(HAVE_STACK, f"raster stack not installed: {MISSING}")
class MeasuredHeightTests(unittest.TestCase):
    """Fixture buildings: BLD-000004 at (5, 5) 10 x 8 m, BLD-000005 at (65, 5) 6 x 6 m."""

    @classmethod
    def setUpClass(cls):
        terrain_service._cache.clear()
        cls.dsm_dir = settings.data_dir / "dsm"
        cls.dtm_dir = settings.data_dir / "dtm"
        # Ground at 200 m everywhere except a 1 m rise under the second building.
        rasters.write_elevation(cls.dtm_dir / "dtm.tif", {(60.0, 0.0, 20.0, 20.0): 201.0})
        rasters.write_elevation(
            cls.dsm_dir / "dsm.tif",
            {
                (60.0, 0.0, 20.0, 20.0): 201.0,
                (5.0, 5.0, 10.0, 8.0): 206.5,      # roof of BLD-000004: 6.5 m above ground
                (65.0, 5.0, 6.0, 6.0): 204.0,      # roof of BLD-000005: 3.0 m above its ground
            },
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dsm_dir, ignore_errors=True)
        shutil.rmtree(cls.dtm_dir, ignore_errors=True)
        terrain_service._cache.clear()

    def test_status(self):
        state = terrain_service.status(settings)
        self.assertTrue(state["heights_available"])
        self.assertIsNone(state["message"])
        self.assertEqual(state["dsm"]["crs"], "EPSG:32643")
        self.assertIsNotNone(state["overlap_extent"])

    def test_heights_are_dsm_minus_dtm(self):
        result = terrain_service.building_heights(settings, runtime.EXISTING_SOURCE)
        self.assertTrue(result["available"])
        self.assertEqual(result["measured"], 2)
        heights = {building["uid"]: building for building in result["buildings"]}

        first = heights["BLD-000004"]
        self.assertAlmostEqual(first["height_m"], 6.5, delta=0.05)
        self.assertAlmostEqual(first["ground_elevation_m"], 200.0, delta=0.01)
        self.assertAlmostEqual(first["roof_elevation_m"], 206.5, delta=0.01)
        self.assertGreater(first["dsm_samples"], 100)

        second = heights["BLD-000005"]
        self.assertAlmostEqual(second["height_m"], 3.0, delta=0.05)
        self.assertAlmostEqual(second["ground_elevation_m"], 201.0, delta=0.01)
        # Height does not follow footprint area: the larger building is not assumed taller.
        self.assertIn("DSM", result["method"])

    def test_elevation_grid_for_the_3d_view(self):
        state = terrain_service.status(settings)
        grid = terrain_service.grid(settings, state["overlap_extent"], surface="dsm", size=64)
        self.assertTrue(grid["measured"])
        self.assertLessEqual(max(grid["rows"], grid["cols"]), 64)
        self.assertEqual(len(grid["values"]), grid["rows"])
        self.assertEqual(len(grid["values"][0]), grid["cols"])
        self.assertAlmostEqual(grid["min"], 200.0, delta=0.01)
        self.assertGreater(grid["max"], 200.5)
        with self.assertRaises(terrain_service.TerrainError):
            terrain_service.grid(settings, (10.0, 10.0, 11.0, 11.0), surface="dsm")


if __name__ == "__main__":
    unittest.main()
