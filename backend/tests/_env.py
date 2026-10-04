"""Isolate the test run: temporary data directory, authentication off.

The values are set in ``os.environ`` before ``backend.config`` is imported;
``python-dotenv`` does not override variables that are already set, so a
developer's ``backend/.env`` cannot leak into the tests.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(tempfile.mkdtemp(prefix="cadastra-tests-"))
DATA_DIR = ROOT / "data"
PROCESSING_DIR = ROOT / "processing"
MODELS_DIR = ROOT / "models"

os.environ["CADASTRA_DATA_DIR"] = str(DATA_DIR)
os.environ["CADASTRA_PROCESSING_DIR"] = str(PROCESSING_DIR)
# A stand-in default checkpoint (never loaded: the pipeline is mocked in the
# API tests), so starting a job does not depend on the real 98 MB file.
MODELS_DIR.mkdir(parents=True, exist_ok=True)
(MODELS_DIR / "best_weighted_multiclass_unet.pth").write_bytes(b"stand-in village checkpoint for tests")
os.environ["CADASTRA_MODEL_PATH"] = str(MODELS_DIR / "best_weighted_multiclass_unet.pth")
os.environ["CADASTRA_AUTH"] = "off"
os.environ["CADASTRA_ALLOW_DEMO_ASSIGNMENT"] = "true"
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_KEY"] = ""

atexit.register(shutil.rmtree, ROOT, ignore_errors=True)

from backend.tests import support  # noqa: E402

support.write_fixture_data(DATA_DIR)
