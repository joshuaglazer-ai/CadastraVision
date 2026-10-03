"""Tile planning, preprocessing and the confidence / entropy arithmetic.

These parts of the AI pipeline are plain NumPy and run without PyTorch.
"""

import math
import unittest

import numpy as np

from backend.ai import inference
from backend.ai.pipeline import plan_tiles
from backend.ai.raster import validate_for_inference


class TilePlanTests(unittest.TestCase):
    def test_cores_cover_every_pixel_exactly_once(self):
        for width, height in ((512, 512), (1100, 600), (2049, 1025), (64, 64), (513, 31)):
            cover = np.zeros((height, width), dtype=np.int32)
            for (col, row, core_w, core_h), _read in plan_tiles(width, height, 512, 64):
                cover[row : row + core_h, col : col + core_w] += 1
            self.assertTrue((cover == 1).all(), f"{width}x{height}")

    def test_read_window_adds_context_inside_the_raster(self):
        for core, read in plan_tiles(1100, 600, 512, 64):
            col, row, core_w, core_h = core
            read_col, read_row, read_w, read_h = read
            self.assertLessEqual(read_col, col)
            self.assertLessEqual(read_row, row)
            self.assertGreaterEqual(read_col + read_w, col + core_w)
            self.assertGreaterEqual(read_row + read_h, row + core_h)
            self.assertGreaterEqual(read_col, 0)
            self.assertGreaterEqual(read_row, 0)
            self.assertLessEqual(read_col + read_w, 1100)
            self.assertLessEqual(read_row + read_h, 600)
            self.assertLessEqual(col - read_col, 32)

    def test_interior_tile_has_the_full_margin(self):
        tiles = plan_tiles(2048, 2048, 512, 64)
        self.assertEqual(len(tiles), 16)
        core, read = tiles[5]
        self.assertEqual(core, (512, 512, 512, 512))
        self.assertEqual(read, (480, 480, 576, 576))

    def test_tile_size_is_validated(self):
        with self.assertRaises(ValueError):
            plan_tiles(100, 100, 16, 0)


class PreprocessTests(unittest.TestCase):
    def test_uint8_is_scaled_to_unit_range_and_channels_first(self):
        image = np.zeros((4, 6, 3), dtype=np.uint8)
        image[..., 0] = 255
        image[..., 1] = 51
        out = inference.preprocess(image)
        self.assertEqual(out.shape, (3, 4, 6))
        self.assertEqual(out.dtype, np.float32)
        np.testing.assert_allclose(out[0], 1.0)
        np.testing.assert_allclose(out[1], 0.2, atol=1e-6)
        np.testing.assert_allclose(out[2], 0.0)

    def test_imagenet_normalisation_is_selectable(self):
        image = np.full((2, 2, 3), 255, dtype=np.uint8)
        out = inference.preprocess(image, "imagenet")
        expected = (1.0 - inference.IMAGENET_MEAN) / inference.IMAGENET_STD
        np.testing.assert_allclose(out[:, 0, 0], expected, rtol=1e-5)

    def test_wrong_shapes_and_modes_are_rejected(self):
        with self.assertRaises(ValueError):
            inference.preprocess(np.zeros((4, 4), dtype=np.uint8))
        with self.assertRaises(ValueError):
            inference.preprocess(np.zeros((4, 4, 4), dtype=np.uint8))
        with self.assertRaises(ValueError):
            inference.preprocess(np.zeros((4, 4, 3), dtype=np.uint8), "unknown")

    def test_padding_to_the_model_stride(self):
        array = np.arange(3 * 70 * 100, dtype=np.float32).reshape(3, 70, 100)
        padded, height, width = inference.pad_to_stride(array)
        self.assertEqual((height, width), (70, 100))
        self.assertEqual(padded.shape, (3, 96, 128))
        np.testing.assert_array_equal(padded[:, :70, :100], array)

        exact = np.zeros((3, 64, 96), dtype=np.float32)
        self.assertIs(inference.pad_to_stride(exact)[0], exact)

        sliver = np.ones((3, 5, 3), dtype=np.float32)  # pad is larger than the tile
        self.assertEqual(inference.pad_to_stride(sliver)[0].shape, (3, 32, 32))


