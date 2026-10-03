"""The HTTP API, end to end, over the synthetic fixture layers."""

import csv
import io
import unittest
from unittest import mock

from backend.config import settings
from backend.core import auth
from backend.core.auth import AuthError, AuthUser
from backend.tests import support
from backend.tests.apicase import ApiCase


class SystemTests(ApiCase):
    def test_health_and_root(self):
        self.assertEqual(self.ok("/health")["status"], "ok")
        root = self.ok("/")
        self.assertEqual(root["principle"], "AI proposes. GIS validates. Surveyors verify.")

    def test_status_reports_the_model_honestly(self):
        status = self.ok("/api/system/status")
        model = status["model"]
        self.assertEqual(model["encoder"], "resnet34")
        self.assertEqual([c["name"] for c in model["classes"]],
                         ["Background", "Field", "Building", "Road", "Water", "Other"])
        self.assertIn("generalisation_note", model)
        self.assertIsInstance(model["checkpoint_present"], bool)
        self.assertIsInstance(model["runtime_available"], bool)
        if not model["runtime_available"]:
            self.assertTrue(model["runtime_error"])
        self.assertEqual(status["indexing_errors"], {})


class AssignmentApiTests(ApiCase):
    def test_profile_and_assignment(self):
        me = self.ok("/api/surveyors/me")
        self.assertTrue(me["surveyor"]["surveyor_id"].startswith("SRV-"))
        self.assertTrue(me["surveyor"]["is_dev_session"])
        for field in ("assignment_id", "district", "taluk", "village", "assignment_status", "area_ha"):
            self.assertIn(field, me["assignment"])
        self.assertTrue(me["assignment"]["is_demo"])

        current = self.ok("/api/assignments/current")
        self.assertEqual(current["assignment"]["assignment_id"], me["assignment"]["assignment_id"])

    def test_boundary_is_geojson(self):
        for path in ("/api/assignments/current/boundary", "/api/map/assigned-area"):
            boundary = self.ok(path)
            self.assertEqual(boundary["type"], "FeatureCollection")
            self.assertEqual(boundary["features"][0]["geometry"]["type"], "Polygon")


