"""Finding the three required files under download names, and refusing to guess."""

import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from backend.config import (
    LANDCOVER_FILE_NAME,
    MODEL_FILE_NAME,
    PARCELS_FILE_NAME,
    resolve_file,
    settings,
)
from backend.core import runtime
from backend.scripts import prepare_data
from backend.tests import support
from backend.tests.apicase import ApiCase


def touch(path: Path, content: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


class TempDirCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)


class ResolveFileTests(TempDirCase):
    def test_exact_name_is_used(self):
        expected = touch(self.dir / PARCELS_FILE_NAME)
        touch(self.dir / "candidate_parcels (2).geojson")
        resolution = resolve_file(expected)
        self.assertEqual(resolution.status, "found")
        self.assertEqual(resolution.path, expected)
        self.assertIsNone(resolution.describe()["message"])

    def test_single_file_of_the_same_type_is_the_fallback(self):
        download = touch(self.dir / "candidate_parcels (2).geojson")
        touch(self.dir / ".gitkeep")
        touch(self.dir / "notes.txt")
        resolution = resolve_file(self.dir / PARCELS_FILE_NAME)
        self.assertEqual(resolution.status, "fallback")
        self.assertEqual(resolution.path, download)
        described = resolution.describe()
        self.assertEqual(described["used"], "candidate_parcels (2).geojson")
        self.assertIn(PARCELS_FILE_NAME, described["message"])

    def test_suffix_is_matched_without_regard_to_case(self):
        download = touch(self.dir / "best_weighted_multiclass_unet(2).PTH")
        self.assertEqual(resolve_file(self.dir / MODEL_FILE_NAME).path, download)

    def test_several_candidates_are_ambiguous_and_nothing_is_used(self):
        touch(self.dir / "candidate_parcels (1).geojson")
        touch(self.dir / "candidate_parcels (2).geojson")
        resolution = resolve_file(self.dir / PARCELS_FILE_NAME)
        self.assertEqual(resolution.status, "ambiguous")
        self.assertIsNone(resolution.path)
        self.assertFalse(resolution.usable)
        message = resolution.describe()["message"]
        self.assertIn("candidate_parcels (1).geojson", message)
        self.assertIn("candidate_parcels (2).geojson", message)

    def test_empty_or_absent_folder_is_missing(self):
        touch(self.dir / "parcels" / ".gitkeep")
        for folder in (self.dir / "parcels", self.dir / "absent"):
            resolution = resolve_file(folder / PARCELS_FILE_NAME)
            self.assertEqual(resolution.status, "missing")
            self.assertIn("is missing", resolution.describe()["message"])

    def test_messages_do_not_expose_server_paths(self):
        touch(self.dir / "a.geojson")
        touch(self.dir / "b.geojson")
        described = resolve_file(self.dir / PARCELS_FILE_NAME).describe()
        self.assertNotIn(str(self.dir), str(described))


class SettingsFallbackTests(TempDirCase):
    def test_settings_paths_follow_the_fallback(self):
        parcels = touch(self.dir / "parcels" / "candidate_parcels (2).geojson")
        touch(self.dir / "landcover" / "a.geojson")
        touch(self.dir / "landcover" / "b.geojson")
        model = touch(self.dir / "models" / "best_weighted_multiclass_unet(2).pth")
        config = replace(
            settings, data_dir=self.dir, configured_model_path=self.dir / "models" / MODEL_FILE_NAME
        )
        self.assertEqual(config.parcels_file, parcels)
        self.assertEqual(config.model_path, model)
        # Ambiguous: the expected path, which does not exist, so the layer is unavailable.
        self.assertEqual(config.landcover_file, self.dir / "landcover" / LANDCOVER_FILE_NAME)
        self.assertFalse(config.landcover_file.exists())

    def test_runtime_reports_files_and_reasons(self):
        touch(self.dir / "parcels" / "candidate_parcels (2).geojson")
        touch(self.dir / "landcover" / "a.geojson")
        touch(self.dir / "landcover" / "b.geojson")
        with mock.patch.object(settings, "data_dir", self.dir):
            files = runtime.data_files()
            self.assertEqual(files["parcels"]["status"], "fallback")
            self.assertEqual(files["landcover"]["status"], "ambiguous")
            self.assertEqual(files["landcover"]["candidates"], ["a.geojson", "b.geojson"])
            self.assertIn("Several .geojson files", runtime.unavailable_reason("landcover"))
            self.assertEqual(
                runtime.existing_layer_files()["parcels"].name, "candidate_parcels (2).geojson"
            )


