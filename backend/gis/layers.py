"""Indexed store for vector layers.

GeoJSON files are ingested once into a SQLite cache with an R-tree, so the
API can answer "what is inside this map view / this assigned area" without
re-reading a 100 MB file or shipping it to the browser. Each feature keeps

* its full-resolution geometry (compressed) for measurement and export,
* two display generalisations for the map,
* its attributes plus measured geometry metrics and a QA assessment.

The cache is rebuilt automatically when the source file changes.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from backend.ai import qa
from backend.ai.classes import CLASS_KEYS, CLASS_NAMES, CLASS_PREFIX, KEY_TO_ID, class_key
from backend.gis.geojson_io import (
    GeoJSONError,
    crs_name,
    lonlat_transformer,
    read_header,
    reproject_geometry,
    scan_feature_collection,
)
from backend.gis.geometry import analyse_geometry

SCHEMA_VERSION = 4  # 4: area, perimeter, length and width always measured in UTM

# Display generalisation. "detail" removes the raster stair-steps only;
# "overview" is for zoomed-out views.
DETAIL_TOLERANCE_M = 0.05
DETAIL_MIN_HOLE_M2 = 0.02
OVERVIEW_TOLERANCE_M = 0.40
OVERVIEW_MIN_HOLE_M2 = 2.0
OVERVIEW_MIN_PART_M2 = 0.5
GENERALISATION_LEVELS = (
    (DETAIL_TOLERANCE_M, DETAIL_MIN_HOLE_M2, 0.0),
    (OVERVIEW_TOLERANCE_M, OVERVIEW_MIN_HOLE_M2, OVERVIEW_MIN_PART_M2),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS layers (
    layer_key     TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    kind          TEXT NOT NULL,
    label         TEXT,
    origin        TEXT,
    path          TEXT NOT NULL,
    file_size     INTEGER,
    file_mtime    REAL,
    crs           TEXT,
    feature_count INTEGER,
    skipped_count INTEGER,
    minx REAL, miny REAL, maxx REAL, maxy REAL,
    meta_json     TEXT,
    ingested_at   TEXT
);

CREATE TABLE IF NOT EXISTS features (
    id          INTEGER PRIMARY KEY,
    layer_key   TEXT NOT NULL,
    uid         TEXT NOT NULL,
    class_key   TEXT NOT NULL,
    area_m2     REAL,
    perimeter_m REAL,
    priority    TEXT,
    confidence  REAL,
    entropy     REAL,
    vertices    INTEGER,
    rings       INTEGER,
    minx REAL, miny REAL, maxx REAL, maxy REAL,
    props_json  TEXT NOT NULL,
    geom_full   BLOB NOT NULL,
    geom_detail TEXT,
    geom_overview TEXT
);
CREATE INDEX IF NOT EXISTS idx_features_uid ON features (layer_key, uid);
CREATE INDEX IF NOT EXISTS idx_features_class ON features (layer_key, class_key, area_m2);

CREATE VIRTUAL TABLE IF NOT EXISTS features_rtree USING rtree (id, minx, maxx, miny, maxy);
"""


def layer_key(source: str, kind: str) -> str:
    return f"{source}|{kind}"


def _first_number(properties: dict[str, Any], *names: str) -> float | None:
    for name in names:
        value = properties.get(name)
        if isinstance(value, bool) or value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            return number
    return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "yes", "1"):
        return True
    if text in ("false", "no", "0"):
        return False
    return None


_shapely_checked = False
_shapely_shape = None
_shapely_explain = None


def _ogc_validity(geometry: dict[str, Any]) -> tuple[str, str | None]:
    """OGC validity through Shapely when it is installed.

    Returns ``(status, reason)``. Without Shapely the status is
    ``NOT_CHECKED`` rather than a guess.
    """

    global _shapely_checked, _shapely_shape, _shapely_explain
    if not _shapely_checked:
        _shapely_checked = True
        try:
            from shapely.geometry import shape
            from shapely.validation import explain_validity

            _shapely_shape, _shapely_explain = shape, explain_validity
        except Exception:  # Shapely missing or broken: degrade, do not fail
            _shapely_shape = None
    if _shapely_shape is None:
        return "NOT_CHECKED", None
    try:
        geom = _shapely_shape(geometry)
        if geom.is_valid:
            return "VALID", None
        return "INVALID", str(_shapely_explain(geom))
    except Exception as exc:
        return "INVALID", f"could not be parsed ({exc})"


