"""Candidate plots by morphological tessellation around detected buildings."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from backend.tests import support

try:
    import rasterio
    from rasterio.transform import from_origin
    from shapely.geometry import box, shape
    from shapely.ops import transform as shp_transform
    from pyproj import Transformer

    from backend.ai import plots
    from backend.ai.plots import PLOT_REVIEW_REASON, build_plots

    HAVE_STACK = True
    MISSING = ""
except ImportError as exc:  # pragma: no cover
    HAVE_STACK = False
    MISSING = str(exc)

PIXEL = 0.05   # m
SIZE = 600     # px: a 30 m x 30 m scene
# Plots are compared after a round trip through GeoJSON longitude/latitude
# (8 decimals, about 1 mm), so shared edges can differ by a millimetre:
# 0.01 m2 is a 1 mm sliver along a 10 m edge. The application itself checks
# overlaps in UTM before writing, with no such rounding.
ROUNDING_M2 = 0.01


def write_job(folder: Path, buildings=(), roads=(), waters=(), nodata=()):
    """A finished job's outputs: a class raster and its features.

    Rectangles are ``(x, y, width, height)`` in metres from the scene's
    south-west corner, laid out in UTM 43N.
    """

    e0, n0 = support.origin_utm()
    prediction = np.ones((SIZE, SIZE), dtype=np.uint8)  # Field everywhere

    def cells(x, y, w, h):
        c0, c1 = int(round(x / PIXEL)), int(round((x + w) / PIXEL))
        r1, r0 = SIZE - int(round(y / PIXEL)), SIZE - int(round((y + h) / PIXEL))
        return slice(max(r0, 0), max(r1, 0)), slice(max(c0, 0), max(c1, 0))

    features = []
    for class_id, name, rects in ((2, "Building", buildings), (3, "Road", roads), (4, "Water", waters)):
        for x, y, w, h in rects:
            prediction[cells(x, y, w, h)] = class_id
            features.append(support.feature(
                support.rect(x, y, w, h), class_id=class_id, class_name=name,
                feature_id=f"{ {2: 'BLD', 3: 'RD', 4: 'WTR'}[class_id]}-{len(features) + 1:06d}",
                confidence=0.8, entropy=0.5,
            ))
    for x, y, w, h in nodata:
        prediction[cells(x, y, w, h)] = 255
    folder.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        folder / "prediction.tif", "w", driver="GTiff", width=SIZE, height=SIZE, count=1, dtype="uint8",
        crs="EPSG:32643", transform=from_origin(e0, n0 + SIZE * PIXEL, PIXEL, PIXEL), nodata=255,
    ) as dst:
        dst.write(prediction, 1)
    support.write_json(folder / "ai_features.geojson", support.collection("ai", features))
    return folder


def plots_of(folder: Path) -> list:
    data = json.loads((folder / "candidate_plots.geojson").read_text(encoding="utf-8"))
    return data["features"]


_TO_UTM = None


def utm(geometry):
    """A lon/lat GeoJSON geometry as a shapely geometry in metres."""

    global _TO_UTM
    if _TO_UTM is None:
        _TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True).transform
    return shp_transform(_TO_UTM, shape(geometry))


def local(x, y, w, h):
    e0, n0 = support.origin_utm()
    return box(e0 + x, n0 + y, e0 + x + w, n0 + y + h)


@unittest.skipUnless(HAVE_STACK, f"GIS stack not installed: {MISSING}")
class TessellationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def build(self, **options):
        options.setdefault("limit_m", 25.0)
        options.setdefault("grid_m", 0.10)
        return build_plots(self.dir, job_id="JOB-T", **options)

    def test_two_buildings_side_by_side_give_two_plots_that_do_not_overlap(self):
        write_job(self.dir, buildings=[(10, 10, 4, 6), (16, 10, 4, 6)])
        summary = self.build(limit_m=5.0)
        found = plots_of(self.dir)
        self.assertEqual(summary["plots"], 2)
        a, b = (utm(f["geometry"]) for f in found)
        self.assertLess(a.intersection(b).area, ROUNDING_M2)
        # Each plot holds its own building, and the land between them is shared out.
        for feature, building in zip(
            sorted(found, key=lambda f: utm(f["geometry"]).centroid.x), (local(10, 10, 4, 6), local(16, 10, 4, 6))
        ):
            geom = utm(feature["geometry"])
            self.assertGreater(geom.intersection(building).area, 0.99 * building.area)
            self.assertEqual(feature["properties"]["buildings_inside_all"], 1)
            self.assertEqual(feature["properties"]["buildings_inside_seed_rule"], 1)
        gap = local(14, 10, 2, 6)
        self.assertGreater((a.union(b)).intersection(gap).area, 0.95 * gap.area)

    def test_a_road_between_buildings_is_not_crossed(self):
        # Building close to the road on the west; the nearest building east of
        # the road is far away. Straight-line nearest would give the land east
        # of the road to the west building; the road must stop it.
        write_job(self.dir, buildings=[(9, 12, 3, 4), (27, 12, 2, 4)], roads=[(13, 0, 1, 30)])
        self.build(limit_m=20.0)
        west = next(f for f in plots_of(self.dir) if utm(f["geometry"]).centroid.x < support.origin_utm()[0] + 13)
        geom = utm(west["geometry"])
        self.assertLessEqual(geom.bounds[2], support.origin_utm()[0] + 13 + 1e-6)

    def test_a_lone_building_is_limited_by_the_distance_setting(self):
        write_job(self.dir, buildings=[(13, 13, 4, 4)])
        self.build(limit_m=3.0)
        (plot,) = plots_of(self.dir)
        geom = utm(plot["geometry"])
        building = local(13, 13, 4, 4)
        self.assertLess(geom.difference(building.buffer(3.0)).area, ROUNDING_M2)
        # It does reach close to the limit in every direction.
        self.assertGreater(geom.area, 0.9 * building.buffer(3.0).area)
        self.assertEqual(plot["properties"]["delineation_limit_m"], 3.0)

    def test_no_buildings_gives_no_plots(self):
        write_job(self.dir, roads=[(0, 10, 30, 2)])
        summary = self.build()
        self.assertEqual(summary["plots"], 0)
        self.assertEqual(plots_of(self.dir), [])

    def test_plots_never_overlap_roads_or_water(self):
        write_job(
            self.dir,
            buildings=[(5, 5, 4, 4), (12, 5, 4, 4), (5, 20, 4, 4), (20, 20, 3, 3)],
            roads=[(0, 12, 30, 1.5), (17.3, 0, 0.7, 30)],
            waters=[(10, 22, 3, 5)],
        )
        self.build(limit_m=10.0)
        barriers = [local(0, 12, 30, 1.5), local(17.3, 0, 0.7, 30), local(10, 22, 3, 5)]
        found = plots_of(self.dir)
        self.assertEqual(len(found), 4)
        for feature in found:
            geom = utm(feature["geometry"])
            for barrier in barriers:
                self.assertLess(geom.intersection(barrier).area, ROUNDING_M2)
        geoms = [utm(f["geometry"]) for f in found]
        for i in range(len(geoms)):
            for j in range(i + 1, len(geoms)):
                self.assertLess(geoms[i].intersection(geoms[j]).area, ROUNDING_M2)

    def test_plots_stay_inside_valid_imagery(self):
        write_job(self.dir, buildings=[(13, 13, 4, 4)], nodata=[(0, 0, 30, 10)])
        self.build(limit_m=10.0)
        (plot,) = plots_of(self.dir)
        self.assertLess(utm(plot["geometry"]).intersection(local(0, 0, 30, 10)).area, 0.5)

    def test_each_plot_is_labelled_and_described(self):
        write_job(self.dir, buildings=[(10, 10, 4, 4), (15, 11, 1.5, 1)], roads=[(0, 6, 30, 1)])
        summary = self.build(limit_m=5.0)
        (plot,) = plots_of(self.dir)  # the 1.5 m2 shed is not a seed (under 5 m2)
        props = plot["properties"]
        self.assertEqual(props["delineation_method"], "morphological_tessellation")
        self.assertEqual(props["review_reason"], PLOT_REVIEW_REASON)
        self.assertEqual(props["label"], "CANDIDATE PLOT / AI GENERATED / PRELIMINARY")
        self.assertEqual(props["class_name"], "Candidate plot")
        self.assertEqual(props["building_feature_id"], "BLD-000001")
        self.assertAlmostEqual(props["building_area_m2"], 17.5, delta=0.2)  # 16 m2 seed + 1.5 m2 shed
        self.assertAlmostEqual(props["coverage_ratio"], props["building_area_m2"] / props["area_m2"], places=3)
        # Counted two ways: the 1.5 m2 shed is a detected building, not one that gets a plot.
        self.assertEqual(props["buildings_inside_all"], 2)
        self.assertEqual(props["buildings_inside_seed_rule"], 1)
        self.assertEqual(summary["one_building_share_seed_rule"], 1.0)
        self.assertEqual(summary["one_building_share_all"], 0.0)
        self.assertEqual(props["building_confidence"], 0.8)
        self.assertTrue(props["road_access_candidate"])
        self.assertLess(props["nearest_road_distance_m"], 5.0)
        for word in ("parcel boundary", "legal"):
            self.assertNotIn(word, json.dumps(props).lower())
        self.assertEqual(summary["seed_buildings"], 1)

    def test_low_building_coverage_is_flagged_never_removed(self):
        from backend.ai import qa
        from backend.gis.layers import LayerStore

        # A 6 m2 building with 10 m of land around it: about 1 % built.
        write_job(self.dir, buildings=[(14, 14, 3, 2)])
        self.build(limit_m=10.0)
        (plot,) = plots_of(self.dir)  # kept
        self.assertLess(plot["properties"]["coverage_ratio"], plots.LOW_COVERAGE)
        store = LayerStore(self.dir / "cache" / "layers.db")
        store.ensure("job:JOB-T", "plots", self.dir / "candidate_plots.geojson")
        props = store.get("job:JOB-T", "PLOT-000001", kind="plots")["properties"]
        self.assertIn(plots.LOW_COVERAGE_REASON, props["review_reasons"])
        self.assertIn("LOW_BUILDING_COVERAGE", props["qa_flags"])
        self.assertEqual(qa.default_status(props["review_priority"]), "REVIEW_REQUIRED")
        # A well-covered plot is not flagged.
        well = qa.assess(layer="plots", area_m2=100.0, coverage_ratio=0.4)
        self.assertNotIn("LOW_BUILDING_COVERAGE", well["flags"])

    def test_layer_index_marks_every_plot_review_required(self):
        from backend.ai import qa
        from backend.gis.layers import LayerStore

        write_job(self.dir, buildings=[(10, 10, 4, 4)])
        self.build(limit_m=5.0)
        store = LayerStore(self.dir / "cache" / "layers.db")
        record = store.ensure("job:JOB-T", "plots", self.dir / "candidate_plots.geojson")
        self.assertEqual(record["feature_count"], 1)
        feature = store.get("job:JOB-T", "PLOT-000001", kind="plots")
        props = feature["properties"]
        self.assertEqual(props["class_key"], "plot")
        self.assertIn(props["review_priority"], ("Medium", "High"))
        self.assertIn(PLOT_REVIEW_REASON, props["review_reasons"])
        self.assertEqual(qa.default_status(props["review_priority"]), "REVIEW_REQUIRED")
        self.assertNotIn("UNCERTAINTY_UNAVAILABLE", props["qa_flags"])


if HAVE_STACK:
    from backend.config import settings
    from backend.core import runtime
    from backend.services import plot_service, processing_service
    from backend.tests.test_processing import ProcessingCase, fake_pipeline

    class PlotsInTheAppTests(ProcessingCase):
        """A completed job's plots: map layer, export, analytics, reference check."""

        def setUp(self):
            super().setUp()
            job_id = self.create_job()["job_id"]
            with mock.patch.object(processing_service.pipeline, "run_pipeline", side_effect=fake_pipeline), \
                    mock.patch.object(settings, "plots_enabled", False):
                self.client.post(f"/api/processing/{job_id}/start")
                self.wait_for(job_id)
            self.job_id = job_id
            out = runtime.job_output_dir(job_id)
            # The fixture's features come from the stand-in pipeline; add a
            # matching class raster (all Field) for the plot step.
            self._raster_for_fixture(out)
            state = plot_service.build_for_job(job_id, settings, self.store)
            self.assertEqual(state["status"], "COMPLETED", state)

        def _raster_for_fixture(self, out: Path):
            e0, n0 = support.origin_utm()
            width, height = 1200, 900  # 0.1 m cells over x -10..110, y -15..75
            west, north = e0 - 10, n0 + 75
            with rasterio.open(
                out / "prediction.tif", "w", driver="GTiff", width=width, height=height, count=1, dtype="uint8",
                crs="EPSG:32643", transform=from_origin(west, north, 0.1, 0.1), nodata=255,
            ) as dst:
                dst.write(np.ones((height, width), dtype=np.uint8), 1)

        def test_plots_are_their_own_layer_export_and_count(self):
            source = f"job:{self.job_id}"
            catalogue = {l["key"]: l for l in self.ok("/api/map/layers", source=source)["layers"]}
            self.assertTrue(catalogue["plots"]["available"])
            self.assertEqual(catalogue["plots"]["count"], 2)  # buildings of 80 and 36 m2
            self.assertTrue(catalogue["parcels"]["available"])  # the parcel layer is unchanged

            plots = self.ok("/api/map/plots", source=source)
            self.assertEqual(len(plots["features"]), 2)
            uid = plots["features"][0]["id"]
            detail = self.ok(f"/api/map/features/{uid}", source=source)
            self.assertEqual(detail["properties"]["verification_status"], "REVIEW_REQUIRED")

            review = self.client.post("/api/reviews", json={"feature_id": uid, "action": "approve", "source": source})
            self.assertEqual(review.status_code, 200, review.text)

            analytics = self.ok("/api/analytics", source=source)
            self.assertEqual(analytics["candidate_plots"], 2)
            self.assertEqual(analytics["plots"]["one_building_seed_rule"], 2)
            self.assertEqual(analytics["plots"]["one_building_share_all"], 1.0)
            self.assertIn("two ways", analytics["plots"]["one_building_basis"])
            self.assertNotEqual(analytics["candidate_plots"], analytics["candidate_parcels"])

            exported = self.client.get("/api/export/geojson", params={"source": source, "layer": "plots"}).json()
            self.assertEqual(len(exported["features"]), 2)
            self.assertEqual(exported["features"][0]["properties"]["delineation_method"], "morphological_tessellation")

        def test_reference_check(self):
            folder = settings.data_dir / "land_records"
            # One reference footprint per building, and one more in the first plot.
            records = [
                support.feature(support.rect(6, 6, 2, 2), ref="A"),
                support.feature(support.rect(12, 9, 2, 2), ref="B"),
                support.feature(support.rect(66, 6, 2, 2), ref="C"),
                support.feature(support.rect(300, 300, 2, 2), ref="far away"),
            ]
            path = support.write_json(folder / "reference_footprints.geojson", support.collection("ref", records))
            self.addCleanup(path.unlink)
            check = self.ok("/api/analytics", source=f"job:{self.job_id}")["plots"]["reference_check"]
            self.assertEqual(check["reference_features"], 3)  # the far one is outside the area
            self.assertEqual(check["plots_with_one"], 1)
            self.assertEqual(check["plots_with_several"], 1)
            self.assertEqual(check["plots_with_none"], 0)
            self.assertEqual(check["reference_with_own_plot"], 1)


if __name__ == "__main__":
    unittest.main()
