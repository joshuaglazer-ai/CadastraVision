"""Persistent state: processing jobs, surveyor reviews, audit trail, datasets.

SQLite from the standard library is used on purpose. It survives restarts,
needs no extra infrastructure, and the schema maps one-to-one onto Postgres
if the project later moves this state into Supabase.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY,
    surveyor_id     TEXT,
    surveyor_email  TEXT,
    assignment_id   TEXT,
    input_dataset   TEXT,
    input_path      TEXT,
    status          TEXT NOT NULL,
    stage           TEXT,
    progress        REAL NOT NULL DEFAULT 0,
    stages_json     TEXT,
    raster_meta_json TEXT,
    output_dataset  TEXT,
    summary_json    TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
    review_id       TEXT PRIMARY KEY,
    feature_id      TEXT NOT NULL,
    layer           TEXT NOT NULL,
    source          TEXT NOT NULL,
    surveyor_id     TEXT NOT NULL,
    surveyor_email  TEXT,
    assignment_id   TEXT,
    action          TEXT NOT NULL,
    comment         TEXT,
    verification_status TEXT NOT NULL,
    model_confidence REAL,
    model_entropy   REAL,
    original_geometry_json TEXT,
    edited_geometry_json   TEXT,
    ground_truth_json      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_feature ON reviews (source, feature_id, created_at);

CREATE TABLE IF NOT EXISTS audit_log (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    actor_id    TEXT NOT NULL,
    actor_email TEXT,
    action      TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    before_json TEXT,
    after_json  TEXT,
    reason      TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log (entity_type, entity_id, at);

CREATE TABLE IF NOT EXISTS datasets (
    dataset_id  TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    source_type TEXT NOT NULL,
    kind        TEXT NOT NULL,
    origin      TEXT NOT NULL,
    path        TEXT NOT NULL,
    meta_json   TEXT,
    surveyor_id TEXT,
    created_at  TEXT NOT NULL
);

-- Work areas a surveyor declares for themselves (drawn or uploaded in the
-- app). They are not official assignments; those live in the registry file.
CREATE TABLE IF NOT EXISTS work_areas (
    area_id           TEXT PRIMARY KEY,
    owner_email       TEXT NOT NULL,
    owner_surveyor_id TEXT NOT NULL,
    name              TEXT NOT NULL,
    state             TEXT,
    district          TEXT,
    taluk             TEXT,
    village           TEXT,
    geometry_json     TEXT NOT NULL,
    area_m2           REAL NOT NULL,
    origin            TEXT NOT NULL CHECK (origin IN ('drawn', 'uploaded')),
    is_active         INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_work_areas_owner ON work_areas (owner_email, created_at);

-- Which registry assignment a user chose, when they hold several and no
-- work area is active.
CREATE TABLE IF NOT EXISTS assignment_preferences (
    owner_email   TEXT PRIMARY KEY,
    assignment_id TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""

# Action taken by the surveyor -> resulting verification status.
ACTION_STATUS = {
    "approve": "SURVEYOR_VERIFIED",
    "edit": "EDITED",
    "flag": "FLAGGED",
    "reject": "REJECTED",
    "add_ground_truth": "SURVEYOR_REVIEWED",
}

VERIFICATION_STATUSES = (
    "AI_GENERATED",
    "REVIEW_REQUIRED",
    "SURVEYOR_REVIEWED",
    "SURVEYOR_VERIFIED",
    "EDITED",
    "FLAGGED",
    "REJECTED",
)

JOB_ACTIVE = ("QUEUED", "PROCESSING")

_JOB_JSON = {
    "stages_json": "stages",
    "raster_meta_json": "raster_meta",
    "summary_json": "summary",
    "area_check_json": "area_check",
}

# Columns added after the first release, created on databases that predate them.
_ADDED_COLUMNS = {
    "jobs": [("area_check_json", "TEXT")],
}
_REVIEW_JSON = {
    "original_geometry_json": "original_geometry",
    "edited_geometry_json": "edited_geometry",
    "ground_truth_json": "ground_truth",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: str | None) -> Any:
    if value in (None, ""):
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


class Store:
    """Thread-safe SQLite store. One short-lived connection per operation."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            for table, columns in _ADDED_COLUMNS.items():
                present = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                for name, kind in columns:
                    if name not in present:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ jobs
    @staticmethod
    def _job_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        job = dict(row)
        for column, key in _JOB_JSON.items():
            job[key] = _loads(job.pop(column, None))
        return job

    def create_job(
        self,
        *,
        surveyor_id: str | None,
        surveyor_email: str | None,
        assignment_id: str | None,
        input_dataset: str,
        input_path: str,
        stages: list[dict[str, Any]],
        raster_meta: dict[str, Any] | None,
        status: str = "UPLOADED",
        stage: str = "UPLOAD",
    ) -> dict[str, Any]:
        job_id = new_id("JOB")
        now = utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO jobs (job_id, surveyor_id, surveyor_email, assignment_id,
                       input_dataset, input_path, status, stage, progress, stages_json,
                       raster_meta_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)""",
                (
                    job_id,
                    surveyor_id,
                    surveyor_email,
                    assignment_id,
                    input_dataset,
                    input_path,
                    status,
                    stage,
                    _dumps(stages),
                    _dumps(raster_meta),
                    now,
                    now,
                ),
            )
        return self.get_job(job_id)  # type: ignore[return-value]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job_row(row)

    def update_job(self, job_id: str, **fields: Any) -> dict[str, Any]:
        if not fields:
            job = self.get_job(job_id)
            if job is None:
                raise KeyError(job_id)
            return job

        columns: dict[str, Any] = {}
        reverse = {key: column for column, key in _JOB_JSON.items()}
        for key, value in fields.items():
            if key in reverse:
                columns[reverse[key]] = _dumps(value)
            else:
                columns[key] = value
        columns["updated_at"] = utc_now()

        assignments = ", ".join(f"{column} = ?" for column in columns)
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE jobs SET {assignments} WHERE job_id = ?",
                (*columns.values(), job_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(job_id)
        return self.get_job(job_id)  # type: ignore[return-value]

    def list_jobs(self, surveyor_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT * FROM jobs"
        params: list[Any] = []
        if surveyor_id:
            query += " WHERE surveyor_id = ?"
            params.append(surveyor_id)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._job_row(row) for row in rows]  # type: ignore[misc]

    def fail_interrupted_jobs(self) -> int:
        """Jobs left running by a previous process cannot be resumed."""

        now = utc_now()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """UPDATE jobs SET status = 'FAILED',
                       error = 'Interrupted: the server stopped while this job was running. Start it again.',
                       updated_at = ?
                   WHERE status IN ('QUEUED', 'PROCESSING')""",
                (now,),
            )
            return cursor.rowcount

    def job_counts(self, surveyor_id: str | None = None) -> dict[str, int]:
        query = "SELECT status, COUNT(*) AS n FROM jobs"
        params: list[Any] = []
        if surveyor_id:
            query += " WHERE surveyor_id = ?"
            params.append(surveyor_id)
        query += " GROUP BY status"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return {row["status"]: int(row["n"]) for row in rows}

    # --------------------------------------------------------------- reviews
    @staticmethod
    def _review_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        review = dict(row)
        for column, key in _REVIEW_JSON.items():
            review[key] = _loads(review.pop(column, None))
        return review

    def add_review(
        self,
        *,
        feature_id: str,
        layer: str,
        source: str,
        surveyor_id: str,
        surveyor_email: str | None,
        assignment_id: str | None,
        action: str,
        comment: str = "",
        model_confidence: float | None = None,
        model_entropy: float | None = None,
        original_geometry: dict | None = None,
        edited_geometry: dict | None = None,
        ground_truth: dict | None = None,
    ) -> dict[str, Any]:
        if action not in ACTION_STATUS:
            raise ValueError(
                f"Invalid action '{action}'. Use one of: {', '.join(sorted(ACTION_STATUS))}."
            )
        if action == "edit" and not edited_geometry:
            raise ValueError("An edit review must include the edited geometry.")
        if action == "add_ground_truth" and not ground_truth:
            raise ValueError("A ground-truth review must include the observation.")

        before_status = self.effective_status(feature_id, source)
        review_id = new_id("REV")
        now = utc_now()
        status = ACTION_STATUS[action]

        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO reviews (review_id, feature_id, layer, source, surveyor_id,
                       surveyor_email, assignment_id, action, comment, verification_status,
                       model_confidence, model_entropy, original_geometry_json,
                       edited_geometry_json, ground_truth_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    review_id,
                    feature_id,
                    layer,
                    source,
                    surveyor_id,
                    surveyor_email,
                    assignment_id,
                    action,
                    comment or "",
                    status,
                    model_confidence,
                    model_entropy,
                    _dumps(original_geometry),
                    _dumps(edited_geometry),
                    _dumps(ground_truth),
                    now,
                    now,
                ),
            )
            conn.execute(
                """INSERT INTO audit_log (at, actor_id, actor_email, action, entity_type,
                       entity_id, before_json, after_json, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    now,
                    surveyor_id,
                    surveyor_email,
                    f"review.{action}",
                    layer,
                    f"{source}:{feature_id}",
                    _dumps(
                        {
                            "verification_status": before_status,
                            "geometry": original_geometry if action == "edit" else None,
                        }
                    ),
                    _dumps(
                        {
                            "verification_status": self._status_after(before_status, action),
                            "review_id": review_id,
                            "geometry": edited_geometry if action == "edit" else None,
                            "ground_truth": ground_truth,
                        }
                    ),
                    comment or "",
                ),
            )
        return self.get_review(review_id)  # type: ignore[return-value]

    @staticmethod
    def _status_after(before: str, action: str) -> str:
        """Ground truth adds evidence; it does not undo an earlier decision."""

        if action == "add_ground_truth" and before in (
            "SURVEYOR_VERIFIED",
            "EDITED",
            "FLAGGED",
            "REJECTED",
        ):
            return before
        return ACTION_STATUS[action]

    def get_review(self, review_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM reviews WHERE review_id = ?", (review_id,)
            ).fetchone()
        return self._review_row(row)

    def update_review(
        self,
        review_id: str,
        *,
        actor_id: str,
        actor_email: str | None,
        comment: str | None = None,
        action: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_review(review_id)
        if current is None:
            raise KeyError(review_id)

        updates: dict[str, Any] = {}
        if comment is not None:
            updates["comment"] = comment
        if action is not None:
            if action not in ACTION_STATUS:
                raise ValueError(f"Invalid action '{action}'.")
            if action == "edit" and not current.get("edited_geometry"):
                raise ValueError("This review holds no edited geometry; submit a new edit instead.")
            updates["action"] = action
            updates["verification_status"] = ACTION_STATUS[action]
        if not updates:
            return current

        now = utc_now()
        updates["updated_at"] = now
        assignments = ", ".join(f"{column} = ?" for column in updates)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE reviews SET {assignments} WHERE review_id = ?",
                (*updates.values(), review_id),
            )
            conn.execute(
                """INSERT INTO audit_log (at, actor_id, actor_email, action, entity_type,
                       entity_id, before_json, after_json, reason)
                   VALUES (?, ?, ?, 'review.update', 'review', ?, ?, ?, ?)""",
                (
                    now,
                    actor_id,
                    actor_email,
                    review_id,
                    _dumps({k: current.get(k) for k in ("action", "comment", "verification_status")}),
                    _dumps({k: v for k, v in updates.items() if k != "updated_at"}),
                    comment or "",
                ),
            )
        return self.get_review(review_id)  # type: ignore[return-value]

    def list_reviews(
        self,
        *,
        feature_id: str | None = None,
        source: str | None = None,
        status: str | None = None,
        surveyor_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("feature_id", feature_id),
            ("source", source),
            ("verification_status", status),
            ("surveyor_id", surveyor_id),
        ):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        query = "SELECT * FROM reviews"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._review_row(row) for row in rows]  # type: ignore[misc]

    def _feature_history(self, source: str | None = None) -> dict[tuple[str, str], list[sqlite3.Row]]:
        query = (
            "SELECT review_id, feature_id, layer, source, action, verification_status, "
            "edited_geometry_json, surveyor_id, created_at FROM reviews"
        )
        params: list[Any] = []
        if source:
            query += " WHERE source = ?"
            params.append(source)
        query += " ORDER BY created_at ASC, rowid ASC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        history: dict[tuple[str, str], list[sqlite3.Row]] = {}
        for row in rows:
            history.setdefault((row["source"], row["feature_id"]), []).append(row)
        return history

    def feature_states(self, source: str | None = None) -> dict[tuple[str, str], dict[str, Any]]:
        """Effective review state per (source, feature_id).

        The status is the most recent decision (approve / edit / flag /
        reject). Ground truth alone yields SURVEYOR_REVIEWED. The effective
        geometry is the most recent edit, unless a later review rejected it.
        """

        states: dict[tuple[str, str], dict[str, Any]] = {}
        for key, rows in self._feature_history(source).items():
            status = "AI_GENERATED"
            geometry = None
            has_ground_truth = False
            for row in rows:
                action = row["action"]
                if action == "add_ground_truth":
                    has_ground_truth = True
                    if status == "AI_GENERATED":
                        status = "SURVEYOR_REVIEWED"
                    continue
                status = row["verification_status"]
                if action == "edit":
                    geometry = _loads(row["edited_geometry_json"])
                elif action == "reject":
                    geometry = None
            last = rows[-1]
            states[key] = {
                "verification_status": status,
                "edited_geometry": geometry,
                "review_count": len(rows),
                "has_ground_truth": has_ground_truth,
                "layer": last["layer"],
                "last_review_at": last["created_at"],
                "last_surveyor_id": last["surveyor_id"],
            }
        return states

    def effective_status(self, feature_id: str, source: str) -> str:
        state = self.feature_states(source).get((source, feature_id))
        return state["verification_status"] if state else "AI_GENERATED"

    def review_stats(self, source: str | None = None) -> dict[str, Any]:
        states = self.feature_states(source)
        by_status: dict[str, int] = {}
        for state in states.values():
            by_status[state["verification_status"]] = by_status.get(state["verification_status"], 0) + 1

        query = "SELECT action, COUNT(*) AS n FROM reviews"
        params: list[Any] = []
        if source:
            query += " WHERE source = ?"
            params.append(source)
        query += " GROUP BY action"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        actions = {row["action"]: int(row["n"]) for row in rows}

        return {
            "total_reviews": sum(actions.values()),
            "reviewed_features": len(states),
            "status_counts": by_status,
            "action_counts": actions,
            "verified_count": by_status.get("SURVEYOR_VERIFIED", 0),
            "edited_count": by_status.get("EDITED", 0),
            "flagged_count": by_status.get("FLAGGED", 0),
            "rejected_count": by_status.get("REJECTED", 0),
            "ground_truth_count": actions.get("add_ground_truth", 0),
        }

    def ground_truth_records(self, source: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM reviews WHERE action = 'add_ground_truth'"
        params: list[Any] = []
        if source:
            query += " AND source = ?"
            params.append(source)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._review_row(row) for row in rows]  # type: ignore[misc]

    # ----------------------------------------------------------------- audit
    def add_audit(
        self,
        *,
        actor_id: str,
        actor_email: str | None,
        action: str,
        entity_type: str,
        entity_id: str,
        before: Any = None,
        after: Any = None,
        reason: str = "",
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO audit_log (at, actor_id, actor_email, action, entity_type,
                       entity_id, before_json, after_json, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    utc_now(),
                    actor_id,
                    actor_email,
                    action,
                    entity_type,
                    entity_id,
                    _dumps(before),
                    _dumps(after),
                    reason or "",
                ),
            )

    def list_audit(
        self,
        *,
        entity_id: str | None = None,
        actor_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if entity_id:
            clauses.append("(entity_id = ? OR entity_id LIKE ?)")
            params.extend([entity_id, f"%:{entity_id}"])
        if actor_id:
            clauses.append("actor_id = ?")
            params.append(actor_id)
        query = "SELECT * FROM audit_log"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY event_id DESC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["before"] = _loads(event.pop("before_json", None))
            event["after"] = _loads(event.pop("after_json", None))
            events.append(event)
        return events

    # ------------------------------------------------------------ work areas
    @staticmethod
    def _work_area_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        area = dict(row)
        area["geometry"] = _loads(area.pop("geometry_json", None))
        area["is_active"] = bool(area["is_active"])
        return area

    def create_work_area(
        self,
        *,
        owner_email: str,
        owner_surveyor_id: str,
        name: str,
        geometry: dict[str, Any],
        area_m2: float,
        origin: str,
        state: str | None = None,
        district: str | None = None,
        taluk: str | None = None,
        village: str | None = None,
    ) -> dict[str, Any]:
        area_id = new_id("WA")
        now = utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO work_areas (area_id, owner_email, owner_surveyor_id, name, state,
                       district, taluk, village, geometry_json, area_m2, origin, is_active,
                       created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (
                    area_id,
                    owner_email.lower(),
                    owner_surveyor_id,
                    name,
                    state,
                    district,
                    taluk,
                    village,
                    _dumps(geometry),
                    float(area_m2),
                    origin,
                    now,
                    now,
                ),
            )
        return self.get_work_area(area_id)  # type: ignore[return-value]

    def get_work_area(self, area_id: str, owner_email: str | None = None) -> dict[str, Any] | None:
        """One work area; with ``owner_email``, only if that account owns it."""

        query = "SELECT * FROM work_areas WHERE area_id = ?"
        params: list[Any] = [area_id]
        if owner_email is not None:
            query += " AND owner_email = ?"
            params.append(owner_email.lower())
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return self._work_area_row(row)

    def list_work_areas(self, owner_email: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM work_areas WHERE owner_email = ? ORDER BY created_at, area_id",
                (owner_email.lower(),),
            ).fetchall()
        return [self._work_area_row(row) for row in rows]  # type: ignore[misc]

    def active_work_area(self, owner_email: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM work_areas WHERE owner_email = ? AND is_active = 1 LIMIT 1",
                (owner_email.lower(),),
            ).fetchone()
        return self._work_area_row(row)

    _WORK_AREA_FIELDS = ("name", "state", "district", "taluk", "village", "geometry", "area_m2", "origin")

    def update_work_area(self, area_id: str, owner_email: str, **fields: Any) -> dict[str, Any] | None:
        updates = {key: value for key, value in fields.items() if key in self._WORK_AREA_FIELDS}
        if "geometry" in updates:
            updates["geometry_json"] = _dumps(updates.pop("geometry"))
        updates["updated_at"] = utc_now()
        assignments = ", ".join(f"{column} = ?" for column in updates)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE work_areas SET {assignments} WHERE area_id = ? AND owner_email = ?",
                [*updates.values(), area_id, owner_email.lower()],
            )
        return self.get_work_area(area_id, owner_email)

    def delete_work_area(self, area_id: str, owner_email: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM work_areas WHERE area_id = ? AND owner_email = ?",
                (area_id, owner_email.lower()),
            )
        return cursor.rowcount > 0

    def clear_area_choice(self, owner_email: str) -> None:
        """No work area active and no registry choice: the account falls back
        to its registry assignment or, if allowed, the demo assignment."""

        owner = owner_email.lower()
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE work_areas SET is_active = 0 WHERE owner_email = ?", (owner,))
            conn.execute("DELETE FROM assignment_preferences WHERE owner_email = ?", (owner,))

    def activate_work_area(self, area_id: str, owner_email: str) -> None:
        """Make one work area current; every other area of the owner is not."""

        owner = owner_email.lower()
        now = utc_now()
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE work_areas SET is_active = 0 WHERE owner_email = ?", (owner,))
            conn.execute(
                "UPDATE work_areas SET is_active = 1, updated_at = ? WHERE area_id = ? AND owner_email = ?",
                (now, area_id, owner),
            )
            conn.execute("DELETE FROM assignment_preferences WHERE owner_email = ?", (owner,))

    def prefer_registry_assignment(self, owner_email: str, assignment_id: str) -> None:
        """Make a registry assignment current: no work area stays active."""

        owner = owner_email.lower()
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE work_areas SET is_active = 0 WHERE owner_email = ?", (owner,))
            conn.execute(
                """INSERT INTO assignment_preferences (owner_email, assignment_id, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(owner_email) DO UPDATE SET
                       assignment_id = excluded.assignment_id, updated_at = excluded.updated_at""",
                (owner, assignment_id, utc_now()),
            )

    def preferred_registry_assignment(self, owner_email: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT assignment_id FROM assignment_preferences WHERE owner_email = ?",
                (owner_email.lower(),),
            ).fetchone()
        return row["assignment_id"] if row else None

    # -------------------------------------------------------------- datasets
    @staticmethod
    def _dataset_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        dataset = dict(row)
        dataset["meta"] = _loads(dataset.pop("meta_json", None)) or {}
        return dataset

    def add_dataset(
        self,
        *,
        name: str,
        source_type: str,
        kind: str,
        origin: str,
        path: str,
        meta: dict[str, Any] | None = None,
        surveyor_id: str | None = None,
        dataset_id: str | None = None,
    ) -> dict[str, Any]:
        dataset_id = dataset_id or new_id("DS")
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO datasets (dataset_id, name, source_type, kind,
                       origin, path, meta_json, surveyor_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    dataset_id,
                    name,
                    source_type,
                    kind,
                    origin,
                    path,
                    _dumps(meta or {}),
                    surveyor_id,
                    utc_now(),
                ),
            )
        return self.get_dataset(dataset_id)  # type: ignore[return-value]

    def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM datasets WHERE dataset_id = ?", (dataset_id,)
            ).fetchone()
        return self._dataset_row(row)

    def list_datasets(self, source_types: Iterable[str] | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM datasets"
        params: list[Any] = []
        if source_types:
            types = list(source_types)
            query += f" WHERE source_type IN ({', '.join('?' for _ in types)})"
            params.extend(types)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._dataset_row(row) for row in rows]  # type: ignore[misc]


_store: Store | None = None
_store_lock = threading.Lock()


def get_store() -> Store:
    """Process-wide store bound to the configured database path."""

    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                from backend.config import settings

                settings.ensure_dirs()
                _store = Store(settings.state_db)
    return _store


def set_store(store: Store | None) -> None:
    """Replace the process-wide store (used by tests)."""

    global _store
    _store = store