class AuthenticatedApiTests(ApiCase):
    """With authentication on, identity comes only from the verified token."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.multiple(
            settings, auth_mode="supabase", supabase_url="https://project.supabase.test", supabase_key="anon"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        auth.clear_cache()
        self.addCleanup(auth.clear_cache)

    def as_surveyor(self):
        user = AuthUser(user_id="uid-1", email=support.SURVEYOR_EMAIL, name="Asha Rao")
        return mock.patch.object(auth, "verify_token", return_value=user)

    def test_requests_without_a_session_are_rejected(self):
        for path in ("/api/surveyors/me", "/api/map/features", "/api/reviews", "/api/export/geojson", "/stats"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 401, path)
        self.assertEqual(self.client.post("/api/reviews", json={"feature_id": "X", "action": "approve"}).status_code, 401)
        # Liveness stays public.
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_invalid_session_is_rejected(self):
        with mock.patch.object(auth, "verify_token", side_effect=AuthError("Session is invalid or has expired. Sign in again.")):
            response = self.client.get("/api/surveyors/me", headers={"Authorization": "Bearer stale"})
        self.assertEqual(response.status_code, 401)

    def test_surveyor_sees_their_own_assignment(self):
        with self.as_surveyor():
            me = self.client.get("/api/surveyors/me", headers={"Authorization": "Bearer good"}).json()
        self.assertEqual(me["surveyor"]["surveyor_id"], "SRV-TEST-7")
        self.assertEqual(me["surveyor"]["name"], "Asha Rao")
        self.assertEqual(me["assignment"]["assignment_id"], "ASGN-TEST-001")
        self.assertFalse(me["assignment"]["is_demo"])

    def test_surveyor_id_in_the_request_body_is_ignored(self):
        with self.as_surveyor():
            response = self.client.post(
                "/api/reviews",
                headers={"Authorization": "Bearer good"},
                json={"feature_id": "CAND-000001", "action": "approve", "surveyor_id": "SRV-SOMEONE-ELSE"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["surveyor_id"], "SRV-TEST-7")
        self.assertEqual(self.store.list_reviews()[0]["surveyor_id"], "SRV-TEST-7")


class MapApiTests(ApiCase):
    def test_layer_catalogue_marks_missing_data_as_unavailable(self):
        catalogue = self.ok("/api/map/layers")
        layers = {layer["key"]: layer for layer in catalogue["layers"]}
        for key in ("assigned_area", "parcels", "building", "road", "field", "water", "other"):
            self.assertTrue(layers[key]["available"], key)
        self.assertEqual(layers["parcels"]["count"], len(support.PARCELS))
        self.assertEqual(layers["building"]["count"], 2)
        for key in ("dsm", "dtm"):
            self.assertFalse(layers[key]["available"])
            self.assertIn("unavailable", layers[key]["message"].lower())
        self.assertFalse(layers["existing_gis"]["available"])

    def test_parcels_are_labelled_and_generalised_for_the_zoom(self):
        parcels = self.ok("/api/map/parcels", zoom=19)
        self.assertEqual(parcels["type"], "FeatureCollection")
        ids = [feature["id"] for feature in parcels["features"]]
        self.assertIn("CAND-000001", ids)
        self.assertNotIn("CAND-000003", ids)  # 0.25 m2 fragment is not drawn at this zoom
        first = parcels["features"][0]["properties"]
        self.assertEqual(first["verification_status"], "AI_GENERATED")
        self.assertIn("review_priority", first)

    def test_features_filter_by_class_and_box(self):
        buildings = self.ok("/api/map/features", zoom=21, classes="Building")
        self.assertEqual({f["properties"]["class_name"] for f in buildings["features"]}, {"Building"})
        self.assertEqual(buildings["total_matching"], 2)

        elsewhere = self.ok("/api/map/features", zoom=18, bbox="10,10,11,11")
        self.assertEqual(elsewhere["features"], [])

        self.assertEqual(self.get("/api/map/features", bbox="not,a,box").status_code, 400)

    def test_feature_detail_and_parcel_reasoning(self):
        detail = self.ok("/api/map/features/CAND-000001", reasoning="true")
        properties = detail["properties"]
        self.assertAlmostEqual(properties["area_m2"], 1200.0, delta=0.05)
        self.assertEqual(properties["verification_status"], "AI_GENERATED")
        self.assertIsNone(properties["confidence"])

        reasoning = detail["reasoning"]
        self.assertEqual(reasoning["scenario"], "candidate")
        self.assertEqual(reasoning["building_count"], 1)
        self.assertAlmostEqual(reasoning["building_area_m2"], 80.0, delta=0.5)
        composition = {item["class_key"]: item for item in reasoning["composition"]}
        self.assertIn("field", composition)
        self.assertIn("building", composition)

        self.assertEqual(self.get("/api/map/features/NOPE-1").status_code, 404)
        self.assertEqual(self.ok("/api/parcels/CAND-000002")["id"], "CAND-000002")

    def test_unknown_source_is_a_404(self):
        self.assertEqual(self.get("/api/map/layers", source="job:JOB-NOPE").status_code, 404)

    def test_terrain_is_reported_as_missing_not_invented(self):
        status = self.ok("/api/terrain/status")
        self.assertFalse(status["heights_available"])
        self.assertEqual(status["message"], "DSM/DTM data required for measured terrain and building height.")
        buildings = self.ok("/api/terrain/buildings")
        self.assertFalse(buildings["available"])
        self.assertEqual(buildings["buildings"], [])
        self.assertEqual(self.get("/api/terrain/grid", surface="dtm", bbox="77.62,28.55,77.63,28.56").status_code, 404)


class ReviewApiTests(ApiCase):
    def counts(self):
        return self.ok("/api/reviews/queue", group="high", limit=1)["counts"]

    def test_queue_is_built_from_measured_signals(self):
        queue = self.ok("/api/reviews/queue", group="medium")
        self.assertEqual(queue["counts"]["medium"], 1)
        item = queue["items"][0]
        self.assertEqual(item["feature_id"], "CAND-000004")
        self.assertEqual(item["issue"], "No road detected within 5 m")
        self.assertEqual(item["status"], "REVIEW_REQUIRED")
        self.assertFalse(item["uncertainty"]["available"])
        self.assertEqual(len(item["bbox"]), 4)
        self.assertEqual(self.get("/api/reviews/queue", group="bogus").status_code, 400)

    def test_approve_moves_the_feature_to_verified_and_persists(self):
        before = self.counts()
        response = self.review(feature_id="CAND-000004", action="approve", comment="Access is by a footpath")
        self.assertEqual(response.status_code, 200, response.text)
        review = response.json()
        self.assertEqual(review["verification_status"], "SURVEYOR_VERIFIED")
        self.assertEqual(review["feature_status"], "SURVEYOR_VERIFIED")
        self.assertTrue(review["review_id"])

        after = self.counts()
        self.assertEqual(after["medium"], before["medium"] - 1)
        self.assertEqual(after["verified"], before["verified"] + 1)

        detail = self.ok("/api/map/features/CAND-000004")
        self.assertEqual(detail["properties"]["verification_status"], "SURVEYOR_VERIFIED")
        self.assertEqual(len(detail["reviews"]), 1)

    def test_flag_and_reject_need_a_reason(self):
        self.assertEqual(self.review(feature_id="CAND-000001", action="flag").status_code, 400)
        self.assertEqual(self.review(feature_id="CAND-000001", action="reject", comment="  ").status_code, 400)
        flagged = self.review(feature_id="CAND-000001", action="flag", comment="Two fields merged")
        self.assertEqual(flagged.json()["verification_status"], "FLAGGED")
        queue = self.ok("/api/reviews/queue", group="flagged")
        self.assertEqual([item["feature_id"] for item in queue["items"]], ["CAND-000001"])

    def test_invalid_requests(self):
        self.assertEqual(self.review(feature_id="CAND-000001", action="bless").status_code, 400)
        self.assertEqual(self.review(feature_id="NOPE-1", action="approve").status_code, 404)
        self.assertEqual(self.review(feature_id="CAND-000001", action="approve", source="job:NOPE").status_code, 404)
        self.assertEqual(self.review(feature_id="CAND-000001", action="edit").status_code, 400)

    def test_edit_keeps_the_ai_geometry_and_records_the_correction(self):
        corrected = support.rect(0, 0, 42, 30)
        response = self.review(
            feature_id="CAND-000001", action="edit", comment="East edge moved to the fence",
            edited_geometry=corrected,
        )
        self.assertEqual(response.status_code, 200, response.text)
        review = response.json()
        self.assertEqual(review["verification_status"], "EDITED")
        self.assertAlmostEqual(review["edited_metrics"]["area_m2"], 1260.0, delta=0.1)
        self.assertAlmostEqual(review["original_area_m2"], 1200.0, delta=0.1)
        self.assertEqual(review["original_geometry"]["type"], "Polygon")

        detail = self.ok("/api/map/features/CAND-000001", geometry="detail")
        self.assertEqual(detail["properties"]["verification_status"], "EDITED")
        self.assertEqual(detail["geometry"]["coordinates"], corrected["coordinates"])
        self.assertIsNotNone(detail["ai_geometry"])

        degenerate = {"type": "Polygon", "coordinates": [[[77.6, 28.5], [77.6, 28.5]]]}
        self.assertEqual(
            self.review(feature_id="CAND-000001", action="edit", edited_geometry=degenerate).status_code, 400
        )

    def test_ground_truth_is_kept_apart_from_the_prediction(self):
        response = self.review(
            feature_id="BLD-000004", action="add_ground_truth", comment="Tin roof, single storey",
            ground_truth={"observed_class": "Building", "latitude": 28.5591, "longitude": 77.6251,
                          "accuracy_m": 0.03, "device": "GNSS rover"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        truth = response.json()["ground_truth"]
        self.assertEqual(truth["observed_class"], "Building")
        self.assertEqual(truth["predicted_class"], "Building")
        self.assertEqual(response.json()["feature_status"], "SURVEYOR_REVIEWED")

        self.assertEqual(self.review(feature_id="BLD-000004", action="add_ground_truth", ground_truth={}).status_code, 400)
        self.assertEqual(
            self.review(feature_id="BLD-000004", action="add_ground_truth", ground_truth={"latitude": 28.5}).status_code,
            400,
        )
        points = self.ok("/api/map/gnss")
        self.assertEqual(len(points["features"]), 1)

    def test_audit_trail_records_who_what_when_before_and_after(self):
        self.review(feature_id="CAND-000002", action="reject", comment="Shadow, not a field")
        review_id = self.ok("/api/reviews")["reviews"][0]["review_id"]
        patched = self.client.patch(f"/api/reviews/{review_id}", json={"comment": "Shadow of a tree line"})
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["comment"], "Shadow of a tree line")
        self.assertEqual(self.client.patch("/api/reviews/REV-NOPE", json={"comment": "x"}).status_code, 404)

        events = self.ok("/api/audit")["events"]
        actions = [event["action"] for event in events]
        self.assertEqual(actions[:2], ["review.update", "review.reject"])
        reject = events[1]
        self.assertTrue(reject["actor_id"].startswith("SRV-"))
        self.assertTrue(reject["at"])
        self.assertEqual(reject["before"]["verification_status"], "AI_GENERATED")
        self.assertEqual(reject["after"]["verification_status"], "REJECTED")
        self.assertEqual(reject["reason"], "Shadow, not a field")


class AnalyticsAndExportTests(ApiCase):
    def test_analytics_match_the_data(self):
        analytics = self.ok("/api/analytics")
        self.assertEqual(analytics["candidate_parcels"], len(support.PARCELS))
        self.assertEqual(analytics["ai_features"], len(support.LANDCOVER))
        self.assertEqual(analytics["buildings"]["count"], 2)
        self.assertAlmostEqual(analytics["buildings"]["area_m2"], 116.0, delta=0.05)
        self.assertEqual(analytics["roads"]["count"], 1)
        self.assertAlmostEqual(analytics["water"]["area_m2"], 100.0, delta=0.05)
        self.assertEqual(analytics["parcels"]["no_road_access"], 1)
        self.assertEqual(analytics["review"]["counts"]["medium"], 1)
        self.assertFalse(analytics["uncertainty"]["available"])
        self.assertIsNone(analytics["uncertainty"]["mean_confidence"])
        self.assertEqual(analytics["processing"]["state"], "IDLE")
        self.assertEqual(analytics["label"], "AI GENERATED / PRELIMINARY")

    def test_geojson_export_carries_metadata_and_status(self):
        self.review(feature_id="CAND-000001", action="approve")
        response = self.get("/api/export/geojson")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["content-disposition"])
        data = response.json()
        self.assertEqual(data["type"], "FeatureCollection")
        self.assertEqual(len(data["features"]), len(support.PARCELS))

        metadata = data["metadata"]
        for key in ("project", "assignment", "surveyor", "model", "exported_at", "record_status",
                    "disclaimer", "legal_notice", "verification"):
            self.assertIn(key, metadata)
        self.assertIn("not legal cadastral ownership records", metadata["disclaimer"])
        self.assertEqual(metadata["verification"]["verified_features"], 1)

        by_id = {feature["id"]: feature["properties"] for feature in data["features"]}
        self.assertEqual(by_id["CAND-000001"]["verification_status"], "SURVEYOR_VERIFIED")
        self.assertEqual(by_id["CAND-000002"]["verification_status"], "AI_GENERATED")
        self.assertNotEqual(by_id["CAND-000001"]["status_label"], by_id["CAND-000002"]["status_label"])

        verified = self.ok("/api/export/geojson", status="verified")
        self.assertEqual([feature["id"] for feature in verified["features"]], ["CAND-000001"])
        unverified = self.ok("/api/export/geojson", status="unverified")
        self.assertEqual(len(unverified["features"]), len(support.PARCELS) - 1)

    def test_edited_geometry_is_what_gets_exported(self):
        corrected = support.rect(60, 0, 22, 20)
        self.review(feature_id="CAND-000002", action="edit", edited_geometry=corrected, comment="Widened")
        data = self.ok("/api/export/geojson", status="verified")
        feature = data["features"][0]
        self.assertEqual(feature["geometry"]["coordinates"], corrected["coordinates"])
        self.assertEqual(feature["properties"]["geometry_source"], "SURVEYOR_EDIT")

    def test_csv_export(self):
        response = self.get("/api/export/csv", layer="landcover")
        self.assertEqual(response.status_code, 200)
        lines = [line for line in response.text.splitlines() if not line.startswith("#")]
        rows = list(csv.DictReader(io.StringIO("\n".join(lines))))
        self.assertEqual(len(rows), len(support.LANDCOVER))
        self.assertIn("verification_status", rows[0])
        self.assertIn("area_m2", rows[0])
        self.assertIn("not legal cadastral ownership records", response.text)

    def test_export_rejects_unknown_options(self):
        self.assertEqual(self.get("/api/export/geojson", layer="secrets").status_code, 400)
        self.assertEqual(self.get("/api/export/geojson", status="official").status_code, 400)

    def test_export_is_audited(self):
        self.ok("/api/export/geojson")
        actions = [event["action"] for event in self.ok("/api/audit")["events"]]
        self.assertTrue(any(action.startswith("export") for action in actions))


class DatasetApiTests(ApiCase):
    def test_discovery_lists_every_source_and_says_what_is_missing(self):
        datasets = self.ok("/api/datasets")
        categories = {category["key"]: category for category in datasets["categories"]}
        self.assertEqual(
            set(categories),
            {"drone", "satellite", "dsm", "dtm", "ai_layers", "land_records", "gis",
             "survey_of_india", "documents", "gnss"},
        )
        for key in ("drone", "dsm", "dtm", "land_records"):
            self.assertEqual(categories[key]["status"], "NOT AVAILABLE", key)
            self.assertEqual(categories[key]["datasets"], [])
        self.assertEqual(categories["ai_layers"]["count"], 2)
        layer = categories["ai_layers"]["datasets"][0]
        for field in ("name", "status", "crs", "extent", "feature_count"):
            self.assertIn(field, layer)

    def test_gnss_points_can_be_uploaded_and_appear_on_the_map(self):
        body = b"name,latitude,longitude,accuracy_m\nGCP-1,28.5590,77.6250,0.02\nGCP-2,28.5600,77.6262,0.03\nbad,abc,77\n"
        response = self.client.post(
            "/api/datasets/upload", data={"source_type": "gnss"},
            files={"file": ("gcp points.csv", body, "text/csv")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        dataset = response.json()
        self.addCleanup(lambda: [p.unlink() for p in (settings.data_dir / "gnss").glob("*")])
        self.assertEqual(dataset["feature_count"], 2)
        self.assertNotIn(" ", dataset["name"])
        points = self.ok("/api/map/gnss")
        self.assertEqual(len(points["features"]), 2)
        self.assertEqual(self.ok(f"/api/datasets/{dataset['dataset_id']}")["name"], dataset["name"])

    def test_uploads_are_validated(self):
        def upload(source_type, name, body=b"x"):
            return self.client.post(
                "/api/datasets/upload", data={"source_type": source_type},
                files={"file": (name, body, "application/octet-stream")},
            )

        self.assertEqual(upload("gnss", "payload.exe").status_code, 400)       # wrong type
        self.assertEqual(upload("ai_layers", "a.geojson", b"{}").status_code, 400)  # not an upload target
        self.assertEqual(upload("nonsense", "a.csv").status_code, 400)
        self.assertEqual(upload("documents", "empty.pdf", b"").status_code, 400)

    def test_path_traversal_in_a_file_name_is_neutralised(self):
        response = self.client.post(
            "/api/datasets/upload", data={"source_type": "documents"},
            files={"file": ("../../evil.pdf", b"%PDF-1.4", "application/pdf")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        dataset = response.json()
        stored = list((settings.data_dir / "documents").glob("*"))
        self.addCleanup(lambda: [p.unlink() for p in stored])
        self.assertEqual(len(stored), 1)
        self.assertNotIn("..", dataset["name"])
        self.assertFalse((settings.data_dir.parent / "evil.pdf").exists())
        self.assertNotIn(str(settings.data_dir), str(dataset))  # no server paths in responses

    def test_unknown_dataset(self):
        self.assertEqual(self.get("/api/datasets/does-not-exist").status_code, 404)


class ExistingGisTests(ApiCase):
    """Parcel reasoning when an existing parcel layer is available."""

    def setUp(self):
        super().setUp()
        folder = settings.data_dir / "land_records"
        crs = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32643"}}
        records = [
            # A surveyed plot around the first field and its building, in UTM.
            support.feature(
                {"type": "Polygon", "coordinates": [support.rect_utm(-2, -1.5, 44, 33)]},
                plot_no="117/2", holder_ref="R-0042",
            ),
            support.feature({"type": "Point", "coordinates": support.rect_utm(10, 10, 1, 1)[0]}, kind="pillar"),
            # A plot where the model found nothing.
            support.feature({"type": "Polygon", "coordinates": [support.rect_utm(0, 120, 20, 20)]}, plot_no="118"),
        ]
        self.path = support.write_json(folder / "village_plots.geojson", support.collection("plots", records, crs=crs))
        self.addCleanup(self.path.unlink)

    def dataset(self):
        layers = {layer["key"]: layer for layer in self.ok("/api/map/layers")["layers"]}
        self.assertTrue(layers["existing_gis"]["available"])
        return layers["existing_gis"]["datasets"][0]

    def test_existing_layer_is_discovered_and_drawn_in_lonlat(self):
        dataset = self.dataset()
        self.assertEqual(dataset["name"], "village_plots.geojson")
        categories = {c["key"]: c for c in self.ok("/api/datasets")["categories"]}
        record = categories["land_records"]["datasets"][0]
        self.assertEqual(record["crs"], "EPSG:32643")
        self.assertEqual(record["status"], "AVAILABLE")

        overlay = self.ok(f"/api/datasets/{dataset['dataset_id']}/overlay")
        self.assertEqual([feature["id"] for feature in overlay["features"]], [0, 1, 2])
        lon, lat = overlay["features"][0]["geometry"]["coordinates"][0][0]
        self.assertAlmostEqual(lon, support.ORIGIN_LON, delta=0.001)
        self.assertAlmostEqual(lat, support.ORIGIN_LAT, delta=0.001)

    def test_ai_features_are_overlaid_on_the_existing_parcel(self):
        dataset = self.dataset()
        detail = self.ok(f"/api/datasets/{dataset['dataset_id']}/features/0")
        self.assertEqual(detail["record_label"], "EXISTING GIS RECORD")
        self.assertEqual(detail["properties"]["plot_no"], "117/2")
        self.assertAlmostEqual(detail["metrics"]["area_m2"], 44 * 33, delta=0.1)
        self.assertEqual(detail["metrics"]["metric_crs"], "EPSG:32643")

        reasoning = detail["reasoning"]
        self.assertTrue(reasoning["available"], reasoning.get("message"))
        self.assertEqual(reasoning["scenario"], "existing_gis")
        self.assertIn("Existing GIS parcel", reasoning["scenario_label"])
        self.assertEqual(reasoning["building_count"], 1)
        self.assertAlmostEqual(reasoning["building_area_m2"], 80.0, delta=0.5)
        composition = {entry["class_key"]: entry for entry in reasoning["composition"]}
        self.assertAlmostEqual(composition["field"]["area_m2"], 1200.0, delta=1.0)
        self.assertNotIn("water", composition)
        self.assertIn("not a legal cadastral ownership record", reasoning["disclaimer"])

    def test_parcel_with_no_ai_features_reports_none(self):
        dataset = self.dataset()
        reasoning = self.ok(f"/api/datasets/{dataset['dataset_id']}/features/2")["reasoning"]
        self.assertTrue(reasoning["available"])
        self.assertEqual(reasoning["composition"], [])
        self.assertEqual(reasoning["building_count"], 0)

    def test_non_polygon_and_unknown_records(self):
        dataset = self.dataset()
        point = self.ok(f"/api/datasets/{dataset['dataset_id']}/features/1")
        self.assertFalse(point["reasoning"]["available"])
        self.assertIsNone(point["metrics"])
        self.assertEqual(self.get(f"/api/datasets/{dataset['dataset_id']}/features/99").status_code, 404)
        self.assertEqual(self.get("/api/datasets/DS-NOPE/features/0").status_code, 404)


class LegacyEndpointTests(ApiCase):
    def test_original_endpoints_still_answer(self):
        stats = self.ok("/stats")
        self.assertEqual(stats["total_parcels"], len(support.PARCELS))
        self.assertAlmostEqual(stats["total_area_m2"], 2500.25, delta=0.1)

        parcels = self.ok("/parcels")
        self.assertEqual(parcels["total"], len(support.PARCELS))
        self.assertEqual(self.ok("/parcels/CAND-000001")["properties"]["parcel_id"], "CAND-000001")
        self.assertEqual(self.get("/parcels/NOPE").status_code, 404)
        self.assertEqual(self.ok("/parcels-geojson")["type"], "FeatureCollection")
        self.assertEqual(self.ok("/landcover-geojson")["type"], "FeatureCollection")

        summary = self.ok("/landcover-summary")
        self.assertEqual(summary["total_features"], len(support.LANDCOVER))
        self.assertEqual(summary["class_counts"]["Building"], 2)
        self.assertEqual(self.ok("/export/geojson")["type"], "FeatureCollection")


if __name__ == "__main__":
    unittest.main()
