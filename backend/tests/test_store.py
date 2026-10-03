"""Persistent state: processing jobs, reviews and the audit trail."""

import tempfile
import unittest
from pathlib import Path

from backend.core.store import ACTION_STATUS, Store
from backend.tests import support

SOURCE = "existing"


def job_fields(**overrides):
    fields = dict(
        surveyor_id="SRV-1",
        surveyor_email="one@example.test",
        assignment_id="ASGN-1",
        input_dataset="ortho.tif",
        input_path="/tmp/ortho.tif",
        stages=[{"key": "UPLOAD", "status": "done"}],
        raster_meta={"width": 1024, "height": 768, "crs": "EPSG:32643"},
    )
    fields.update(overrides)
    return fields


def review_fields(**overrides):
    fields = dict(
        feature_id="CAND-000001",
        layer="parcels",
        source=SOURCE,
        surveyor_id="SRV-1",
        surveyor_email="one@example.test",
        assignment_id="ASGN-1",
        action="approve",
    )
    fields.update(overrides)
    return fields


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state" / "cadastra.db"
        self.store = Store(self.path)


class JobTests(StoreCase):
    def test_job_round_trip(self):
        job = self.store.create_job(**job_fields())
        self.assertTrue(job["job_id"].startswith("JOB-"))
        self.assertEqual(job["status"], "UPLOADED")
        self.assertEqual(job["stage"], "UPLOAD")
        self.assertEqual(job["progress"], 0)
        self.assertEqual(job["raster_meta"]["crs"], "EPSG:32643")
        self.assertEqual(job["stages"][0]["key"], "UPLOAD")
        for field in ("created_at", "updated_at", "surveyor_id", "assignment_id", "input_dataset"):
            self.assertTrue(job[field])

    def test_update_and_unknown_job(self):
        job = self.store.create_job(**job_fields())
        updated = self.store.update_job(
            job["job_id"], status="PROCESSING", stage="RUN_SEGMENTATION", progress=41.5,
            summary={"feature_count": 3},
        )
        self.assertEqual(updated["status"], "PROCESSING")
        self.assertEqual(updated["progress"], 41.5)
        self.assertEqual(updated["summary"], {"feature_count": 3})
        with self.assertRaises(KeyError):
            self.store.update_job("JOB-NOPE", status="FAILED")
        self.assertIsNone(self.store.get_job("JOB-NOPE"))

    def test_jobs_survive_a_restart(self):
        job = self.store.create_job(**job_fields())
        self.store.update_job(job["job_id"], status="COMPLETED", progress=100.0)

        reopened = Store(self.path)
        again = reopened.get_job(job["job_id"])
        self.assertEqual(again["status"], "COMPLETED")
        self.assertEqual(again["input_dataset"], "ortho.tif")

    def test_jobs_running_when_the_server_stopped_are_marked_failed(self):
        running = self.store.create_job(**job_fields())
        self.store.update_job(running["job_id"], status="PROCESSING")
        queued = self.store.create_job(**job_fields(), status="QUEUED")
        done = self.store.create_job(**job_fields())
        self.store.update_job(done["job_id"], status="COMPLETED")

        reopened = Store(self.path)
        self.assertEqual(reopened.fail_interrupted_jobs(), 2)
        self.assertEqual(reopened.get_job(running["job_id"])["status"], "FAILED")
        self.assertIn("Interrupted", reopened.get_job(queued["job_id"])["error"])
        self.assertEqual(reopened.get_job(done["job_id"])["status"], "COMPLETED")

    def test_listing_is_scoped_to_a_surveyor(self):
        self.store.create_job(**job_fields())
        self.store.create_job(**job_fields(surveyor_id="SRV-2"))
        self.assertEqual(len(self.store.list_jobs()), 2)
        self.assertEqual(len(self.store.list_jobs(surveyor_id="SRV-2")), 1)
        self.assertEqual(self.store.job_counts("SRV-1"), {"UPLOADED": 1})


