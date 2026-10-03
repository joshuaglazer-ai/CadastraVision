"""Work areas: a surveyor's own boundaries, isolated per account and audited."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from backend.config import settings
from backend.core import auth
from backend.core.auth import AuthUser
from backend.core.store import Store
from backend.gis.geometry import bbox_polygon
from backend.services import assignment_service, work_area_service
from backend.services.work_area_service import WorkAreaError
from backend.tests import support
from backend.tests.apicase import ApiCase

ASHA = AuthUser(user_id="uid-asha", email=support.SURVEYOR_EMAIL, name="Asha Rao", govt_surveyor_id="UP/SRV/0412")
RAVI = AuthUser(user_id="uid-ravi", email="ravi.surveyor@example.test", name="Ravi Kumar")
NEWCOMER = AuthUser(user_id="uid-new", email="new.surveyor@example.test", name="New Surveyor")


def area_payload(**extra):
    payload = {
        "name": "North fields",
        "state": "Uttar Pradesh",
        "district": "Gautam Buddh Nagar",
        "taluk": "Dadri",
        "village": "Uplarshi",
        "origin": "drawn",
        # 200 m x 100 m: exactly 2 ha.
        "boundary": support.rect(500, 500, 200, 100),
    }
    payload.update(extra)
    return payload


class StoreCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / "state.db")


# --------------------------------------------------------------- geometry
class BoundaryValidationTests(unittest.TestCase):
    def test_area_is_measured_by_the_server(self):
        geometry, metrics = work_area_service.validate_boundary(support.rect(0, 0, 200, 100))
        self.assertAlmostEqual(metrics["area_m2"], 20000.0, delta=1.0)
        self.assertEqual(geometry["type"], "Polygon")
        self.assertTrue(metrics["metric_crs"].startswith("EPSG:326"))

    def test_feature_and_single_feature_collection_are_accepted(self):
        polygon = support.rect(0, 0, 50, 50)
        for boundary in (
            support.feature(polygon, name="a"),
            support.collection("upload", [support.feature(polygon)]),
        ):
            _, metrics = work_area_service.validate_boundary(boundary)
            self.assertAlmostEqual(metrics["area_m2"], 2500.0, delta=0.5)

    def test_unclosed_ring_is_closed(self):
        polygon = support.rect(0, 0, 50, 50)
        polygon["coordinates"][0] = polygon["coordinates"][0][:-1]
        geometry, _ = work_area_service.validate_boundary(polygon)
        ring = geometry["coordinates"][0]
        self.assertEqual(ring[0], ring[-1])

    def test_projected_crs_is_reprojected(self):
        ring = support.rect_utm(0, 0, 100, 100)
        upload = support.collection(
            "utm",
            [support.feature({"type": "Polygon", "coordinates": [ring]})],
            crs={"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32643"}},
        )
        geometry, metrics = work_area_service.validate_boundary(upload)
        self.assertAlmostEqual(metrics["area_m2"], 10000.0, delta=1.0)
        lon, lat = geometry["coordinates"][0][0]
        self.assertAlmostEqual(lon, support.ORIGIN_LON, delta=0.01)
        self.assertAlmostEqual(lat, support.ORIGIN_LAT, delta=0.01)

    def test_projected_coordinates_without_a_crs_are_rejected(self):
        ring = support.rect_utm(0, 0, 100, 100)
        with self.assertRaisesRegex(WorkAreaError, "longitude/latitude range.*'crs' member"):
            work_area_service.validate_boundary({"type": "Polygon", "coordinates": [ring]})

    def test_invalid_boundaries_are_rejected_with_a_reason(self):
        lon, lat = support.ORIGIN_LON, support.ORIGIN_LAT
        bowtie = {
            "type": "Polygon",
            "coordinates": [[[lon, lat], [lon + 0.001, lat + 0.001], [lon + 0.001, lat], [lon, lat + 0.001], [lon, lat]]],
        }
        flat = {"type": "Polygon", "coordinates": [[[lon, lat], [lon + 0.001, lat], [lon + 0.002, lat], [lon, lat]]]}
        cases = [
            (bowtie, "Edges must not cross"),
            (flat, "encloses no area"),
            ({"type": "Point", "coordinates": [lon, lat]}, "Polygon or MultiPolygon, not Point"),
            ({"type": "Polygon", "coordinates": []}, "no coordinates"),
            ({"type": "Polygon", "coordinates": [[[lon, lat], [lon, lat]]]}, "not a valid polygon"),
            (support.collection("two", [support.feature(support.rect(0, 0, 5, 5))] * 2), "exactly one polygon"),
            ("not geojson", "GeoJSON object"),
        ]
        for boundary, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(WorkAreaError, message):
                    work_area_service.validate_boundary(boundary)

    def test_a_declared_area_from_the_client_is_ignored(self):
        boundary = support.feature(support.rect(0, 0, 10, 10), area_m2=999999, declared_area_ha=50)
        _, metrics = work_area_service.validate_boundary(boundary)
        self.assertAlmostEqual(metrics["area_m2"], 100.0, delta=0.1)


# ------------------------------------------------------------- precedence
class ResolveContextPrecedenceTests(StoreCase):
    def context(self, user, config=settings):
        return assignment_service.resolve_context(user, config, self.store)

    def test_registry_assignment_when_no_work_area_is_active(self):
        context = self.context(ASHA)
        self.assertEqual(context.assignment_id, "ASGN-TEST-001")
        self.assertEqual(context.assignment["label"], "ASSIGNED")
        self.assertTrue(context.assignment["is_official"])

    def test_active_work_area_comes_first_and_is_labelled_self_declared(self):
        created = work_area_service.create_area(area_payload(), self.context(ASHA), self.store)
        context = self.context(ASHA)
        self.assertEqual(context.assignment_id, created["assignment_id"])
        self.assertEqual(context.assignment["label"], "SELF-DECLARED WORK AREA")
        self.assertFalse(context.assignment["is_official"])
        self.assertFalse(context.assignment["is_demo"])
        self.assertIn("not an official survey assignment", context.notes[0])
        self.assertAlmostEqual(context.assignment["area_m2"], 20000.0, delta=1.0)
        # The map is filtered to the work area's extent.
        lon_min, lat_min, lon_max, lat_max = context.bbox
        self.assertLess(lon_min, lon_max)
        self.assertEqual(context.boundary["type"], "Polygon")

    def test_activating_the_registry_assignment_switches_back(self):
        work_area_service.create_area(area_payload(), self.context(ASHA), self.store)
        work_area_service.activate("ASGN-TEST-001", self.context(ASHA), settings, self.store)
        self.assertEqual(self.context(ASHA).assignment_id, "ASGN-TEST-001")

    def test_demo_only_when_allowed_and_never_another_users_area(self):
        work_area_service.create_area(area_payload(), self.context(ASHA), self.store)
        demo = self.context(NEWCOMER)
        self.assertTrue(demo.assignment["is_demo"])
        self.assertEqual(demo.assignment["label"], "DEMO ASSIGNMENT")

        strict = replace(settings, allow_demo_assignment=False)
        none = self.context(NEWCOMER, strict)
        self.assertIsNone(none.assignment)
        self.assertEqual(none.notes, [assignment_service.NO_AREA_NOTE])

    def test_deleting_the_active_area_falls_back(self):
        created = work_area_service.create_area(area_payload(), self.context(NEWCOMER), self.store)
        self.assertEqual(self.context(NEWCOMER).assignment_id, created["assignment_id"])
        work_area_service.delete_area(created["assignment_id"], self.context(NEWCOMER), self.store)
        self.assertTrue(self.context(NEWCOMER).assignment["is_demo"])

    def test_govt_surveyor_id_is_reported_as_self_declared(self):
        surveyor = self.context(ASHA).surveyor
        self.assertEqual(surveyor["govt_surveyor_id"], "UP/SRV/0412")
        self.assertEqual(surveyor["govt_surveyor_id_status"], "SELF-DECLARED")
        self.assertTrue(surveyor["profile_complete"])
        self.assertFalse(self.context(RAVI).surveyor["profile_complete"])


# ------------------------------------------------------------------ store
class OwnershipAndAuditTests(StoreCase):
    def context(self, user):
        return assignment_service.resolve_context(user, settings, self.store)

    def test_full_lifecycle_is_audited(self):
        created = work_area_service.create_area(area_payload(activate=False), self.context(RAVI), self.store)
        area_id = created["assignment_id"]
        self.assertFalse(created["is_active"])
        work_area_service.update_area(
            area_id, {"name": "South fields", "boundary": support.rect(0, 0, 100, 100)}, self.context(RAVI), self.store
        )
        work_area_service.activate(area_id, self.context(RAVI), settings, self.store)
        work_area_service.delete_area(area_id, self.context(RAVI), self.store)

        events = list(reversed(self.store.list_audit(entity_id=area_id)))
        self.assertEqual(
            [e["action"] for e in events],
            ["work_area.create", "work_area.update", "work_area.activate", "work_area.delete"],
        )
        update = events[1]
        self.assertEqual(update["before"]["name"], "North fields")
        self.assertEqual(update["after"]["name"], "South fields")
        self.assertAlmostEqual(update["after"]["area_m2"], 10000.0, delta=1.0)
        self.assertEqual(events[3]["after"], None)
        self.assertTrue(all(e["actor_email"] == RAVI.email for e in events))

    def test_another_users_area_is_not_found(self):
        created = work_area_service.create_area(area_payload(), self.context(ASHA), self.store)
        area_id = created["assignment_id"]
        for action in (
            lambda: work_area_service.update_area(area_id, {"name": "Taken"}, self.context(RAVI), self.store),
            lambda: work_area_service.delete_area(area_id, self.context(RAVI), self.store),
            lambda: work_area_service.activate(area_id, self.context(RAVI), settings, self.store),
        ):
            with self.assertRaises(WorkAreaError) as caught:
                action()
            self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(self.store.get_work_area(area_id)["name"], "North fields")
        self.assertEqual(work_area_service.list_areas(self.context(RAVI), settings, self.store)["items"], [])

    def test_only_one_area_is_active_per_owner(self):
        first = work_area_service.create_area(area_payload(name="A"), self.context(RAVI), self.store)
        second = work_area_service.create_area(area_payload(name="B"), self.context(RAVI), self.store)
        areas = {a["area_id"]: a["is_active"] for a in self.store.list_work_areas(RAVI.email)}
        self.assertEqual(areas, {first["assignment_id"]: False, second["assignment_id"]: True})

    def test_registry_assignments_cannot_be_edited_or_deleted(self):
        for action in (
            lambda: work_area_service.update_area("ASGN-TEST-001", {"name": "x"}, self.context(ASHA), self.store),
            lambda: work_area_service.delete_area("ASGN-TEST-001", self.context(ASHA), self.store),
        ):
            with self.assertRaises(WorkAreaError) as caught:
                action()
            self.assertEqual(caught.exception.status_code, 404)

    def test_someone_elses_registry_assignment_cannot_be_activated(self):
        with self.assertRaises(WorkAreaError) as caught:
            work_area_service.activate("ASGN-TEST-001", self.context(RAVI), settings, self.store)
        self.assertEqual(caught.exception.status_code, 404)

    def test_field_validation(self):
        context = self.context(RAVI)
        with self.assertRaisesRegex(WorkAreaError, "name"):
            work_area_service.create_area(area_payload(name="  "), context, self.store)
        with self.assertRaisesRegex(WorkAreaError, "longer than"):
            work_area_service.create_area(area_payload(village="x" * 200), context, self.store)
        with self.assertRaisesRegex(WorkAreaError, "origin"):
            work_area_service.create_area(area_payload(origin="guessed"), context, self.store)
        self.assertEqual(self.store.list_work_areas(RAVI.email), [])


# -------------------------------------------------------------------- API
class WorkAreaApiTests(ApiCase):
    """Two signed-in users through the HTTP API; identity only from the token."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.multiple(
            settings, auth_mode="supabase", supabase_url="https://project.supabase.test", supabase_key="anon"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.clear_cache()
        self.addCleanup(auth.clear_cache)
        users = {"Bearer asha": ASHA, "Bearer ravi": RAVI}
        verify = mock.patch.object(auth, "verify_token", side_effect=lambda token, _s: users[f"Bearer {token}"])
        verify.start()
        self.addCleanup(verify.stop)

    def call(self, method, path, who, **kwargs):
        return self.client.request(method, path, headers={"Authorization": f"Bearer {who}"}, **kwargs)

    def test_create_list_activate_edit_delete(self):
        created = self.call("POST", "/api/assignments", "ravi", json=area_payload(owner_email=ASHA.email))
        self.assertEqual(created.status_code, 201, created.text)
        area = created.json()
        area_id = area["assignment_id"]
        self.assertEqual(area["label"], "SELF-DECLARED WORK AREA")
        self.assertAlmostEqual(area["area_m2"], 20000.0, delta=1.0)
        # The owner is the token's account, whatever the body says.
        self.assertEqual(self.store.get_work_area(area_id)["owner_email"], RAVI.email)

        listing = self.call("GET", "/api/assignments", "ravi").json()
        self.assertEqual([i["assignment_id"] for i in listing["items"]], [area_id])
        self.assertTrue(listing["items"][0]["is_current"])

        current = self.call("GET", "/api/assignments/current", "ravi").json()
        self.assertEqual(current["assignment"]["assignment_id"], area_id)
        boundary = self.call("GET", "/api/map/assigned-area", "ravi").json()
        self.assertEqual(boundary["features"][0]["properties"]["label"], "SELF-DECLARED WORK AREA")

        edited = self.call("PATCH", f"/api/assignments/{area_id}", "ravi", json={"village": "Bisrakh"})
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(edited.json()["village"], "Bisrakh")

        self.assertEqual(self.call("POST", f"/api/assignments/{area_id}/activate", "ravi").status_code, 200)
        self.assertEqual(self.call("DELETE", f"/api/assignments/{area_id}", "ravi").status_code, 200)
        self.assertEqual(self.call("GET", "/api/assignments", "ravi").json()["items"], [])

    def test_ownership_isolation(self):
        area_id = self.call("POST", "/api/assignments", "asha", json=area_payload()).json()["assignment_id"]
        for method, path, body in (
            ("PATCH", f"/api/assignments/{area_id}", {"name": "mine now"}),
            ("DELETE", f"/api/assignments/{area_id}", None),
            ("POST", f"/api/assignments/{area_id}/activate", None),
        ):
            response = self.call(method, path, "ravi", json=body)
            self.assertEqual(response.status_code, 404, (method, response.text))
        ravi = self.call("GET", "/api/assignments", "ravi").json()
        self.assertNotIn(area_id, [i["assignment_id"] for i in ravi["items"]])
        self.assertNotEqual(ravi["current_id"], area_id)

        asha = self.call("GET", "/api/assignments", "asha").json()
        self.assertEqual(
            [(i["assignment_id"], i["kind"]) for i in asha["items"]],
            [("ASGN-TEST-001", "registry"), (area_id, "work_area")],
        )

    def test_measure_reports_the_server_measurement_and_stores_nothing(self):
        response = self.call("POST", "/api/assignments/measure", "ravi", json={"boundary": support.rect(0, 0, 300, 100)})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertAlmostEqual(body["area_m2"], 30000.0, delta=1.0)
        self.assertEqual(body["area_ha"], 3.0)
        self.assertEqual(body["notes"], [])
        # A box in longitude/latitude, as "use the current map view" sends.
        view = bbox_polygon(77.62, 28.55, 77.63, 28.56)
        noted = self.call("POST", "/api/assignments/measure", "ravi", json={"boundary": view}).json()
        self.assertTrue(any("axis-aligned rectangle" in note for note in noted["notes"]))
        self.assertEqual(self.store.list_work_areas(RAVI.email), [])
        bad = self.call("POST", "/api/assignments/measure", "ravi", json={"boundary": None})
        self.assertEqual(bad.status_code, 400)

    def test_invalid_geometry_is_a_400_with_the_reason(self):
        response = self.call(
            "POST", "/api/assignments", "ravi", json=area_payload(boundary={"type": "Point", "coordinates": [77, 28]})
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Polygon or MultiPolygon", response.json()["detail"])

    def test_jobs_and_reviews_follow_the_active_area(self):
        area_id = self.call("POST", "/api/assignments", "ravi", json=area_payload()).json()["assignment_id"]
        review = self.call("POST", "/api/reviews", "ravi", json={"feature_id": "CAND-000001", "action": "approve"})
        self.assertEqual(review.status_code, 200, review.text)
        self.assertEqual(self.store.list_reviews()[0]["assignment_id"], area_id)


if __name__ == "__main__":
    unittest.main()
