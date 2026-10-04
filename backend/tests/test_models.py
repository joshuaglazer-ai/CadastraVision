"""Choosing the segmentation model per job, from the registry."""

import csv
import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.ai import model as model_module
from backend.ai import registry
from backend.config import settings
from backend.core import runtime
from backend.services import processing_service
from backend.tests import support
from backend.tests.test_processing import ProcessingCase, fake_pipeline

REPO_REGISTRY = Path(__file__).resolve().parents[1] / "models" / "registry.json"
URBAN_FILE = "cadastra_unet_resnet34_uavpal_sep.pth"
URBAN_BYTES = b"stand-in urban checkpoint for tests"


class RegistryCase(ProcessingCase):
    """Uses the project's registry.json in the test models folder."""

    def setUp(self):
        super().setUp()
        self.models = registry.models_dir(settings)
        shutil.copy(REPO_REGISTRY, self.models / registry.REGISTRY_FILE_NAME)
        self.addCleanup(lambda: (self.models / registry.REGISTRY_FILE_NAME).unlink(missing_ok=True))
        self.addCleanup(lambda: (self.models / URBAN_FILE).unlink(missing_ok=True))

    def add_urban(self):
        (self.models / URBAN_FILE).write_bytes(URBAN_BYTES)

    def models_listing(self):
        return {m["id"]: m for m in self.ok("/api/processing/models")["models"]}

    def run_job(self, **params):
        job_id = self.create_job()["job_id"]
        seen = {}

        def capture(input_path, output_dir, **kwargs):
            seen.update(model_path=kwargs.get("model_path"), model_info=kwargs.get("model_info"))
            return fake_pipeline(input_path, output_dir, **kwargs)

        with mock.patch.object(processing_service.pipeline, "run_pipeline", side_effect=capture):
            response = self.client.post(f"/api/processing/{job_id}/start", params=params or None)
            if response.status_code == 200:
                self.wait_for(job_id)
        return job_id, response, seen


class ModelSelectionTests(RegistryCase):
    def test_default_model_is_the_village_model(self):
        listing = self.ok("/api/processing/models")
        self.assertEqual(listing["default"], "village")
        village = self.models_listing()["village"]
        self.assertTrue(village["available"])
        self.assertEqual(village["suits"], "village")
        self.assertEqual(len(village["hash"]), 12)
        self.assertNotIn("_path", village)
        self.assertNotIn(str(self.models), json.dumps(listing))  # no server paths

        job_id, response, seen = self.run_job()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(Path(seen["model_path"]).name, "best_weighted_multiclass_unet.pth")
        self.assertEqual(self.ok(f"/api/processing/{job_id}")["model"]["id"], "village")

    def test_missing_file_is_listed_as_unavailable_not_an_error(self):
        urban = self.models_listing()["urban"]
        self.assertFalse(urban["available"])
        self.assertIn(f"File missing: put {URBAN_FILE} in", urban["reason"])
        self.assertIsNone(urban["hash"])
        self.assertEqual(urban["licence"], "CC BY-NC-SA 4.0: research and demo use")
        _, response, _ = self.run_job(model_id="urban")
        self.assertEqual(response.status_code, 409)
        self.assertIn("unavailable", response.json()["detail"])

    def test_choosing_the_urban_model(self):
        self.add_urban()
        urban = self.models_listing()["urban"]
        self.assertTrue(urban["available"])
        expected_hash = hashlib.sha256(URBAN_BYTES).hexdigest()[:12]
        self.assertEqual(urban["hash"], expected_hash)

        job_id, response, seen = self.run_job(model_id="urban")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(Path(seen["model_path"]).name, URBAN_FILE)
        self.assertEqual(seen["model_info"]["id"], "urban")
        job = self.ok(f"/api/processing/{job_id}")
        self.assertEqual(
            {k: job["model"][k] for k in ("id", "file", "hash")},
            {"id": "urban", "file": URBAN_FILE, "hash": expected_hash},
        )
        label = next(s["label"] for s in runtime.known_sources(self.store) if s.get("job_id") == job_id)
        self.assertIn("Urban model", label)

    def test_unknown_or_path_like_ids_are_rejected(self):
        for bad in ("suburban", "../../etc/passwd", "C:\\models\\x.pth", URBAN_FILE):
            with self.subTest(bad=bad):
                _, response, seen = self.run_job(model_id=bad)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(seen, {})

    def test_model_is_recorded_on_the_job_and_in_every_export(self):
        self.add_urban()
        job_id, response, _ = self.run_job(model_id="urban")
        self.assertEqual(response.status_code, 200, response.text)
        params = {"source": f"job:{job_id}", "layer": "landcover"}

        geo = self.client.get("/api/export/geojson", params=params).json()
        self.assertEqual(geo["metadata"]["model"]["id"], "urban")
        self.assertEqual(geo["metadata"]["model"]["file"], URBAN_FILE)
        self.assertTrue(all(f["properties"]["model_id"] == "urban" for f in geo["features"]))

        text = self.client.get("/api/export/csv", params=params).text
        self.assertIn(f"# model: Urban model (UAVPal, Bhopal) (id urban, file {URBAN_FILE}", text)
        rows = list(csv.DictReader(io.StringIO("\n".join(l for l in text.splitlines() if not l.startswith("#")))))
        self.assertTrue(rows and all(r["model_id"] == "urban" for r in rows))

        try:
            import pyogrio  # noqa: F401
        except ImportError:  # pragma: no cover
            return
        gpkg = self.client.get("/api/export/gpkg", params=params)
        self.assertEqual(gpkg.status_code, 200, gpkg.text[:200])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "e.gpkg"
            path.write_bytes(gpkg.content)
            con = sqlite3.connect(path)
            try:
                meta = dict(con.execute("SELECT key, value FROM cadastra_vision_metadata"))
                ids = {r[0] for r in con.execute("SELECT model_id FROM cadastra_vision_landcover")}
            finally:
                con.close()
        self.assertEqual(meta["model.id"], "urban")
        self.assertEqual(ids, {"urban"})

    def test_existing_jobs_are_recorded_as_the_village_model(self):
        job_id = self.create_job()["job_id"]
        waiting = self.create_job()["job_id"]
        self.store.update_job(job_id, model=None, status="COMPLETED")
        self.store.update_job(waiting, model=None)  # UPLOADED: has not run
        self.assertEqual(self.store.jobs_without_model(), [job_id])
        self.assertEqual(processing_service.record_default_model(self.store, settings), 1)
        model = self.store.get_job(job_id)["model"]
        self.assertEqual(model["id"], "village")
        self.assertIn("Recorded after the run", model["note"])
        self.assertEqual(processing_service.record_default_model(self.store, settings), 0)
        self.assertIsNone(self.store.get_job(waiting)["model"])  # recorded when it starts