class UncertaintyArithmeticTests(unittest.TestCase):
    def test_class_confidence_and_entropy(self):
        probabilities = np.zeros((6, 1, 3), dtype=np.float32)
        probabilities[2, 0, 0] = 1.0                 # certain: Building
        probabilities[:, 0, 1] = 1.0 / 6.0           # no information
        probabilities[1, 0, 2] = 0.5                 # split between Field and Road
        probabilities[3, 0, 2] = 0.5

        prediction, confidence, entropy = inference.summarise_probabilities(probabilities)
        self.assertEqual(prediction.dtype, np.uint8)
        self.assertEqual(prediction[0, 0], 2)
        self.assertAlmostEqual(float(confidence[0, 0]), 1.0, places=6)
        self.assertAlmostEqual(float(entropy[0, 0]), 0.0, places=5)

        self.assertAlmostEqual(float(confidence[0, 1]), 1.0 / 6.0, places=6)
        self.assertAlmostEqual(float(entropy[0, 1]), math.log(6), places=4)

        self.assertEqual(prediction[0, 2], 1)
        self.assertAlmostEqual(float(confidence[0, 2]), 0.5, places=6)
        self.assertAlmostEqual(float(entropy[0, 2]), math.log(2), places=4)

    def test_entropy_is_never_negative(self):
        probabilities = np.zeros((6, 8, 8), dtype=np.float32)
        probabilities[4] = 1.0
        _, _, entropy = inference.summarise_probabilities(probabilities)
        self.assertGreaterEqual(float(entropy.min()), 0.0)


def raster_meta(**overrides):
    meta = {
        "crs": "EPSG:32643", "transform": [0.05, 0.0, 756800.0, 0.0, -0.05, 3162000.0],
        "is_north_up": True, "band_count": 3, "rgb_bands": [1, 2, 3],
        "dtypes": ["uint8", "uint8", "uint8"], "width": 4096, "height": 4096,
        "resolution_m": [0.05, 0.05], "nodata": 0, "alpha_band": None, "crs_is_geographic": False,
    }
    meta.update(overrides)
    return meta


class RasterValidationTests(unittest.TestCase):
    def test_compatible_orthoimage_passes(self):
        checks = validate_for_inference(raster_meta())
        self.assertEqual(checks, {"errors": [], "warnings": []})

    def test_missing_crs(self):
        errors = validate_for_inference(raster_meta(crs=None))["errors"]
        self.assertTrue(any("coordinate reference system" in error for error in errors))

    def test_pixel_coordinates_only(self):
        errors = validate_for_inference(raster_meta(transform=[1.0, 0.0, 0.0, 0.0, 1.0, 0.0]))["errors"]
        self.assertTrue(any("georeferencing" in error for error in errors))

    def test_rotated_raster(self):
        errors = validate_for_inference(raster_meta(is_north_up=False))["errors"]
        self.assertTrue(any("rotated" in error for error in errors))

    def test_too_few_bands(self):
        errors = validate_for_inference(raster_meta(band_count=1, rgb_bands=None, dtypes=["uint8"]))["errors"]
        self.assertTrue(any("RGB imagery is required" in error for error in errors))

    def test_sixteen_bit_imagery(self):
        errors = validate_for_inference(raster_meta(dtypes=["uint16"] * 3))["errors"]
        self.assertTrue(any("8-bit RGB" in error for error in errors))

    def test_tiny_raster(self):
        errors = validate_for_inference(raster_meta(width=32, height=32))["errors"]
        self.assertTrue(any("too small" in error for error in errors))

    def test_coarse_imagery_is_a_warning_about_generalisation_not_an_error(self):
        checks = validate_for_inference(raster_meta(resolution_m=[10.0, 10.0]))
        self.assertEqual(checks["errors"], [])
        self.assertTrue(any("likely to degrade" in warning for warning in checks["warnings"]))

    def test_undeclared_nodata_and_geographic_crs_are_explained(self):
        warnings = validate_for_inference(
            raster_meta(nodata=None, crs="EPSG:4326", crs_is_geographic=True)
        )["warnings"]
        self.assertTrue(any("pure black" in warning for warning in warnings))
        self.assertTrue(any("geographic CRS" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
