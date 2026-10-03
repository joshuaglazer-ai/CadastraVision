"""Shared base for tests that drive the HTTP API."""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

try:
    from starlette.testclient import TestClient

    from backend.main import app

    API_AVAILABLE = True
    API_SKIP_REASON = ""
except ImportError as exc:  # FastAPI / httpx not installed
    TestClient = None  # type: ignore[assignment]
    app = None
    API_AVAILABLE = False
    API_SKIP_REASON = f"FastAPI test client unavailable: {exc}"

from backend.core.store import Store, get_store, set_store


@unittest.skipUnless(API_AVAILABLE, API_SKIP_REASON)
class ApiCase(unittest.TestCase):
    """One running application per class, a fresh state database per test."""

    @classmethod
    def setUpClass(cls):
        logging.disable(logging.WARNING)
        cls._client_context = TestClient(app)
        cls.client = cls._client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._client_context.__exit__(None, None, None)
        logging.disable(logging.NOTSET)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        previous = get_store()
        self.store = Store(Path(self._tmp.name) / "state.db")
        set_store(self.store)
        self.addCleanup(set_store, previous)

    # ------------------------------------------------------------ helpers
    def get(self, path, **params):
        return self.client.get(path, params=params or None)

    def ok(self, path, **params):
        response = self.get(path, **params)
        self.assertEqual(response.status_code, 200, response.text[:400])
        return response.json()

    def review(self, **body):
        return self.client.post("/api/reviews", json=body)
