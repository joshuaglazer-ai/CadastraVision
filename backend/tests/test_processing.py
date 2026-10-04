"""Processing jobs: upload validation, persistent state, real stage reports.

The segmentation itself is covered by ``test_pipeline``. Here the pipeline
function is replaced by a stand-in so that the job machinery (state, stage
bookkeeping, failure handling, indexing of the output) can be exercised on
machines without PyTorch. The stand-in is test code only.
"""

import time
import unittest
from pathlib import Path
from unittest import mock

from backend.ai import pipeline
from backend.core import runtime
from backend.services import processing_service
from backend.tests import support
from backend.tests.apicase import ApiCase

RASTER_META = {
    "file_name": "ortho.tif", "width": 2048, "height": 1536, "band_count": 3,
    "crs": "EPSG:32643", "resolution_m": [0.05, 0.05], "nodata": None,
    "bounds_lonlat": [77.625, 28.559, 77.626, 28.5597], "warnings": [], "errors": [],
}


def fake_pipeline(input_path, output_dir, *, job_id, report, **_):
    """Reports every stage and writes outputs shaped like the real ones."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for key, _label in pipeline.STAGES[1:]:
        report(key, "running", fraction=0.5, detail="working")
        report(key, "done", detail="finished")

    features = support.landcover_features()
    for index, feature in enumerate(features):
        feature["properties"].update(confidence=0.95 - index * 0.05, entropy=0.1 + index * 0.1)
    parcels = support.parcel_features()
    for feature in parcels:
        feature["properties"].update(confidence=0.9, entropy=0.2)
    support.write_json(output_dir / "ai_features.geojson", support.collection("ai", features))
    support.write_json(output_dir / "candidate_parcels.geojson", support.collection("parcels", parcels))
    return {"job_id": job_id, "feature_count": len(features), "candidate_parcel_count": len(parcels),
            "status": "AI GENERATED / PRELIMINARY"}


def failing_pipeline(input_path, output_dir, *, report, **_):
    report("LOAD_MODEL", "running", fraction=0.0, detail="Loading checkpoint")
    raise pipeline.PipelineError("Model checkpoint not found.")


class ProcessingCase(ApiCase):
    def upload(self, name="ortho.tif", body=b"II*\x00 not a real raster"):
        return self.client.post("/api/processing/upload", files={"file": (name, body, "image/tiff")})

    def create_job(self):
        with mock.patch.object(processing_service, "_inspect", return_value=dict(RASTER_META)):
            response = self.upload()
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def wait_for(self, job_id, states=("COMPLETED", "FAILED"), timeout=20.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.ok(f"/api/processing/{job_id}")
            # Also wait for the worker thread to let go of the job: on Windows
            # its database connection must be closed before the test's
            # temporary state database can be deleted.
            if job["status"] in states and job_id not in processing_service._running:
                return job
            time.sleep(0.05)
        self.fail(f"job {job_id} did not reach {states}")


class UploadTests(ProcessingCase):
    def test_only_geotiff_is_accepted(self):
        response = self.upload(name="photo.png")
        self.assertEqual(response.status_code, 400)
        self.assertIn("GeoTIFF", response.json()["detail"])

    def test_empty_upload_is_rejected(self):
        self.assertEqual(self.upload(body=b"").status_code, 400)

    def test_raster_that_fails_validation_is_rejected_and_not_kept(self):
        meta = dict(RASTER_META, errors=["The raster has no coordinate reference system."])
        with mock.patch.object(processing_service, "_inspect", return_value=meta):
            response = self.upload()
        self.assertEqual(response.status_code, 422)
        self.assertIn("coordinate reference system", response.json()["detail"])
        self.assertEqual(self.ok("/api/processing")["jobs"], [])

    def test_upload_registers_a_job_without_exposing_server_paths(self):
        job = self.create_job()
        self.assertTrue(job["job_id"].startswith("JOB-"))
        self.assertEqual(job["status"], "UPLOADED")
        self.assertEqual(job["stage"], "UPLOAD")
        self.assertEqual(job["progress"], 0)
        self.assertEqual(job["input_dataset"], "ortho.tif")
        self.assertEqual(job["raster_meta"]["crs"], "EPSG:32643")
        self.assertNotIn("input_path", job)
        self.assertTrue(job["surveyor_id"].startswith("SRV-"))
        self.assertTrue(job["assignment_id"])
        self.assertEqual([stage["key"] for stage in job["stages"]], [key for key, _ in pipeline.STAGES])
        self.assertEqual(job["stages"][0]["status"], "done")
        self.assertTrue(all(stage["status"] == "pending" for stage in job["stages"][1:]))


class LifecycleTests(ProcessingCase):
    def test_stage_list_matches_the_pipeline(self):
        stages = self.ok("/api/processing/stages")["stages"]
        self.assertEqual(
            [stage["key"] for stage in stages],
            ["UPLOAD", "LOAD_MODEL", "READ_RASTER", "TILE_IMAGE", "RUN_SEGMENTATION",
             "CALCULATE_CONFIDENCE", "CALCULATE_ENTROPY", "POLYGONIZE", "REPAIR_GEOMETRY",
             "GENERATE_GIS", "RUN_QA", "READY_FOR_REVIEW"],
        )

    def test_job_runs_to_ready_for_review_and_its_layers_open_on_the_map(self):
        job_id = self.create_job()["job_id"]
        self.assertEqual(self.get(f"/api/processing/{job_id}/result").status_code, 409)

        with mock.patch.object(processing_service.pipeline, "run_pipeline", side_effect=fake_pipeline):
            started = self.client.post(f"/api/processing/{job_id}/start")
            self.assertEqual(started.status_code, 200, started.text)
            self.assertIn(started.json()["status"], ("QUEUED", "PROCESSING", "COMPLETED"))
            job = self.wait_for(job_id)

        self.assertEqual(job["status"], "COMPLETED", job.get("error"))
        self.assertEqual(job["stage"], "READY_FOR_REVIEW")
        self.assertEqual(job["progress"], 100.0)
        self.assertTrue(all(stage["status"] == "done" for stage in job["stages"]))
        source = f"job:{job_id}"
        self.assertEqual(job["source"], source)
        self.addCleanup(runtime.get_layers().drop, source)

        result = self.ok(f"/api/processing/{job_id}/result")
        self.assertEqual(result["label"], "AI GENERATED / PRELIMINARY")
        self.assertEqual(result["layers"]["landcover"]["totals"]["features"], len(support.LANDCOVER))
        self.assertEqual(result["layers"]["landcover"]["totals"]["with_confidence"], len(support.LANDCOVER))

        # The output is a layer source of its own, with model-derived uncertainty.
        catalogue = self.ok("/api/map/layers", source=source)
        self.assertEqual(catalogue["source"], source)
        features = self.ok("/api/map/features", source=source, zoom=21)["features"]
        self.assertTrue(features)
        self.assertTrue(all(f["properties"]["confidence"] is not None for f in features))
        analytics = self.ok("/api/analytics", source=source)
        self.assertTrue(analytics["uncertainty"]["available"])
        self.assertIsNotNone(analytics["uncertainty"]["mean_confidence"])
        # Low-confidence features of this job are ranked for review.
        queue = self.ok("/api/reviews/queue", source=source, group="high")
        self.assertGreater(queue["counts"]["high"], 0)

        actions = [event["action"] for event in self.ok("/api/audit")["events"]]
        for expected in ("job.create", "job.start", "job.complete"):
            self.assertIn(expected, actions)

        # Starting a finished job again does not re-run it.
        again = self.client.post(f"/api/processing/{job_id}/start").json()
        self.assertEqual(again["status"], "COMPLETED")

    def test_failure_is_reported_with_the_stage_that_failed(self):
        job_id = self.create_job()["job_id"]
        with mock.patch.object(processing_service.pipeline, "run_pipeline", side_effect=failing_pipeline):
            self.client.post(f"/api/processing/{job_id}/start")
            job = self.wait_for(job_id)

        self.assertEqual(job["status"], "FAILED")
        self.assertEqual(job["error"], "Model checkpoint not found.")
        self.assertLess(job["progress"], 100.0)
        failed = [stage for stage in job["stages"] if stage["status"] == "failed"]
        self.assertEqual([stage["key"] for stage in failed], ["LOAD_MODEL"])
        self.assertIsNone(job["source"])
        self.assertEqual(self.get(f"/api/processing/{job_id}/result").status_code, 409)
        self.assertIn("job.fail", [event["action"] for event in self.ok("/api/audit")["events"]])

    def test_plots_are_queued_in_the_same_update_that_completes_the_job(self):
        # A client that sees COMPLETED must also see the plots on their way,
        # or it stops polling and shows "Not built" until reloaded.
        seen = {}

        def capture(job_id, settings, store, actor="system"):
            job = store.get_job(job_id)
            seen.update(status=job["status"], plots=(job.get("summary") or {}).get("plots"))
            return {"status": "COMPLETED"}

        job_id = self.create_job()["job_id"]
        self.addCleanup(runtime.get_layers().drop, f"job:{job_id}")
        from backend.services import plot_service

        with mock.patch.object(processing_service.pipeline, "run_pipeline", side_effect=fake_pipeline), \
                mock.patch.object(plot_service, "build_for_job", side_effect=capture):
            self.client.post(f"/api/processing/{job_id}/start")
            self.wait_for(job_id)

        self.assertEqual(seen["status"], "COMPLETED")
        self.assertEqual(seen["plots"]["status"], "QUEUED")

    def test_plots_interrupted_by_a_restart_are_marked_failed(self):
        job_id = self.create_job()["job_id"]
        self.store.update_job(job_id, status="COMPLETED", summary={"plots": {"status": "RUNNING"}})
        processing_service.recover_interrupted(self.store)
        plots = self.store.get_job(job_id)["summary"]["plots"]
        self.assertEqual(plots["status"], "FAILED")
        self.assertIn("restart", plots["error"])

    def test_unknown_job(self):
        self.assertEqual(self.get("/api/processing/JOB-NOPE").status_code, 404)
        self.assertEqual(self.client.post("/api/processing/JOB-NOPE/start").status_code, 404)

    def test_jobs_of_other_surveyors_on_other_assignments_are_hidden(self):
        foreign = self.store.create_job(
            surveyor_id="SRV-OTHER", surveyor_email="other@example.test", assignment_id="ASGN-OTHER",
            input_dataset="other.tif", input_path="/nonexistent/other.tif",
            stages=pipeline.initial_stages(), raster_meta=None,
        )
        self.assertEqual(self.get(f"/api/processing/{foreign['job_id']}").status_code, 404)
        self.assertEqual(self.client.post(f"/api/processing/{foreign['job_id']}/start").status_code, 404)

    def test_missing_input_file_cannot_be_started(self):
        me = self.ok("/api/surveyors/me")
        job = self.store.create_job(
            surveyor_id=me["surveyor"]["surveyor_id"], surveyor_email=me["surveyor"]["email"],
            assignment_id=me["assignment"]["assignment_id"], input_dataset="gone.tif",
            input_path="/nonexistent/gone.tif", stages=pipeline.initial_stages(), raster_meta=None,
        )
        self.assertEqual(self.client.post(f"/api/processing/{job['job_id']}/start").status_code, 409)


class AreaCheckTests(ProcessingCase):
    """Before a job starts, its image is checked against the current area."""

    def run_to_end(self, job_id, **params):
        with mock.patch.object(processing_service.pipeline, "run_pipeline", side_effect=fake_pipeline):
            response = self.client.post(f"/api/processing/{job_id}/start", params=params or None)
            if response.status_code == 200:
                self.wait_for(job_id)
        return response

    def test_image_inside_the_area_starts_and_is_recorded(self):
        job_id = self.create_job()["job_id"]
        check = self.ok(f"/api/processing/{job_id}/area-check")
        self.assertTrue(check["intersects"])
        self.assertIsNone(check["distance_km"])
        self.assertEqual(self.run_to_end(job_id).status_code, 200)
        job = self.ok(f"/api/processing/{job_id}")
        self.assertTrue(job["area_check"]["intersects"])
        self.assertFalse(job["area_check"]["confirmed_outside_area"])
        self.assertEqual(job["assignment_id"], "ASGN-TEST-DEMO")

    def test_image_outside_the_area_needs_confirmation_and_is_recorded(self):
        job_id = self.create_job()["job_id"]
        # Switch to a work area 50 km away from the image.
        far = self.client.post(
            "/api/assignments",
            json={"name": "Elsewhere", "origin": "drawn", "boundary": support.rect(50000, 50000, 200, 200)},
        )
        self.assertEqual(far.status_code, 201, far.text)
        area_id = far.json()["assignment_id"]

        check = self.ok(f"/api/processing/{job_id}/area-check")
        self.assertFalse(check["intersects"])
        self.assertGreater(check["distance_km"], 40)
        self.assertLess(check["distance_km"], 90)
        self.assertIn("lies outside Elsewhere", check["message"])
        # The demo assignment covers the image, so switching to it is offered.
        self.assertIn("ASGN-TEST-DEMO", [a["assignment_id"] for a in check["matching_areas"]])

        refused = self.run_to_end(job_id)
        self.assertEqual(refused.status_code, 409)
        self.assertIn("outside", refused.json()["detail"])
        self.assertEqual(self.ok(f"/api/processing/{job_id}")["status"], "UPLOADED")

        self.assertEqual(self.run_to_end(job_id, confirm_outside_area="true").status_code, 200)
        job = self.ok(f"/api/processing/{job_id}")
        self.assertFalse(job["area_check"]["intersects"])
        self.assertTrue(job["area_check"]["confirmed_outside_area"])
        self.assertEqual(job["assignment_id"], area_id)
        started = [e for e in self.ok("/api/audit")["events"] if e["action"] == "job.start"]
        self.assertFalse(started[0]["after"]["image_intersects_area"])

    def test_switching_area_before_starting_records_the_new_area(self):
        job_id = self.create_job()["job_id"]
        self.client.post(
            "/api/assignments",
            json={"name": "Elsewhere", "origin": "drawn", "boundary": support.rect(50000, 50000, 200, 200)},
        )
        self.assertEqual(self.client.post("/api/assignments/ASGN-TEST-DEMO/activate").status_code, 200)
        self.assertEqual(self.run_to_end(job_id).status_code, 200)
        job = self.ok(f"/api/processing/{job_id}")
        self.assertTrue(job["area_check"]["intersects"])
        self.assertEqual(job["assignment_id"], "ASGN-TEST-DEMO")


class ProgressTests(unittest.TestCase):
    def test_progress_is_a_function_of_real_stage_state(self):
        stages = pipeline.initial_stages()
        self.assertEqual(pipeline.overall_progress(stages), 0.0)

        index = {stage["key"]: stage for stage in stages}
        index["UPLOAD"]["status"] = "done"
        index["LOAD_MODEL"]["status"] = "done"
        index["READ_RASTER"]["status"] = "done"
        index["TILE_IMAGE"]["status"] = "done"
        index["RUN_SEGMENTATION"].update(status="running", fraction=0.5)
        expected = (0.04 + 0.02 + 0.01 + 0.62 * 0.5) * 100
        self.assertAlmostEqual(pipeline.overall_progress(stages), expected, places=1)

        for stage in stages:
            stage["status"] = "done"
        self.assertEqual(pipeline.overall_progress(stages), 100.0)

    def test_stage_weights_cover_the_whole_bar(self):
        self.assertAlmostEqual(sum(pipeline.STAGE_WEIGHT.values()), 1.0, places=6)
        self.assertEqual(set(pipeline.STAGE_WEIGHT), {key for key, _ in pipeline.STAGES})


if __name__ == "__main__":
    unittest.main()
