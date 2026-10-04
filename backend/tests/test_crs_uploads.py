"""Both upload paths accept data in EPSG:32643 (UTM 43N) and EPSG:4326.

UTM zone 43N covers 72 E to 78 E, which includes Uplarshi (77.6 E) and
Bhopal (77.4 E). A drone GeoTIFF goes through the Processing upload; a
GeoJSON of reference footprints through the Datasets upload as an existing
GIS layer.
"""

import json
import tempfile
import unittest
from pathlib import Path

from backend.config import settings
from backend.tests import rasters, support
from backend.tests.apicase import ApiCase

try:
    import rasterio
    from pyproj import Transformer

    HAVE_STACK = True
    MISSING = ""
except ImportError as exc:  # pragma: no cover
    HAVE_STACK = False
    MISSING = str(exc)


def write_orthoimage_4326(path: Path) -> Path:
    """The synthetic scene with the same ground size, in longitude/latitude."""

    from rasterio.enums import ColorInterp
    from rasterio.transform import from_origin

    rgb, alpha = rasters.paint()
    west, north = rasters.origin()
    to_lonlat = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
    lon0, lat0 = to_lonlat.transform(west, north)
    lon1, _ = to_lonlat.transform(west + rasters.PIXEL * rasters.WIDTH, north)
    _, lat1 = to_lonlat.transform(west, north - rasters.PIXEL * rasters.HEIGHT)
    dlon = (lon1 - lon0) / rasters.WIDTH
    dlat = (lat0 - lat1) / rasters.HEIGHT
    profile = dict(
        driver="GTiff", width=rasters.WIDTH, height=rasters.HEIGHT, count=4, dtype="uint8",
        transform=from_origin(lon0, lat0, dlon, dlat), photometric="RGB", alpha="YES", crs="EPSG:4326",
    )
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(rgb, [1, 2, 3])
        dst.write(alpha, 4)
        dst.colorinterp = [ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha]
    return path


@unittest.skipUnless(HAVE_STACK, f"raster stack not installed: {MISSING}")
class GeoTiffUploadTests(ApiCase):
    def upload(self, path: Path):
        response = self.client.post(
            "/api/processing/upload", files={"file": (path.name, path.read_bytes(), "image/tiff")}
        )
        self.assertEqual(response.status_code, 200, response.text[:300])
        return response.json()

    def test_utm_and_lonlat_geotiffs_are_accepted_and_located(self):
        with tempfile.TemporaryDirectory() as folder:
            utm = rasters.write_orthoimage(Path(folder) / "scene_32643.tif")
            lonlat = write_orthoimage_4326(Path(folder) / "scene_4326.tif")
            for path, crs in ((utm, "EPSG:32643"), (lonlat, "EPSG:4326")):
                with self.subTest(crs=crs):
                    job = self.upload(path)
                    meta = job["raster_meta"]
                    self.assertEqual(meta["crs"], crs)
                    lon_min, lat_min, lon_max, lat_max = meta["bounds_lonlat"]
                    self.assertAlmostEqual(lon_min, support.ORIGIN_LON, delta=0.001)
                    self.assertAlmostEqual(lat_min, support.ORIGIN_LAT, delta=0.001)
                    self.assertAlmostEqual(meta["resolution_m"][0], rasters.PIXEL, delta=rasters.PIXEL * 0.01)
                    # Checked against the current area like any other image.
                    check = self.ok(f"/api/processing/{job['job_id']}/area-check")
                    self.assertTrue(check["intersects"])

    def test_a_lonlat_geotiff_is_measured_in_ground_metres(self):
        from backend.ai import pipeline

        with tempfile.TemporaryDirectory() as folder:
            summary = pipeline.run_pipeline(
                write_orthoimage_4326(Path(folder) / "scene.tif"), Path(folder) / "out",
                model_path=Path("unused.pth"), job_id="J4326", tile_size=256, overlap=32, chunk=256,
                model_loader=rasters.loader,
            )
        self.assertEqual(summary["metric_crs"], "EPSG:32643")
        for name, expected in (("Field", 295.0), ("Building", 20.0), ("Road", 105.0), ("Water", 25.0)):
            with self.subTest(name=name):
                self.assertAlmostEqual(summary["classes"][name]["area_m2"], expected, delta=expected * 0.01)