class LayerStore:
    def __init__(
        self,
        db_path: Path | str,
        *,
        sliver_area_m2: float = 1.0,
        min_parcel_area_m2: float = 25.0,
        road_access_distance_m: float = 5.0,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.sliver_area_m2 = sliver_area_m2
        self.min_parcel_area_m2 = min_parcel_area_m2
        self.road_access_distance_m = road_access_distance_m
        self._ingest_lock = threading.Lock()
        self._progress: dict[str, dict[str, Any]] = {}
        self._init_db()

    # ------------------------------------------------------------ plumbing
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=60)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            if row is None or row["value"] != str(SCHEMA_VERSION):
                # Generalisation rules or columns changed: rebuild from source.
                conn.executescript(
                    "DROP TABLE IF EXISTS features_rtree; DROP TABLE IF EXISTS features;"
                    "DROP TABLE IF EXISTS layers;"
                )
                conn.executescript(SCHEMA)
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )

    # -------------------------------------------------------------- layers
    @staticmethod
    def _layer_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        layer = dict(row)
        layer["meta"] = json.loads(layer.pop("meta_json") or "{}")
        if layer.get("minx") is not None:
            layer["bbox"] = [layer["minx"], layer["miny"], layer["maxx"], layer["maxy"]]
        else:
            layer["bbox"] = None
        for key in ("minx", "miny", "maxx", "maxy"):
            layer.pop(key, None)
        return layer

    def layer(self, source: str, kind: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM layers WHERE layer_key = ?", (layer_key(source, kind),)
            ).fetchone()
        return self._layer_row(row)

    def layers(self, source: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM layers"
        params: list[Any] = []
        if source:
            query += " WHERE source = ?"
            params.append(source)
        query += " ORDER BY source, kind"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._layer_row(row) for row in rows]  # type: ignore[misc]

    def progress(self) -> dict[str, dict[str, Any]]:
        return {key: dict(value) for key, value in self._progress.items()}

    def drop(self, source: str, kind: str | None = None) -> None:
        with self._ingest_lock, self._connect() as conn:
            if kind is None:
                keys = [
                    row["layer_key"]
                    for row in conn.execute(
                        "SELECT layer_key FROM layers WHERE source = ?", (source,)
                    )
                ]
            else:
                keys = [layer_key(source, kind)]
            for key in keys:
                self._delete_layer(conn, key)

    @staticmethod
    def _delete_layer(conn: sqlite3.Connection, key: str) -> None:
        conn.execute(
            "DELETE FROM features_rtree WHERE id IN (SELECT id FROM features WHERE layer_key = ?)",
            (key,),
        )
        conn.execute("DELETE FROM features WHERE layer_key = ?", (key,))
        conn.execute("DELETE FROM layers WHERE layer_key = ?", (key,))

    # -------------------------------------------------------------- ingest
    def ensure(
        self,
        source: str,
        kind: str,
        path: Path | str,
        *,
        label: str | None = None,
        origin: str = "existing",
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Ingest ``path`` unless an up-to-date copy is already cached.

        Returns the layer record, or ``None`` when the file does not exist.
        Raises :class:`GeoJSONError` when the file is present but unusable.
        """

        path = Path(path)
        if not path.exists():
            return None

        stat = path.stat()
        current = self.layer(source, kind)
        if (
            current
            and current["path"] == str(path)
            and current["file_size"] == stat.st_size
            and abs((current["file_mtime"] or 0) - stat.st_mtime) < 1e-6
        ):
            return current

        with self._ingest_lock:
            # Another thread may have finished the same ingest while we waited.
            current = self.layer(source, kind)
            if (
                current
                and current["path"] == str(path)
                and current["file_size"] == stat.st_size
                and abs((current["file_mtime"] or 0) - stat.st_mtime) < 1e-6
            ):
                return current
            self._ingest(source, kind, path, label=label, origin=origin, meta=meta or {})
        return self.layer(source, kind)

    def _ingest(
        self,
        source: str,
        kind: str,
        path: Path,
        *,
        label: str | None,
        origin: str,
        meta: dict[str, Any],
    ) -> None:
        key = layer_key(source, kind)
        started = time.time()
        self._progress[key] = {"state": "reading", "done": 0, "total": None, "file": path.name}

        header = read_header(path)
        crs = crs_name(header)
        to_lonlat = lonlat_transformer(crs)
        total = int(header.get("feature_count") or 0)
        self._progress[key].update(state="indexing", total=total)

        stat = path.stat()
        self._area_audit = {"supplied": 0, "disagreeing": 0, "min_ratio": math.inf, "max_ratio": 0.0}
        rows: list[tuple] = []
        used_uids: set[str] = set()
        skipped: list[str] = []
        bounds = [math.inf, math.inf, -math.inf, -math.inf]
        state = {"index": 0}

        conn = sqlite3.connect(str(self.db_path), timeout=120)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._delete_layer(conn, key)

            def flush() -> None:
                if not rows:
                    return
                cursor = conn.cursor()
                for row in rows:
                    cursor.execute(
                        """INSERT INTO features (layer_key, uid, class_key, area_m2, perimeter_m,
                               priority, confidence, entropy, vertices, rings,
                               minx, miny, maxx, maxy, props_json, geom_full, geom_detail,
                               geom_overview)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        row,
                    )
                    cursor.execute(
                        "INSERT INTO features_rtree (id, minx, maxx, miny, maxy) VALUES (?, ?, ?, ?, ?)",
                        (cursor.lastrowid, row[10], row[12], row[11], row[13]),
                    )
                rows.clear()

            def on_feature(feature: dict[str, Any]) -> None:
                state["index"] += 1
                index = state["index"]
                built = self._build_row(key, kind, feature, index, to_lonlat, used_uids)
                if isinstance(built, str):
                    if len(skipped) < 25:
                        skipped.append(f"feature {index}: {built}")
                    else:
                        skipped.append("")
                    return
                rows.append(built)
                bounds[0] = min(bounds[0], built[10])
                bounds[1] = min(bounds[1], built[11])
                bounds[2] = max(bounds[2], built[12])
                bounds[3] = max(bounds[3], built[13])
                if len(rows) >= 200:
                    flush()
                    self._progress[key]["done"] = index

            scan_feature_collection(path, on_feature)
            flush()

            kept = state["index"] - len(skipped)
            layer_meta = dict(meta)
            layer_meta.update(
                {
                    "source_crs": crs,
                    "collection_name": header.get("name"),
                    "skipped_examples": [item for item in skipped if item][:25],
                    "area_check": {
                        "rule": "area_m2 measured in the feature's UTM zone; supplied values are not used",
                        "supplied_area_values": self._area_audit["supplied"],
                        "disagreeing_by_more_than_1pct": self._area_audit["disagreeing"],
                        "supplied_to_measured_ratio_range": (
                            [round(self._area_audit["min_ratio"], 4), round(self._area_audit["max_ratio"], 4)]
                            if self._area_audit["disagreeing"]
                            else None
                        ),
                    },
                    "ingest_seconds": round(time.time() - started, 2),
                    "generalisation": {
                        "detail_tolerance_m": DETAIL_TOLERANCE_M,
                        "overview_tolerance_m": OVERVIEW_TOLERANCE_M,
                    },
                }
            )
            conn.execute(
                """INSERT OR REPLACE INTO layers (layer_key, source, kind, label, origin, path,
                       file_size, file_mtime, crs, feature_count, skipped_count,
                       minx, miny, maxx, maxy, meta_json, ingested_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (
                    key,
                    source,
                    kind,
                    label or path.stem,
                    origin,
                    str(path),
                    stat.st_size,
                    stat.st_mtime,
                    crs,
                    kept,
                    len(skipped),
                    bounds[0] if kept else None,
                    bounds[1] if kept else None,
                    bounds[2] if kept else None,
                    bounds[3] if kept else None,
                    json.dumps(layer_meta, ensure_ascii=False),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            self._progress[key] = {"state": "failed", "file": path.name}
            raise
        finally:
            conn.close()

        self._progress[key] = {
            "state": "ready",
            "done": state["index"],
            "total": state["index"],
            "file": path.name,
        }

    def _build_row(
        self,
        key: str,
        kind: str,
        feature: dict[str, Any],
        index: int,
        to_lonlat,
        used_uids: set[str],
    ) -> tuple | str:
        """One database row for a feature, or a reason it was not usable.

        Unusable features are counted and reported on the layer; they are
        never silently dropped.
        """

        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            return "not a GeoJSON Feature"

        geometry = feature.get("geometry")
        if isinstance(geometry, dict) and to_lonlat is not None:
            try:
                geometry = reproject_geometry(geometry, to_lonlat)
            except Exception as exc:  # malformed rings
                return f"could not be reprojected ({exc})"

        analysis = analyse_geometry(geometry, GENERALISATION_LEVELS)
        problems = analysis["problems"]
        if analysis["fatal"]:
            return problems[0] if problems else "geometry is unusable"
        metrics = analysis["metrics"]
        bbox = analysis["bbox"]
        detail, overview = analysis["simplified"]

        properties = dict(feature.get("properties") or {})

        # ---- class -------------------------------------------------------
        raw_class = properties.get("class_name")
        if raw_class in (None, ""):
            raw_class = properties.get("class_id")
        if raw_class in (None, "") and kind == "parcels":
            raw_class = properties.get("source_class")
        ckey = class_key(raw_class)
        cid = KEY_TO_ID.get(ckey)

        if cid is not None:
            class_label = CLASS_NAMES[cid]
        else:
            class_label = str(raw_class) if raw_class not in (None, "") else "Unknown"

        # ---- identifier --------------------------------------------------
        uid = None
        if kind == "parcels" and properties.get("parcel_id") not in (None, ""):
            uid = str(properties["parcel_id"])
        elif properties.get("uid") not in (None, ""):
            uid = str(properties["uid"])
        elif isinstance(properties.get("feature_id"), str) and "-" in properties["feature_id"]:
            uid = properties["feature_id"]
        else:
            number = properties.get("feature_id", feature.get("id", index))
            try:
                number = int(number)
            except (TypeError, ValueError):
                number = index
            prefix = "CAND" if kind == "parcels" else CLASS_PREFIX.get(cid, "FTR") if cid is not None else "FTR"
            uid = f"{prefix}-{number:06d}"
        if uid in used_uids:
            uid = f"{uid}-{index}"
        used_uids.add(uid)

        # ---- measurements: always measured here, in the feature's UTM zone.
        # A supplied area_m2 is not trusted: a file measured in Web Mercator
        # carries areas about 1 / cos^2(latitude) too large. Disagreements
        # are counted on the layer, not kept as a second area field.
        supplied_area = _first_number(properties, "area_m2")
        area = metrics["area_m2"]
        perimeter = metrics["perimeter_m"]
        audit = getattr(self, "_area_audit", None)
        if audit is not None and supplied_area is not None:
            audit["supplied"] += 1
            if abs(supplied_area - area) > max(0.01 * area, 0.01):
                audit["disagreeing"] += 1
                if area > 0:
                    ratio = supplied_area / area
                    audit["min_ratio"] = min(audit["min_ratio"], ratio)
                    audit["max_ratio"] = max(audit["max_ratio"], ratio)
        confidence = _first_number(properties, "confidence", "mean_confidence")
        entropy = _first_number(properties, "entropy", "mean_entropy")

        geometry_status = str(properties.get("geometry_status") or "").upper()
        if not geometry_status:
            if problems:
                geometry_status = "INVALID"
            else:
                geometry_status, reason = _ogc_validity(geometry)
                if reason:
                    problems = [*problems, reason]
        has_overlap = bool(_as_bool(properties.get("has_overlap")))
        road_access = _as_bool(
            properties.get("road_access_candidate", properties.get("road_access"))
        )

        assessment = qa.assess(
            layer=kind,
            area_m2=area,
            compactness=metrics["compactness"],
            rings=metrics["rings"],
            confidence=confidence,
            entropy=entropy,
            geometry_status=geometry_status,
            geometry_problems=problems,
            has_overlap=has_overlap,
            road_access=road_access,
            sliver_area_m2=self.sliver_area_m2,
            min_parcel_area_m2=self.min_parcel_area_m2,
            road_access_distance_m=self.road_access_distance_m,
        )

        properties.update(
            {
                "uid": uid,
                "layer": kind,
                "class_key": ckey,
                "class_id": cid if cid is not None else properties.get("class_id"),
                "class_name": class_label,
                "area_m2": area,
                "perimeter_m": perimeter,
                "length_m": metrics["length_m"],
                "width_m": metrics["width_m"],
                "metric_crs": metrics["metric_crs"],
                "metrics_source": "computed",
                "compactness": round(metrics["compactness"], 6),
                "vertex_count": metrics["vertices"],
                "ring_count": metrics["rings"],
                "hole_count": metrics["holes"],
                "confidence": confidence,
                "entropy": entropy,
                "geometry_status": geometry_status,
                "geometry_problems": list(problems),
                "has_overlap": has_overlap,
                "review_priority": assessment["priority"],
                "review_reasons": assessment["reasons"],
                "qa_flags": assessment["flags"],
            }
        )
        if kind == "parcels":
            properties["road_access_candidate"] = road_access

        return (
            key,
            uid,
            ckey,
            area,
            perimeter,
            assessment["priority"],
            confidence,
            entropy,
            metrics["vertices"],
            metrics["rings"],
            bbox[0],
            bbox[1],
            bbox[2],
            bbox[3],
            json.dumps(properties, ensure_ascii=False, separators=(",", ":")),
            zlib.compress(
                json.dumps(geometry, separators=(",", ":")).encode("utf-8"), level=6
            ),
            json.dumps(detail, separators=(",", ":")) if detail else None,
            json.dumps(overview, separators=(",", ":")) if overview else None,
        )

    # --------------------------------------------------------------- query
    @staticmethod
    def _feature(row: sqlite3.Row, geometry: str) -> dict[str, Any]:
        properties = json.loads(row["props_json"])
        geom: Any = None
        if geometry == "full":
            geom = json.loads(zlib.decompress(row["geom_full"]).decode("utf-8"))
        elif geometry == "detail":
            geom = json.loads(row["geom_detail"]) if row["geom_detail"] else None
        elif geometry == "overview":
            geom = json.loads(row["geom_overview"]) if row["geom_overview"] else None
        return {
            "type": "Feature",
            "id": row["uid"],
            "bbox": [row["minx"], row["miny"], row["maxx"], row["maxy"]],
            "properties": properties,
            "geometry": geom,
        }

    def _where(
        self,
        source: str,
        kinds: list[str],
        classes: list[str] | None,
        bbox: tuple[float, float, float, float] | None,
        min_area: float | None,
        max_area: float | None,
        priorities: list[str] | None,
        uids: list[str] | None,
    ) -> tuple[str, str, list[Any]]:
        joins = ""
        clauses = [f"f.layer_key IN ({', '.join('?' for _ in kinds)})"]
        params: list[Any] = [layer_key(source, kind) for kind in kinds]
        if bbox is not None:
            joins = " JOIN features_rtree r ON r.id = f.id"
            clauses.append("r.maxx >= ? AND r.minx <= ? AND r.maxy >= ? AND r.miny <= ?")
            params.extend([bbox[0], bbox[2], bbox[1], bbox[3]])
        if classes:
            clauses.append(f"f.class_key IN ({', '.join('?' for _ in classes)})")
            params.extend(classes)
        if min_area is not None:
            clauses.append("f.area_m2 >= ?")
            params.append(float(min_area))
        if max_area is not None:
            clauses.append("f.area_m2 < ?")
            params.append(float(max_area))
        if priorities:
            clauses.append(f"f.priority IN ({', '.join('?' for _ in priorities)})")
            params.extend(priorities)
        if uids:
            clauses.append(f"f.uid IN ({', '.join('?' for _ in uids)})")
            params.extend(uids)
        return joins, " WHERE " + " AND ".join(clauses), params

    def query(
        self,
        source: str,
        kinds: list[str] | str,
        *,
        classes: list[str] | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        min_area: float | None = None,
        max_area: float | None = None,
        priorities: list[str] | None = None,
        uids: list[str] | None = None,
        geometry: str = "detail",
        order: str = "area_desc",
        limit: int = 1000,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return ``(features, total_matching)``.

        ``geometry`` is one of ``full``, ``detail``, ``overview`` or ``none``.
        """

        if isinstance(kinds, str):
            kinds = [kinds]
        joins, where, params = self._where(
            source, kinds, classes, bbox, min_area, max_area, priorities, uids
        )
        if geometry == "overview":
            # Features too small to draw at overview scale have no overview
            # geometry; leave them out here so LIMIT counts drawable features.
            where += " AND f.geom_overview IS NOT NULL"
        order_sql = {
            "area_desc": "f.area_m2 DESC",
            "area_asc": "f.area_m2 ASC",
            "uid": "f.uid ASC",
            "priority": "CASE f.priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END, f.area_m2 DESC",
            "confidence": "f.confidence IS NULL, f.confidence ASC, f.area_m2 DESC",
        }.get(order, "f.area_m2 DESC")

        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM features f{joins}{where}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT f.* FROM features f{joins}{where} ORDER BY {order_sql} LIMIT ? OFFSET ?",
                [*params, int(limit), int(offset)],
            ).fetchall()
        return [self._feature(row, geometry) for row in rows], int(total)

    def get(
        self, source: str, uid: str, kind: str | None = None, geometry: str = "full"
    ) -> dict[str, Any] | None:
        query = "SELECT * FROM features WHERE uid = ? AND "
        params: list[Any] = [uid]
        if kind:
            query += "layer_key = ?"
            params.append(layer_key(source, kind))
        else:
            query += "layer_key LIKE ?"
            params.append(f"{source}|%")
        query += " ORDER BY layer_key DESC LIMIT 1"
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return self._feature(row, geometry) if row else None

    def iter_full(
        self,
        source: str,
        kind: str,
        classes: list[str] | None = None,
        batch: int = 200,
    ) -> Iterator[dict[str, Any]]:
        """Stream every feature of a layer at full resolution (for export)."""

        last_id = 0
        while True:
            clauses = ["layer_key = ?", "id > ?"]
            params: list[Any] = [layer_key(source, kind), last_id]
            if classes:
                clauses.append(f"class_key IN ({', '.join('?' for _ in classes)})")
                params.extend(classes)
            with self._connect() as conn:
                rows = conn.execute(
                    f"SELECT * FROM features WHERE {' AND '.join(clauses)} ORDER BY id LIMIT ?",
                    [*params, batch],
                ).fetchall()
            if not rows:
                return
            for row in rows:
                yield self._feature(row, "full")
            last_id = rows[-1]["id"]

    # --------------------------------------------------------------- stats
    def stats(
        self,
        source: str,
        kind: str,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> dict[str, Any] | None:
        """Counts and areas per class for a layer (optionally within a box)."""

        if self.layer(source, kind) is None:
            return None
        joins, where, params = self._where(source, [kind], None, bbox, None, None, None, None)
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT f.class_key AS class_key,
                           COUNT(*) AS n,
                           COALESCE(SUM(f.area_m2), 0) AS area,
                           SUM(CASE WHEN f.area_m2 < ? THEN 1 ELSE 0 END) AS fragments,
                           SUM(CASE WHEN f.priority = 'High' THEN 1 ELSE 0 END) AS high,
                           SUM(CASE WHEN f.priority = 'Medium' THEN 1 ELSE 0 END) AS medium,
                           SUM(CASE WHEN f.priority = 'Low' THEN 1 ELSE 0 END) AS low,
                           SUM(CASE WHEN f.confidence IS NOT NULL THEN 1 ELSE 0 END) AS with_confidence,
                           AVG(f.confidence) AS mean_confidence,
                           AVG(f.entropy) AS mean_entropy,
                           COALESCE(SUM(f.perimeter_m), 0) AS perimeter
                    FROM features f{joins}{where}
                    GROUP BY f.class_key""",
                [self.sliver_area_m2, *params],
            ).fetchall()

        classes: dict[str, dict[str, Any]] = {}
        totals = {
            "features": 0,
            "area_m2": 0.0,
            "fragments": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "with_confidence": 0,
        }
        for row in rows:
            entry = {
                "class_key": row["class_key"],
                "class_name": CLASS_NAMES.get(KEY_TO_ID.get(row["class_key"], -1), row["class_key"].title()),
                "count": int(row["n"]),
                "area_m2": round(float(row["area"]), 3),
                "perimeter_m": round(float(row["perimeter"]), 3),
                "fragments": int(row["fragments"] or 0),
                "high": int(row["high"] or 0),
                "medium": int(row["medium"] or 0),
                "low": int(row["low"] or 0),
                "with_confidence": int(row["with_confidence"] or 0),
                "mean_confidence": row["mean_confidence"],
                "mean_entropy": row["mean_entropy"],
            }
            classes[row["class_key"]] = entry
            totals["features"] += entry["count"]
            totals["area_m2"] += entry["area_m2"]
            totals["fragments"] += entry["fragments"]
            totals["high"] += entry["high"]
            totals["medium"] += entry["medium"]
            totals["low"] += entry["low"]
            totals["with_confidence"] += entry["with_confidence"]
        totals["area_m2"] = round(totals["area_m2"], 3)

        ordered = {
            key: classes[key]
            for key in [*CLASS_KEYS.values(), *sorted(set(classes) - set(CLASS_KEYS.values()))]
            if key in classes
        }
        return {"source": source, "kind": kind, "classes": ordered, "totals": totals}

    def uids_by_priority(self, source: str, kind: str) -> dict[str, str]:
        """``uid -> priority`` for every feature of a layer."""

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT uid, priority FROM features WHERE layer_key = ?",
                (layer_key(source, kind),),
            ).fetchall()
        return {row["uid"]: row["priority"] for row in rows}


__all__ = ["LayerStore", "GeoJSONError", "layer_key"]
