"""The assigned area comes from the registry entry of the signed-in user."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from backend.config import settings
from backend.core.auth import AuthUser
from backend.services import assignment_service
from backend.tests import support


def user(email: str, **extra) -> AuthUser:
    return AuthUser(user_id=f"id-{email}", email=email, name=extra.pop("name", None), **extra)


class AssignmentTests(unittest.TestCase):
    def test_registered_surveyor_gets_their_own_assignment(self):
        context = assignment_service.resolve_context(user(support.SURVEYOR_EMAIL), settings)
        self.assertEqual(context.assignment_id, "ASGN-TEST-001")
        self.assertEqual(context.surveyor_id, "SRV-TEST-7")
        self.assertEqual(context.assignment["village"], "Assigned Village")
        self.assertFalse(context.assignment["is_demo"])
        self.assertIsNotNone(context.boundary)
        # 150 m x 100 m boundary, measured in a projected CRS.
        self.assertAlmostEqual(context.assignment["area_ha"], 1.5, delta=0.001)
        self.assertEqual(len(context.bbox), 4)

    def test_email_match_ignores_case(self):
        context = assignment_service.resolve_context(user(support.SURVEYOR_EMAIL.upper().lower()), settings)
        self.assertEqual(context.assignment_id, "ASGN-TEST-001")

    def test_unregistered_user_gets_the_demo_assignment_labelled_as_demo(self):
        context = assignment_service.resolve_context(user("someone.else@example.test"), settings)
        self.assertEqual(context.assignment_id, "ASGN-TEST-DEMO")
        self.assertTrue(context.assignment["is_demo"])
        self.assertIn("DEMO", str(context.assignment.get("label", "")).upper())
        # Registry details about a person never leak into a demo session.
        self.assertNotEqual(context.surveyor_id, "SRV-TEST-7")

    def test_no_demo_fallback_means_no_assignment(self):
        strict = replace(settings, allow_demo_assignment=False)
        context = assignment_service.resolve_context(user("someone.else@example.test"), strict)
        self.assertIsNone(context.assignment)
        self.assertIsNone(context.boundary)
        self.assertTrue(any("No survey assignment" in note for note in context.notes))
        self.assertEqual(assignment_service.boundary_feature_collection(context)["features"], [])

    def test_missing_registry_is_reported_not_invented(self):
        with tempfile.TemporaryDirectory() as folder:
            empty = replace(settings, data_dir=Path(folder))
            context = assignment_service.resolve_context(user(support.SURVEYOR_EMAIL), empty)
        self.assertIsNone(context.assignment)
        self.assertTrue(context.notes)

    def test_surveyor_id_is_stable_and_derived_from_the_account(self):
        first = assignment_service.surveyor_id_for(user("a@example.test"))
        again = assignment_service.surveyor_id_for(user("a@example.test"))
        other = assignment_service.surveyor_id_for(user("b@example.test"))
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)
        self.assertTrue(first.startswith("SRV-"))

    def test_boundary_collection_carries_the_assignment(self):
        context = assignment_service.resolve_context(user(support.SURVEYOR_EMAIL), settings)
        collection = assignment_service.boundary_feature_collection(context)
        self.assertEqual(collection["type"], "FeatureCollection")
        self.assertEqual(len(collection["features"]), 1)
        self.assertEqual(collection["features"][0]["properties"]["assignment_id"], "ASGN-TEST-001")
        self.assertEqual(collection["features"][0]["geometry"]["type"], "Polygon")


if __name__ == "__main__":
    unittest.main()