class ReviewTests(StoreCase):
    def test_unreviewed_feature_is_ai_generated(self):
        self.assertEqual(self.store.effective_status("CAND-000001", SOURCE), "AI_GENERATED")

    def test_each_action_sets_its_status(self):
        geometry = support.rect(0, 0, 10, 10)
        extras = {
            "edit": {"edited_geometry": geometry, "original_geometry": geometry},
            "add_ground_truth": {"ground_truth": {"observed_class": "Building"}},
        }
        for index, (action, status) in enumerate(ACTION_STATUS.items()):
            feature_id = f"F-{index}"
            review = self.store.add_review(
                **review_fields(feature_id=feature_id, action=action, comment="checked", **extras.get(action, {}))
            )
            self.assertEqual(review["verification_status"], status)
            self.assertEqual(self.store.effective_status(feature_id, SOURCE), status)

    def test_review_records_who_what_when_and_the_model_signals(self):
        review = self.store.add_review(
            **review_fields(comment="Matches the fence line", model_confidence=0.91, model_entropy=0.22)
        )
        self.assertTrue(review["review_id"].startswith("REV-"))
        self.assertEqual(review["surveyor_id"], "SRV-1")
        self.assertEqual(review["model_confidence"], 0.91)
        self.assertEqual(review["model_entropy"], 0.22)
        self.assertTrue(review["created_at"])

    def test_invalid_reviews_are_refused(self):
        with self.assertRaises(ValueError):
            self.store.add_review(**review_fields(action="bless"))
        with self.assertRaises(ValueError):
            self.store.add_review(**review_fields(action="edit"))
        with self.assertRaises(ValueError):
            self.store.add_review(**review_fields(action="add_ground_truth"))

    def test_latest_decision_wins_and_edit_geometry_is_kept(self):
        ai_geometry = support.rect(0, 0, 10, 10)
        corrected = support.rect(0, 0, 12, 10)
        self.store.add_review(**review_fields(action="flag", comment="check east edge"))
        self.store.add_review(
            **review_fields(action="edit", original_geometry=ai_geometry, edited_geometry=corrected)
        )
        state = self.store.feature_states(SOURCE)[(SOURCE, "CAND-000001")]
        self.assertEqual(state["verification_status"], "EDITED")
        self.assertEqual(state["edited_geometry"], corrected)
        self.assertEqual(state["review_count"], 2)

        stored = self.store.list_reviews(feature_id="CAND-000001")[0]
        self.assertEqual(stored["original_geometry"], ai_geometry)

    def test_ground_truth_does_not_undo_a_decision(self):
        self.store.add_review(**review_fields(action="approve"))
        self.store.add_review(
            **review_fields(action="add_ground_truth", ground_truth={"observed_class": "Field"})
        )
        state = self.store.feature_states(SOURCE)[(SOURCE, "CAND-000001")]
        self.assertEqual(state["verification_status"], "SURVEYOR_VERIFIED")
        self.assertTrue(state["has_ground_truth"])
        self.assertEqual(len(self.store.ground_truth_records(SOURCE)), 1)

    def test_reviews_survive_a_restart(self):
        self.store.add_review(**review_fields(action="reject", comment="shadow"))
        reopened = Store(self.path)
        self.assertEqual(reopened.effective_status("CAND-000001", SOURCE), "REJECTED")
        self.assertEqual(reopened.review_stats(SOURCE)["rejected_count"], 1)

    def test_sources_are_kept_apart(self):
        self.store.add_review(**review_fields())
        self.assertEqual(self.store.effective_status("CAND-000001", "job:JOB-X"), "AI_GENERATED")


class AuditTests(StoreCase):
    def test_every_review_writes_an_audit_event(self):
        ai_geometry = support.rect(0, 0, 10, 10)
        corrected = support.rect(0, 0, 12, 10)
        self.store.add_review(
            **review_fields(
                action="edit", comment="moved to the fence",
                original_geometry=ai_geometry, edited_geometry=corrected,
            )
        )
        event = self.store.list_audit(entity_id="CAND-000001")[0]
        self.assertEqual(event["action"], "review.edit")
        self.assertEqual(event["actor_id"], "SRV-1")
        self.assertEqual(event["reason"], "moved to the fence")
        self.assertEqual(event["before"]["verification_status"], "AI_GENERATED")
        self.assertEqual(event["before"]["geometry"], ai_geometry)
        self.assertEqual(event["after"]["verification_status"], "EDITED")
        self.assertEqual(event["after"]["geometry"], corrected)
        self.assertTrue(event["at"])

    def test_changing_a_review_is_audited_too(self):
        review = self.store.add_review(**review_fields(action="flag", comment="unsure"))
        changed = self.store.update_review(
            review["review_id"], actor_id="SRV-1", actor_email="one@example.test",
            comment="Visited: boundary is correct", action="approve",
        )
        self.assertEqual(changed["verification_status"], "SURVEYOR_VERIFIED")
        events = self.store.list_audit()
        self.assertEqual(events[0]["action"], "review.update")
        self.assertEqual(events[0]["before"]["action"], "flag")
        self.assertEqual(events[0]["after"]["action"], "approve")
        with self.assertRaises(KeyError):
            self.store.update_review("REV-NOPE", actor_id="SRV-1", actor_email=None, comment="x")


if __name__ == "__main__":
    unittest.main()