class ReferenceLayerUploadTests(ApiCase):
    """Reference building footprints uploaded as an existing GIS layer."""

    def footprints(self, crs: str) -> bytes:
        if crs == "EPSG:32643":
            rings = [support.rect_utm(0, 0, 20, 10), support.rect_utm(40, 0, 15, 15)]
            collection = support.collection(
                "footprints", [support.feature({"type": "Polygon", "coordinates": [r]}, bldg=i) for i, r in enumerate(rings)],
                crs={"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32643"}},
            )
        else:
            polygons = [support.rect(0, 0, 20, 10), support.rect(40, 0, 15, 15)]
            collection = support.collection(
                "footprints", [support.feature(p, bldg=i) for i, p in enumerate(polygons)], crs=None
            )
        return json.dumps(collection).encode("utf-8")

    def test_utm_and_lonlat_footprints_upload_draw_and_measure(self):
        for crs, name in (("EPSG:32643", "footprints_utm.geojson"), ("EPSG:4326", "footprints_lonlat.geojson")):
            with self.subTest(crs=crs):
                response = self.client.post(
                    "/api/datasets/upload", data={"source_type": "land_records"},
                    files={"file": (name, self.footprints(crs), "application/geo+json")},
                )
                self.assertEqual(response.status_code, 200, response.text[:300])
                dataset = response.json()
                stored = settings.data_dir / "land_records" / dataset["name"]
                self.addCleanup(lambda p=stored: p.unlink(missing_ok=True))
                self.assertEqual(dataset["feature_count"], 2)

                overlay = self.ok(f"/api/datasets/{dataset['dataset_id']}/overlay")
                lon, lat = overlay["features"][0]["geometry"]["coordinates"][0][0]
                self.assertAlmostEqual(lon, support.ORIGIN_LON, delta=0.001)  # drawn in lon/lat
                self.assertAlmostEqual(lat, support.ORIGIN_LAT, delta=0.001)

                record = self.ok(f"/api/datasets/{dataset['dataset_id']}/features/0")
                self.assertAlmostEqual(record["metrics"]["area_m2"], 200.0, delta=0.5)

        layers = {layer["key"]: layer for layer in self.ok("/api/map/layers")["layers"]}
        self.assertTrue(layers["existing_gis"]["available"])
        self.assertEqual(layers["existing_gis"]["count"], 2)

    def test_utm_coordinates_without_a_crs_are_flagged_not_drawn(self):
        collection = support.collection(
            "footprints", [support.feature({"type": "Polygon", "coordinates": [support.rect_utm(0, 0, 20, 10)]})], crs=None
        )
        response = self.client.post(
            "/api/datasets/upload", data={"source_type": "land_records"},
            files={"file": ("utm_without_crs.geojson", json.dumps(collection).encode(), "application/geo+json")},
        )
        self.assertEqual(response.status_code, 200, response.text[:300])
        dataset = response.json()
        self.addCleanup(lambda: (settings.data_dir / "land_records" / dataset["name"]).unlink(missing_ok=True))
        self.assertEqual(dataset["status"], "REQUIRES REVIEW")
        self.assertIn("not degrees", " ".join(dataset["notes"]))
        self.assertIn("EPSG:32643", " ".join(dataset["notes"]))
        layers = {layer["key"]: layer for layer in self.ok("/api/map/layers")["layers"]}
        self.assertFalse(layers["existing_gis"]["available"])  # not drawn in the wrong place


if __name__ == "__main__":
    unittest.main()