class ApiReportsAmbiguityTests(ApiCase):
    """The status endpoint and the layer control name the problem file."""

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        data = Path(tmp.name)
        support.write_fixture_data(data)
        parcels = data / "parcels" / PARCELS_FILE_NAME
        shutil.copy(parcels, data / "parcels" / "candidate_parcels (1).geojson")
        parcels.rename(data / "parcels" / "candidate_parcels (2).geojson")
        landcover = data / "landcover" / LANDCOVER_FILE_NAME
        landcover.rename(data / "landcover" / "uplarshi_landcover_with_attributes (1).geojson")
        patcher = mock.patch.object(settings, "data_dir", data)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Re-index from the real fixture names for the tests that follow.
        self.addCleanup(runtime.ensure_source, runtime.EXISTING_SOURCE)

    def test_status_and_catalogue(self):
        status = self.ok("/api/system/status")
        files = status["data_files"]
        self.assertEqual(files["parcels"]["status"], "ambiguous")
        self.assertEqual(
            files["parcels"]["candidates"],
            ["candidate_parcels (1).geojson", "candidate_parcels (2).geojson"],
        )
        self.assertEqual(files["landcover"]["status"], "fallback")
        self.assertIn("checkpoint_file_status", status["model"])

        catalogue = self.ok("/api/map/layers")
        layers = {layer["key"]: layer for layer in catalogue["layers"]}
        self.assertFalse(layers["parcels"]["available"])
        self.assertIn("candidate_parcels (1).geojson", layers["parcels"]["message"])
        self.assertIn(PARCELS_FILE_NAME, layers["parcels"]["message"])
        # The single land-cover download is used.
        self.assertTrue(layers["building"]["available"])
        self.assertEqual(catalogue["data_files"]["landcover"]["used"],
                         "uplarshi_landcover_with_attributes (1).geojson")

        datasets = self.ok("/api/datasets")
        ai = next(c for c in datasets["categories"] if c["key"] == "ai_layers")
        self.assertTrue(any("Several .geojson files" in note for note in ai["notes"]))


class ImportFromFolderTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.source = self.dir / "Downloads"
        self.target = self.dir / "backend"
        self.config = replace(
            settings,
            data_dir=self.target / "data",
            configured_model_path=self.target / "models" / MODEL_FILE_NAME,
        )

    def run_import(self):
        return {r.label: r for r in prepare_data.import_files(self.source, self.config)}

    def test_download_names_are_copied_under_clean_names(self):
        touch(self.source / "candidate_parcels (2).geojson", b"parcels")
        touch(self.source / "best_weighted_multiclass_unet(2).pth", b"weights")
        touch(self.source / "uplarshi_landcover_with_attributes (1).geojson", b"landcover")
        touch(self.source / "unrelated.geojson", b"other")
        results = self.run_import()
        self.assertEqual({r.status for r in results.values()}, {"copied"})
        self.assertEqual((self.target / "data" / "parcels" / PARCELS_FILE_NAME).read_bytes(), b"parcels")
        self.assertEqual((self.target / "models" / MODEL_FILE_NAME).read_bytes(), b"weights")
        self.assertEqual(
            (self.target / "data" / "landcover" / LANDCOVER_FILE_NAME).read_bytes(), b"landcover"
        )
        self.assertIn("candidate_parcels (2).geojson", results["candidate parcels"].line())
        # A second run finds identical files and copies nothing.
        self.assertEqual({r.status for r in self.run_import().values()}, {"unchanged"})

    def test_clean_name_wins_and_identical_copies_are_not_ambiguous(self):
        touch(self.source / PARCELS_FILE_NAME, b"clean")
        touch(self.source / "candidate_parcels (1).geojson", b"older")
        touch(self.source / "uplarshi_landcover_with_attributes (1).geojson", b"same")
        touch(self.source / "uplarshi_landcover_with_attributes (2).geojson", b"same")
        results = self.run_import()
        self.assertEqual(results["candidate parcels"].source.name, PARCELS_FILE_NAME)
        self.assertEqual(results["land-cover layer"].status, "copied")

    def test_differing_copies_are_reported_not_guessed(self):
        touch(self.source / "uplarshi_landcover_with_attributes (1).geojson", b"one")
        touch(self.source / "uplarshi_landcover_with_attributes (2).geojson", b"two")
        results = self.run_import()
        landcover = results["land-cover layer"]
        self.assertEqual(landcover.status, "ambiguous")
        self.assertIn("several different files", landcover.line())
        self.assertFalse((self.target / "data" / "landcover" / LANDCOVER_FILE_NAME).exists())
        self.assertEqual(results["model checkpoint"].status, "not_found")
        self.assertIn("NOT FOUND", results["model checkpoint"].line())

    def test_command_line_exit_code(self):
        touch(self.source / "candidate_parcels (2).geojson", b"parcels")
        with mock.patch.object(prepare_data, "settings", self.config), \
                mock.patch.object(prepare_data, "build_index"), \
                mock.patch("builtins.print"):
            self.assertEqual(prepare_data.main(["--from", str(self.source)]), 1)
            self.assertEqual(prepare_data.main(["--from", str(self.dir / "absent")]), 2)


if __name__ == "__main__":
    unittest.main()