class RegistryFileTests(unittest.TestCase):
    def test_unsupported_entries_and_missing_registry(self):
        with tempfile.TemporaryDirectory() as folder:
            from dataclasses import replace

            config = replace(settings, configured_model_path=Path(folder) / "best_weighted_multiclass_unet.pth")
            (Path(folder) / "best_weighted_multiclass_unet.pth").write_bytes(b"x")
            # No registry file: the default model only, with a note.
            listing = registry.list_models(config)
            self.assertEqual([m["id"] for m in listing["models"]], ["village"])
            self.assertIn("not found", listing["note"])
            # An entry for another architecture is listed, unavailable, with the reason.
            (Path(folder) / "other.pth").write_bytes(b"y")
            (Path(folder) / "registry.json").write_text(json.dumps({"models": [
                {"id": "village", "file": "best_weighted_multiclass_unet.pth", "default": True},
                {"id": "other", "file": "other.pth", "classes": 4},
                {"id": "sneaky", "file": "../outside.pth"},
            ]}), encoding="utf-8")
            models = {m["id"]: m for m in registry.list_models(config)["models"]}
            self.assertTrue(models["village"]["available"])
            self.assertFalse(models["other"]["available"])
            self.assertIn("classes 4", models["other"]["reason"])
            self.assertFalse(models["sneaky"]["available"])
            self.assertIn("without a path", models["sneaky"]["reason"])


class OneModelInMemoryTests(unittest.TestCase):
    def test_switching_models_releases_the_previous_one_first(self):
        model_module.release_model()
        self.addCleanup(model_module.release_model)
        held_when_loading = []

        def fake_load(path, device=None):
            held_when_loading.append(model_module._cached.get("model"))
            return object(), "cpu"

        with tempfile.TemporaryDirectory() as folder, mock.patch.object(model_module, "load_model", side_effect=fake_load):
            village, urban = Path(folder) / "village.pth", Path(folder) / "urban.pth"
            village.write_bytes(b"v")
            urban.write_bytes(b"u")
            first, _ = model_module.get_model(village)
            again, _ = model_module.get_model(village)
            second, _ = model_module.get_model(urban)
        self.assertIs(first, again)  # cached, not loaded twice
        self.assertIsNot(first, second)
        self.assertEqual(held_when_loading, [None, None])  # nothing held while loading the next
        self.assertEqual(model_module.loaded_model_path(), str(urban))


try:
    import rasterio  # noqa: F401

    HAVE_STACK = True
except ImportError:  # pragma: no cover
    HAVE_STACK = False


@unittest.skipUnless(HAVE_STACK, "raster stack not installed")
class PipelineRecordsModelTests(unittest.TestCase):
    def test_features_carry_the_model(self):
        from backend.ai import pipeline
        from backend.tests import rasters

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            info = {"id": "urban", "name": "Urban model", "file": URBAN_FILE, "hash": "abc123def456"}
            summary = pipeline.run_pipeline(
                rasters.write_orthoimage(root / "o.tif"), root / "out", model_path=root / URBAN_FILE,
                job_id="J", tile_size=256, overlap=32, chunk=256, model_loader=rasters.loader, model_info=info,
            )
            features = json.loads((root / "out" / "ai_features.geojson").read_text(encoding="utf-8"))
            parcels = json.loads((root / "out" / "candidate_parcels.geojson").read_text(encoding="utf-8"))
        self.assertEqual(summary["model"], "Urban model")
        self.assertEqual(summary["model_info"]["hash"], "abc123def456")
        self.assertEqual(features["metadata"]["model_info"]["id"], "urban")
        for feature in features["features"] + parcels["features"]:
            self.assertEqual(
                (feature["properties"]["model_id"], feature["properties"]["model_hash"]), ("urban", "abc123def456")
            )


if __name__ == "__main__":
    unittest.main()
