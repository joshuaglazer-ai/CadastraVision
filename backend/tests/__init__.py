"""Backend tests.

Importing this package points the application at a throw-away data
directory (see ``_env``) before any backend module reads its settings, so
the suite never touches real survey data, reviews or processing jobs.

Run from the repository root:

    python -m pytest backend/tests
    python -m unittest discover -s backend/tests -t .
"""

from backend.tests import _env  # noqa: F401  (must run before backend.config is imported)
