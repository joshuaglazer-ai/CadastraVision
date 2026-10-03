"""Review priority comes from measurable signals, not from guesses."""

import math
import unittest

from backend.ai import qa


def assess(**overrides):
    values = dict(layer="landcover", area_m2=200.0, compactness=0.7, rings=1)
    values.update(overrides)
    return qa.assess(**values)


class UncertaintyTests(unittest.TestCase):
    def test_confident_feature_is_low_priority(self):
        result = assess(confidence=0.95, entropy=0.10)
        self.assertEqual(result["priority"], "Low")
        self.assertEqual(result["reasons"], [])

    def test_low_confidence_is_high_priority(self):
        result = assess(confidence=0.55, entropy=0.30)
        self.assertEqual(result["priority"], "High")
        self.assertIn("LOW_CONFIDENCE", result["flags"])

    def test_moderate_confidence_is_medium_priority(self):
        self.assertEqual(assess(confidence=0.70, entropy=0.30)["priority"], "Medium")

    def test_high_entropy_alone_raises_priority(self):
        result = assess(confidence=0.90, entropy=1.30)
        self.assertEqual(result["priority"], "High")
        self.assertIn("HIGH_ENTROPY", result["flags"])

    def test_missing_uncertainty_is_flagged_not_invented(self):
        result = assess()
        self.assertEqual(result["priority"], "Low")
        self.assertIn("UNCERTAINTY_UNAVAILABLE", result["flags"])

    def test_non_finite_values_are_treated_as_missing(self):
        result = assess(confidence=float("nan"), entropy=None)
        self.assertIn("UNCERTAINTY_UNAVAILABLE", result["flags"])

    def test_band_labels(self):
        self.assertFalse(qa.uncertainty_band(None, None)["available"])
        self.assertEqual(qa.uncertainty_band(0.95, 0.1)["level"], "low")
        self.assertEqual(qa.uncertainty_band(0.70, 0.1)["level"], "moderate")
        self.assertEqual(qa.uncertainty_band(0.40, 1.5)["level"], "high")
        band = qa.uncertainty_band(0.5, math.log(6))
        self.assertAlmostEqual(band["normalized_entropy"], 1.0, places=3)


class GeometryAndParcelTests(unittest.TestCase):
    def test_invalid_geometry_is_high_priority(self):
        result = assess(confidence=0.95, entropy=0.1, geometry_status="INVALID",
                        geometry_problems=["Self-intersection"])
        self.assertEqual(result["priority"], "High")
        self.assertIn("INVALID_GEOMETRY", result["flags"])
        self.assertTrue(any("Self-intersection" in reason for reason in result["reasons"]))

    def test_repaired_geometry_and_overlap_are_medium(self):
        self.assertEqual(assess(confidence=0.95, entropy=0.1, geometry_status="REPAIRED")["priority"], "Medium")
        self.assertEqual(assess(confidence=0.95, entropy=0.1, has_overlap=True)["priority"], "Medium")

    def test_fragment_is_flagged(self):
        result = assess(area_m2=0.3, confidence=0.95, entropy=0.1)
        self.assertIn("FRAGMENT", result["flags"])

    def test_many_interior_rings_suggest_merged_features(self):
        self.assertEqual(assess(rings=60, confidence=0.95, entropy=0.1)["priority"], "Medium")
        result = assess(rings=600, confidence=0.95, entropy=0.1)
        self.assertEqual(result["priority"], "High")
        self.assertIn("COMPLEX_GEOMETRY", result["flags"])

    def test_parcel_without_road_access(self):
        result = assess(layer="parcels", road_access=False, confidence=0.95, entropy=0.1)
        self.assertEqual(result["priority"], "Medium")
        self.assertIn("NO_ROAD_ACCESS", result["flags"])

    def test_unknown_road_access_is_not_treated_as_no_access(self):
        result = assess(layer="parcels", road_access=None, confidence=0.95, entropy=0.1)
        self.assertEqual(result["priority"], "Low")

    def test_parcel_below_minimum_size(self):
        result = assess(layer="parcels", area_m2=10.0, road_access=True, confidence=0.95, entropy=0.1)
        self.assertIn("BELOW_PARCEL_MINIMUM", result["flags"])

    def test_default_status(self):
        self.assertEqual(qa.default_status("High"), "REVIEW_REQUIRED")
        self.assertEqual(qa.default_status("Medium"), "REVIEW_REQUIRED")
        self.assertEqual(qa.default_status("Low"), "AI_GENERATED")


if __name__ == "__main__":
    unittest.main()
