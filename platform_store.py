"""Durable storage helpers for the Shade Study Builder platform."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


APP_DIR = Path(__file__).parent
_ACTIVE_DATABASE_PATH: Path | None = None
_UNUSABLE_DATABASE_PATHS: set[Path] = set()
_DATABASE_FALLBACK_REASON = ""
_READABLE_SOURCE_DATABASE_PATH: Path | None = None
PROJECT_STORE_INITIALIZED_KEY = "project_store_initialized"


def default_database_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Shade-GIS" / "shade_study_builder.sqlite3"
    return APP_DIR / "platform_data" / "shade_study_builder.sqlite3"


def fallback_database_paths() -> list[Path]:
    return [
        Path.home() / ".shade-gis" / "shade_study_builder.sqlite3",
        Path(tempfile.gettempdir()) / "Shade-GIS" / "shade_study_builder.sqlite3",
    ]


def candidate_database_paths() -> list[Path]:
    candidates = []
    configured = os.environ.get("SHADE_GIS_DB_PATH")
    if configured:
        candidates.append(Path(configured))
    candidates.append(default_database_path())
    candidates.extend(fallback_database_paths())

    unique = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def is_readonly_database_error(error: BaseException) -> bool:
    return "readonly database" in str(error).lower()


def probe_database_writable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SQLITE_SCHEMA)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS storage_healthcheck (
                id INTEGER PRIMARY KEY,
                checked_at TEXT NOT NULL
            )
            """
        )
        conn.execute("INSERT INTO storage_healthcheck (checked_at) VALUES (?)", (utc_timestamp(),))
        conn.execute(
            """
            DELETE FROM storage_healthcheck
            WHERE id NOT IN (SELECT id FROM storage_healthcheck ORDER BY id DESC LIMIT 3)
            """
        )
        conn.commit()


def mark_database_path_unusable(path: Path, reason: BaseException | str) -> None:
    global _ACTIVE_DATABASE_PATH, _DATABASE_FALLBACK_REASON, _READABLE_SOURCE_DATABASE_PATH
    resolved = path.expanduser().resolve()
    _UNUSABLE_DATABASE_PATHS.add(resolved)
    if _ACTIVE_DATABASE_PATH == resolved:
        _ACTIVE_DATABASE_PATH = None
    _DATABASE_FALLBACK_REASON = str(reason)
    if resolved.exists() and sqlite_database_readable(resolved):
        _READABLE_SOURCE_DATABASE_PATH = resolved


def sqlite_database_readable(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        return True
    except sqlite3.Error:
        return False


def project_ids_at(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            rows = conn.execute("SELECT id FROM projects").fetchall()
        return {str(row[0]) for row in rows}
    except sqlite3.Error:
        return set()


def fallback_needs_source_copy(source: Path, target: Path) -> bool:
    source_ids = project_ids_at(source)
    if not source_ids:
        return False
    if not target.exists():
        return True
    target_ids = project_ids_at(target)
    return not source_ids.issubset(target_ids)


def copy_readonly_source_to_fallback(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and fallback_needs_source_copy(source, target):
        backup = target.with_name(f"{target.stem}.backup-{utc_timestamp().replace(':', '').replace('-', '').replace('T', '-')}{target.suffix}")
        shutil.copy2(target, backup)
    if fallback_needs_source_copy(source, target):
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_conn:
            with sqlite3.connect(target) as target_conn:
                source_conn.backup(target_conn)
                target_conn.commit()


def choose_database_path() -> Path:
    global _ACTIVE_DATABASE_PATH, _DATABASE_FALLBACK_REASON, _READABLE_SOURCE_DATABASE_PATH
    if _ACTIVE_DATABASE_PATH and _ACTIVE_DATABASE_PATH not in _UNUSABLE_DATABASE_PATHS:
        return _ACTIVE_DATABASE_PATH

    last_error: BaseException | None = None
    for candidate in candidate_database_paths():
        if candidate in _UNUSABLE_DATABASE_PATHS:
            continue
        try:
            if _READABLE_SOURCE_DATABASE_PATH and candidate != _READABLE_SOURCE_DATABASE_PATH:
                copy_readonly_source_to_fallback(_READABLE_SOURCE_DATABASE_PATH, candidate)
            probe_database_writable(candidate)
        except (OSError, sqlite3.Error) as error:
            last_error = error
            _UNUSABLE_DATABASE_PATHS.add(candidate)
            _DATABASE_FALLBACK_REASON = str(error)
            if candidate.exists() and sqlite_database_readable(candidate):
                _READABLE_SOURCE_DATABASE_PATH = candidate
            continue
        _ACTIVE_DATABASE_PATH = candidate
        return candidate

    if last_error:
        raise sqlite3.OperationalError(f"No writable Shade-GIS database path is available: {last_error}") from last_error
    raise sqlite3.OperationalError("No writable Shade-GIS database path is available")


def database_status() -> dict[str, Any]:
    active = database_path()
    preferred = candidate_database_paths()[0]
    return {
        "active_path": str(active),
        "preferred_path": str(preferred),
        "using_fallback": active != preferred,
        "fallback_reason": _DATABASE_FALLBACK_REASON,
        "source_path": str(_READABLE_SOURCE_DATABASE_PATH) if _READABLE_SOURCE_DATABASE_PATH else "",
    }


PROJECT_FIELDS = [
    "name",
    "agency",
    "region",
    "description",
    "owners",
    "visibility",
    "dataset_version",
    "methodology_version",
    "source_name",
    "source_license",
    "source_url",
]

STOP_FIELDS = [
    "stop_id",
    "stop_name",
    "stop_lat",
    "stop_lon",
    "agency",
    "routes",
    "municipality",
    "shading",
    "shade_coverage",
    "shade_sources",
    "review_status",
    "confidence",
    "ridership",
    "priority_score",
]

LABEL_FIELDS = [
    "id",
    "project_id",
    "stop_id",
    "image_id",
    "labeler_id",
    "labeler_role",
    "shade_category",
    "shade_coverage",
    "shade_sources",
    "confidence",
    "notes",
    "source",
    "metadata_json",
    "created_at",
]

IMAGE_FIELDS = [
    "id",
    "project_id",
    "stop_id",
    "uri",
    "storage_path",
    "image_type",
    "source",
    "captured_at",
    "attribution",
    "metadata_json",
    "created_at",
]

REVIEW_HISTORY_FIELDS = [
    "id",
    "project_id",
    "stop_id",
    "actor_id",
    "action",
    "from_status",
    "to_status",
    "notes",
    "metadata_json",
    "created_at",
]


def empty_dataframe(columns: list[str]) -> pd.DataFrame:
    """Return an empty frame without invoking Arrow string-index inference."""
    column_index = pd.Index(columns, dtype=object)
    return pd.DataFrame(columns=column_index)

NUMERIC_STOP_FIELDS = {
    "stop_lat",
    "stop_lon",
    "confidence",
    "ridership",
    "priority_score",
}


def utc_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def database_path() -> Path:
    return choose_database_path()


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path is not None else database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate_shade_source_labels(conn: sqlite3.Connection) -> None:
    """Replace retired source labels in persisted project data and audit metadata."""
    label_columns = [
        ("stops", "shading"),
        ("stops", "shade_sources"),
        ("stops", "extra_json"),
        ("shade_labels", "shade_category"),
        ("shade_labels", "shade_sources"),
        ("shade_labels", "metadata_json"),
        ("review_history", "metadata_json"),
        ("images", "metadata_json"),
        ("import_logs", "metadata_json"),
        ("releases", "artifact_manifest_json"),
        ("project_settings", "methodology_json"),
        ("project_settings", "visualization_json"),
    ]
    for table, column in label_columns:
        replacement = (
            f'''REPLACE(REPLACE("{column}", 'Constructed', 'Purpose-built'), '''
            "'Manmade', 'Incidental')"
        )
        conn.execute(
            f'''UPDATE "{table}"
                SET "{column}" = {replacement}
                WHERE "{column}" LIKE '%Constructed%' OR "{column}" LIKE '%Manmade%' '''
        )


def init_database(path: Path | None = None) -> Path:
    db_path = Path(path) if path is not None else database_path()
    with connect(db_path) as conn:
        conn.executescript(SQLITE_SCHEMA)
        project_settings_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(project_settings)").fetchall()
        }
        if "deployment_json" not in project_settings_columns:
            conn.execute(
                "ALTER TABLE project_settings ADD COLUMN deployment_json TEXT NOT NULL DEFAULT '{}'"
            )
        protocol_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(blind_protocols)").fetchall()
        }
        if "assessment_unit" not in protocol_columns:
            conn.execute(
                "ALTER TABLE blind_protocols ADD COLUMN assessment_unit TEXT NOT NULL DEFAULT 'image'"
            )
        rating_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(blind_ratings)").fetchall()
        }
        if "review_method" not in rating_columns:
            conn.execute(
                "ALTER TABLE blind_ratings ADD COLUMN review_method TEXT NOT NULL DEFAULT 'standardized_image'"
            )
        migrate_shade_source_labels(conn)
        conn.commit()
    return db_path


def list_projects(path: Path | None = None) -> list[dict[str, Any]]:
    init_database(path)
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.name, p.agency, p.region, p.visibility, p.dataset_version, p.updated_at,
                   COUNT(s.stop_id) AS location_count,
                   COALESCE(SUM(CASE WHEN s.review_status IN (
                       'Reviewed', 'Crowd Reviewed', 'Expert Reviewed', 'Accepted', 'Archived'
                   ) THEN 1 ELSE 0 END), 0) AS reviewed_count,
                   COALESCE(SUM(CASE WHEN
                       LOWER(TRIM(COALESCE(s.shade_coverage, ''))) IN (
                           'no shade', 'limited', 'limited shade', 'limited natural shade',
                           'significant', 'significant shade', 'significant natural shade'
                       )
                       OR LOWER(TRIM(COALESCE(s.shading, ''))) IN (
                           'no shade', 'limited', 'limited shade', 'limited natural shade',
                           'significant', 'significant shade', 'significant natural shade'
                       )
                       OR EXISTS (
                           SELECT 1
                           FROM shade_labels AS sl
                           WHERE sl.project_id = s.project_id AND sl.stop_id = s.stop_id
                       )
                   THEN 1 ELSE 0 END), 0) AS labeled_count,
                   COUNT(s.stop_id) - COALESCE(SUM(CASE WHEN s.review_status IN (
                       'Reviewed', 'Crowd Reviewed', 'Expert Reviewed', 'Accepted', 'Archived'
                   ) THEN 1 ELSE 0 END), 0) AS awaiting_review_count
            FROM projects AS p
            LEFT JOIN stops AS s ON s.project_id = p.id
            GROUP BY p.id, p.name, p.agency, p.region, p.visibility, p.dataset_version, p.updated_at
            ORDER BY p.updated_at DESC, p.name COLLATE NOCASE
            """
        ).fetchall()
    return [dict(row) for row in rows]


def project_store_initialized(path: Path | None = None) -> bool:
    init_database(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT value FROM app_metadata WHERE key = ?",
            (PROJECT_STORE_INITIALIZED_KEY,),
        ).fetchone()
    return bool(row and row["value"] == "1")


def mark_project_store_initialized(path: Path | None = None) -> None:
    init_database(path)
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO app_metadata (key, value, updated_at)
            VALUES (?, '1', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (PROJECT_STORE_INITIALIZED_KEY, utc_timestamp()),
        )
        conn.commit()


def update_project_details(
    project_id: str,
    *,
    name: str,
    agency: str = "",
    region: str = "",
    description: str = "",
    visibility: str = "Private",
    path: Path | None = None,
) -> None:
    clean_name = str(clean_scalar(name) or "").strip()
    clean_visibility = str(clean_scalar(visibility) or "Private").strip().title()
    if not clean_name:
        raise ValueError("Project name is required")
    if clean_visibility not in {"Private", "Public"}:
        raise ValueError("Project visibility must be Private or Public")

    db_path = Path(path) if path is not None else database_path()
    for attempt in range(2):
        try:
            init_database(db_path)
            with connect(db_path) as conn:
                cursor = conn.execute(
                    """
                    UPDATE projects
                    SET name = ?, agency = ?, region = ?, description = ?, visibility = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        clean_name,
                        clean_scalar(agency),
                        clean_scalar(region),
                        clean_scalar(description),
                        clean_visibility,
                        utc_timestamp(),
                        project_id,
                    ),
                )
                if cursor.rowcount == 0:
                    raise KeyError(f"Project {project_id} was not found")
                conn.commit()
            return
        except sqlite3.OperationalError as error:
            if path is None and attempt == 0 and is_readonly_database_error(error):
                mark_database_path_unusable(db_path, error)
                db_path = database_path()
                continue
            raise


def delete_project(project_id: str, path: Path | None = None) -> bool:
    db_path = Path(path) if path is not None else database_path()
    for attempt in range(2):
        try:
            init_database(db_path)
            with connect(db_path) as conn:
                cursor = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
                conn.commit()
                return cursor.rowcount > 0
        except sqlite3.OperationalError as error:
            if path is None and attempt == 0 and is_readonly_database_error(error):
                mark_database_path_unusable(db_path, error)
                db_path = database_path()
                continue
            raise
    return False


def list_shade_labels(
    project_id: str,
    stop_id: str | None = None,
    path: Path | None = None,
) -> pd.DataFrame:
    init_database(path)
    filters = ["project_id = ?"]
    params: list[Any] = [project_id]
    if stop_id:
        filters.append("stop_id = ?")
        params.append(stop_id)
    where_clause = " AND ".join(filters)
    with connect(path) as conn:
        rows = conn.execute(
            f"""
            SELECT id, project_id, stop_id, image_id, labeler_id, labeler_role,
                   shade_category, shade_coverage, shade_sources, confidence,
                   notes, source, metadata_json, created_at
            FROM shade_labels
            WHERE {where_clause}
            ORDER BY created_at DESC, id DESC
            """,
            params,
        ).fetchall()
    records = []
    for row in rows:
        record = {field: row[field] for field in LABEL_FIELDS}
        metadata = json.loads(record.pop("metadata_json") or "{}")
        for key, value in metadata.items():
            record[f"metadata_{key}"] = value
        records.append(record)
    if not records:
        return empty_dataframe([field for field in LABEL_FIELDS if field != "metadata_json"])
    return pd.DataFrame(records)


def list_images(
    project_id: str,
    stop_id: str | None = None,
    path: Path | None = None,
) -> pd.DataFrame:
    """Return project imagery, optionally scoped to one stop."""
    init_database(path)
    filters = ["project_id = ?"]
    params: list[Any] = [project_id]
    if stop_id:
        filters.append("stop_id = ?")
        params.append(stop_id)
    where_clause = " AND ".join(filters)
    with connect(path) as conn:
        rows = conn.execute(
            f"""
            SELECT id, project_id, stop_id, uri, storage_path, image_type,
                   source, captured_at, attribution, metadata_json, created_at
            FROM images
            WHERE {where_clause}
            ORDER BY captured_at DESC, created_at DESC, id DESC
            """,
            params,
        ).fetchall()
    records = []
    for row in rows:
        record = {field: row[field] for field in IMAGE_FIELDS}
        metadata = json.loads(record.pop("metadata_json") or "{}")
        for key, value in metadata.items():
            record[f"metadata_{key}"] = value
        records.append(record)
    if not records:
        return empty_dataframe([field for field in IMAGE_FIELDS if field != "metadata_json"])
    return pd.DataFrame(records)


def add_image(
    project_id: str,
    image: dict[str, Any],
    path: Path | None = None,
) -> str:
    """Register an uploaded or externally hosted image for review evidence."""
    db_path = Path(path) if path is not None else database_path()
    init_database(db_path)
    image_id = str(image.get("id", "") or uuid.uuid4())
    now = utc_timestamp()
    metadata = {
        key: clean_scalar(value)
        for key, value in image.items()
        if key
        not in {
            "id",
            "stop_id",
            "uri",
            "storage_path",
            "image_type",
            "source",
            "captured_at",
            "attribution",
        }
    }
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO images (
                id, project_id, stop_id, uri, storage_path, image_type,
                source, captured_at, attribution, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                image_id,
                project_id,
                clean_optional_text(image.get("stop_id", "")),
                clean_scalar(image.get("uri", "")),
                clean_optional_text(image.get("storage_path", "")),
                clean_optional_text(image.get("image_type", "")),
                clean_optional_text(image.get("source", "")),
                clean_optional_text(image.get("captured_at", "")),
                clean_optional_text(image.get("attribution", "")),
                json.dumps(metadata, ensure_ascii=True),
                now,
            ),
        )
        conn.commit()
    return image_id


def add_shade_label(
    project_id: str,
    label: dict[str, Any],
    path: Path | None = None,
) -> str:
    db_path = Path(path) if path is not None else database_path()
    for attempt in range(2):
        try:
            return _add_shade_label_once(project_id, label, db_path)
        except sqlite3.OperationalError as error:
            if path is None and attempt == 0 and is_readonly_database_error(error):
                mark_database_path_unusable(db_path, error)
                db_path = database_path()
                continue
            raise
    raise sqlite3.OperationalError("Could not write shade label")


def _add_shade_label_once(
    project_id: str,
    label: dict[str, Any],
    path: Path,
) -> str:
    init_database(path)
    label_id = str(uuid.uuid4())
    now = utc_timestamp()
    metadata = {
        key: clean_scalar(value)
        for key, value in label.items()
        if key
        not in {
            "stop_id",
            "image_id",
            "labeler_id",
            "labeler_role",
            "shade_category",
            "shade_coverage",
            "shade_sources",
            "confidence",
            "notes",
            "source",
        }
    }
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO shade_labels (
                id, project_id, stop_id, image_id, labeler_id, labeler_role,
                shade_category, shade_coverage, shade_sources, confidence,
                notes, source, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                label_id,
                project_id,
                clean_scalar(label.get("stop_id", "")),
                clean_optional_text(label.get("image_id", "")),
                clean_scalar(label.get("labeler_id", "")),
                clean_scalar(label.get("labeler_role", "")),
                clean_scalar(label.get("shade_category", "")),
                clean_scalar(label.get("shade_coverage", "")),
                clean_scalar(label.get("shade_sources", "")),
                clean_number(label.get("confidence")),
                clean_scalar(label.get("notes", "")),
                clean_scalar(label.get("source", "manual")),
                json.dumps(metadata, ensure_ascii=True),
                now,
            ),
        )
        conn.commit()
    return label_id


def list_review_history(
    project_id: str,
    stop_id: str | None = None,
    path: Path | None = None,
) -> pd.DataFrame:
    init_database(path)
    filters = ["project_id = ?"]
    params: list[Any] = [project_id]
    if stop_id:
        filters.append("stop_id = ?")
        params.append(stop_id)
    where_clause = " AND ".join(filters)
    with connect(path) as conn:
        rows = conn.execute(
            f"""
            SELECT id, project_id, stop_id, actor_id, action, from_status,
                   to_status, notes, metadata_json, created_at
            FROM review_history
            WHERE {where_clause}
            ORDER BY created_at DESC, id DESC
            """,
            params,
        ).fetchall()
    records = []
    for row in rows:
        record = {field: row[field] for field in REVIEW_HISTORY_FIELDS}
        metadata = json.loads(record.pop("metadata_json") or "{}")
        for key, value in metadata.items():
            record[f"metadata_{key}"] = value
        records.append(record)
    if not records:
        return empty_dataframe([field for field in REVIEW_HISTORY_FIELDS if field != "metadata_json"])
    return pd.DataFrame(records)


def add_review_event(
    project_id: str,
    event: dict[str, Any],
    path: Path | None = None,
) -> str:
    db_path = Path(path) if path is not None else database_path()
    for attempt in range(2):
        try:
            return _add_review_event_once(project_id, event, db_path)
        except sqlite3.OperationalError as error:
            if path is None and attempt == 0 and is_readonly_database_error(error):
                mark_database_path_unusable(db_path, error)
                db_path = database_path()
                continue
            raise
    raise sqlite3.OperationalError("Could not write review event")


def _add_review_event_once(
    project_id: str,
    event: dict[str, Any],
    path: Path,
) -> str:
    init_database(path)
    event_id = str(uuid.uuid4())
    now = utc_timestamp()
    metadata = {
        key: clean_scalar(value)
        for key, value in event.items()
        if key
        not in {
            "stop_id",
            "actor_id",
            "action",
            "from_status",
            "to_status",
            "notes",
        }
    }
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO review_history (
                id, project_id, stop_id, actor_id, action, from_status,
                to_status, notes, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                project_id,
                clean_scalar(event.get("stop_id", "")),
                clean_scalar(event.get("actor_id", "")),
                clean_scalar(event.get("action", "")),
                clean_scalar(event.get("from_status", "")),
                clean_scalar(event.get("to_status", "")),
                clean_scalar(event.get("notes", "")),
                json.dumps(metadata, ensure_ascii=True),
                now,
            ),
        )
        conn.commit()
    return event_id


def create_project(
    project: dict[str, Any],
    taxonomy: list[dict[str, Any]],
    methodology: dict[str, Any],
    visualization: dict[str, Any],
    stops: pd.DataFrame,
    import_log: list[dict[str, Any]],
    path: Path | None = None,
) -> str:
    project_id = str(uuid.uuid4())
    save_project_bundle(project_id, project, taxonomy, methodology, visualization, stops, import_log, path)
    return project_id


def load_project_bundle(project_id: str, path: Path | None = None) -> dict[str, Any]:
    init_database(path)
    with connect(path) as conn:
        project_row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if project_row is None:
            raise KeyError(f"Project {project_id} was not found")

        settings_row = conn.execute(
            """
            SELECT methodology_json, visualization_json, deployment_json
            FROM project_settings
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
        taxonomy_rows = conn.execute(
            """
            SELECT name, description, color, sort_order
            FROM shade_taxonomy
            WHERE project_id = ?
            ORDER BY sort_order, name COLLATE NOCASE
            """,
            (project_id,),
        ).fetchall()
        stop_rows = conn.execute(
            """
            SELECT *
            FROM stops
            WHERE project_id = ?
            ORDER BY stop_name COLLATE NOCASE, stop_id COLLATE NOCASE
            """,
            (project_id,),
        ).fetchall()
        import_rows = conn.execute(
            """
            SELECT source, format, rows, imported_at, metadata_json
            FROM import_logs
            WHERE project_id = ?
            ORDER BY id
            """,
            (project_id,),
        ).fetchall()

    project = {field: project_row[field] for field in PROJECT_FIELDS}
    project["deployment"] = json.loads(settings_row["deployment_json"] or "{}") if settings_row else {}
    methodology = json.loads(settings_row["methodology_json"]) if settings_row else {}
    visualization = json.loads(settings_row["visualization_json"]) if settings_row else {}
    taxonomy = [dict(row) for row in taxonomy_rows]
    import_log = []
    for row in import_rows:
        entry = {
            "source": row["source"],
            "format": row["format"],
            "rows": row["rows"],
            "imported_at": row["imported_at"],
        }
        entry.update(json.loads(row["metadata_json"] or "{}"))
        import_log.append(entry)

    return {
        "project_id": project_id,
        "project": project,
        "taxonomy": taxonomy,
        "methodology": methodology,
        "visualization": visualization,
        "stops": stops_dataframe(stop_rows),
        "import_log": import_log,
    }


def save_project_bundle(
    project_id: str,
    project: dict[str, Any],
    taxonomy: list[dict[str, Any]],
    methodology: dict[str, Any],
    visualization: dict[str, Any],
    stops: pd.DataFrame,
    import_log: list[dict[str, Any]],
    path: Path | None = None,
) -> None:
    db_path = Path(path) if path is not None else database_path()
    for attempt in range(2):
        try:
            _save_project_bundle_once(project_id, project, taxonomy, methodology, visualization, stops, import_log, db_path)
            return
        except sqlite3.OperationalError as error:
            if path is None and attempt == 0 and is_readonly_database_error(error):
                mark_database_path_unusable(db_path, error)
                db_path = database_path()
                continue
            raise


def _save_project_bundle_once(
    project_id: str,
    project: dict[str, Any],
    taxonomy: list[dict[str, Any]],
    methodology: dict[str, Any],
    visualization: dict[str, Any],
    stops: pd.DataFrame,
    import_log: list[dict[str, Any]],
    path: Path,
) -> None:
    init_database(path)
    now = utc_timestamp()
    project_values = {field: clean_scalar(project.get(field, "")) for field in PROJECT_FIELDS}
    deployment = project.get("deployment") if isinstance(project.get("deployment"), dict) else {}
    if not project_values.get("name"):
        project_values["name"] = "Untitled Shade Study"
    if not project_values.get("visibility"):
        project_values["visibility"] = "Private"

    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO projects (
                id, name, agency, region, description, owners, visibility,
                dataset_version, methodology_version, source_name, source_license,
                source_url, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                agency = excluded.agency,
                region = excluded.region,
                description = excluded.description,
                owners = excluded.owners,
                visibility = excluded.visibility,
                dataset_version = excluded.dataset_version,
                methodology_version = excluded.methodology_version,
                source_name = excluded.source_name,
                source_license = excluded.source_license,
                source_url = excluded.source_url,
                updated_at = excluded.updated_at
            """,
            (
                project_id,
                project_values["name"],
                project_values["agency"],
                project_values["region"],
                project_values["description"],
                project_values["owners"],
                project_values["visibility"],
                project_values["dataset_version"],
                project_values["methodology_version"],
                project_values["source_name"],
                project_values["source_license"],
                project_values["source_url"],
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO project_settings (
                project_id, methodology_json, visualization_json, deployment_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                methodology_json = excluded.methodology_json,
                visualization_json = excluded.visualization_json,
                deployment_json = excluded.deployment_json,
                updated_at = excluded.updated_at
            """,
            (
                project_id,
                json.dumps(methodology, ensure_ascii=True),
                json.dumps(visualization, ensure_ascii=True),
                json.dumps(clean_json_value(deployment), ensure_ascii=True),
                now,
            ),
        )

        conn.execute("DELETE FROM shade_taxonomy WHERE project_id = ?", (project_id,))
        conn.executemany(
            """
            INSERT INTO shade_taxonomy (project_id, name, description, color, sort_order)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    project_id,
                    clean_scalar(item.get("name", "")),
                    clean_scalar(item.get("description", "")),
                    clean_scalar(item.get("color", "")),
                    int(float(item.get("sort_order") or index + 1)),
                )
                for index, item in enumerate(taxonomy)
            ],
        )

        conn.execute("DELETE FROM stops WHERE project_id = ?", (project_id,))
        conn.executemany(
            """
            INSERT INTO stops (
                project_id, stop_id, stop_name, stop_lat, stop_lon, agency, routes,
                municipality, shading, shade_coverage, shade_sources, review_status,
                confidence, ridership, priority_score, extra_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [stop_record(project_id, row, now) for row in dataframe_records(stops)],
        )

        conn.execute("DELETE FROM import_logs WHERE project_id = ?", (project_id,))
        conn.executemany(
            """
            INSERT INTO import_logs (project_id, source, format, rows, imported_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [import_log_record(project_id, entry) for entry in import_log],
        )
        conn.commit()


def stops_dataframe(rows: list[sqlite3.Row]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for row in rows:
        row_keys = set(row.keys())
        record = {field: row[field] for field in STOP_FIELDS if field in row_keys}
        extra = {
            key: row[key]
            for key in row_keys
            if key not in STOP_FIELDS
            and key not in {"id", "project_id", "extra_json", "created_at", "updated_at"}
            and row[key] is not None
        }
        extra.update(json.loads(row["extra_json"] or "{}"))
        record.update(extra)
        records.append(record)
    if not records:
        return empty_dataframe(STOP_FIELDS)
    return pd.DataFrame(records)


def dataframe_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    records = df.where(pd.notna(df), None).to_dict("records")
    return [{str(key): clean_scalar(value) for key, value in record.items()} for record in records]


def stop_record(project_id: str, row: dict[str, Any], now: str) -> tuple[Any, ...]:
    extra = {key: value for key, value in row.items() if key not in STOP_FIELDS}
    values = [row.get(field) for field in STOP_FIELDS]
    return (
        project_id,
        clean_scalar(values[0]),
        clean_scalar(values[1]),
        clean_number(values[2]),
        clean_number(values[3]),
        clean_scalar(values[4]),
        clean_scalar(values[5]),
        clean_scalar(values[6]),
        clean_scalar(values[7]),
        clean_scalar(values[8]),
        clean_scalar(values[9]),
        clean_scalar(values[10]),
        clean_number(values[11]),
        clean_number(values[12]),
        clean_number(values[13]),
        json.dumps(extra, ensure_ascii=True),
        now,
        now,
    )


def import_log_record(project_id: str, entry: dict[str, Any]) -> tuple[Any, ...]:
    metadata = {
        key: clean_scalar(value)
        for key, value in entry.items()
        if key not in {"source", "format", "rows", "imported_at"}
    }
    return (
        project_id,
        clean_scalar(entry.get("source", "")),
        clean_scalar(entry.get("format", "")),
        int(entry.get("rows") or 0),
        clean_scalar(entry.get("imported_at", "")),
        json.dumps(metadata, ensure_ascii=True),
    )


def clean_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(clean_json_value(value), ensure_ascii=True)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    item = getattr(value, "item", None)
    if callable(item):
        try:
            scalar = item()
        except (TypeError, ValueError):
            scalar = value
        if scalar is not value:
            return clean_scalar(scalar)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def clean_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(clean_scalar(key)): clean_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [clean_json_value(item) for item in value]
    value = clean_scalar(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def clean_optional_text(value: Any) -> str | None:
    value = clean_scalar(value)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def clean_number(value: Any) -> float | None:
    value = clean_scalar(value)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    agency TEXT,
    region TEXT,
    description TEXT,
    owners TEXT,
    visibility TEXT NOT NULL DEFAULT 'Private',
    dataset_version TEXT,
    methodology_version TEXT,
    source_name TEXT,
    source_license TEXT,
    source_url TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_settings (
    project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    methodology_json TEXT NOT NULL DEFAULT '{}',
    visualization_json TEXT NOT NULL DEFAULT '{}',
    deployment_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shade_taxonomy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    color TEXT,
    sort_order INTEGER NOT NULL DEFAULT 1,
    UNIQUE(project_id, name)
);

CREATE TABLE IF NOT EXISTS stops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stop_id TEXT NOT NULL,
    stop_name TEXT NOT NULL,
    stop_lat REAL,
    stop_lon REAL,
    agency TEXT,
    routes TEXT,
    municipality TEXT,
    shading TEXT,
    shade_coverage TEXT,
    shade_sources TEXT,
    review_status TEXT,
    confidence REAL,
    ridership REAL,
    priority_score REAL,
    extra_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, stop_id)
);

CREATE TABLE IF NOT EXISTS images (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stop_id TEXT,
    uri TEXT NOT NULL,
    storage_path TEXT,
    image_type TEXT,
    source TEXT,
    captured_at TEXT,
    attribution TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shade_labels (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stop_id TEXT NOT NULL,
    image_id TEXT REFERENCES images(id) ON DELETE SET NULL,
    labeler_id TEXT,
    labeler_role TEXT,
    shade_category TEXT,
    shade_coverage TEXT,
    shade_sources TEXT,
    confidence REAL,
    notes TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blind_protocols (
    project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    phase TEXT NOT NULL DEFAULT 'setup' CHECK (phase IN ('setup', 'coding', 'adjudication', 'closed')),
    codebook_version TEXT NOT NULL,
    target_ratings INTEGER NOT NULL DEFAULT 3 CHECK (target_ratings >= 3),
    assessment_unit TEXT NOT NULL DEFAULT 'image' CHECK (assessment_unit IN ('image', 'stop')),
    agreement_threshold REAL NOT NULL DEFAULT 0.67 CHECK (agreement_threshold >= 0 AND agreement_threshold <= 1),
    instructions TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blind_images (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    image_id TEXT NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    display_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, image_id),
    UNIQUE(project_id, display_id)
);

CREATE TABLE IF NOT EXISTS blind_stops (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stop_id TEXT NOT NULL,
    display_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, stop_id),
    UNIQUE(project_id, display_id),
    FOREIGN KEY(project_id, stop_id) REFERENCES stops(project_id, stop_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS blind_assignments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    blind_image_id TEXT NOT NULL REFERENCES blind_images(id) ON DELETE CASCADE,
    coder_id TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'assigned' CHECK (status IN ('assigned', 'submitted')),
    assigned_at TEXT NOT NULL,
    submitted_at TEXT,
    UNIQUE(project_id, blind_image_id, coder_id)
);

CREATE TABLE IF NOT EXISTS blind_ratings (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    assignment_id TEXT NOT NULL UNIQUE REFERENCES blind_assignments(id) ON DELETE CASCADE,
    codebook_version TEXT NOT NULL,
    shade_source TEXT NOT NULL,
    coverage TEXT NOT NULL,
    waiting_area_covered TEXT NOT NULL,
    permanence TEXT NOT NULL,
    image_adequacy TEXT NOT NULL,
    confidence INTEGER NOT NULL CHECK (confidence BETWEEN 1 AND 5),
    location_recognized TEXT NOT NULL,
    review_method TEXT NOT NULL DEFAULT 'standardized_image',
    submitted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blind_stop_assignments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    blind_stop_id TEXT NOT NULL REFERENCES blind_stops(id) ON DELETE CASCADE,
    coder_id TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'assigned' CHECK (status IN ('assigned', 'submitted')),
    assigned_at TEXT NOT NULL,
    submitted_at TEXT,
    UNIQUE(project_id, blind_stop_id, coder_id)
);

CREATE TABLE IF NOT EXISTS blind_stop_ratings (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    assignment_id TEXT NOT NULL UNIQUE REFERENCES blind_stop_assignments(id) ON DELETE CASCADE,
    codebook_version TEXT NOT NULL,
    shade_source TEXT NOT NULL,
    coverage TEXT NOT NULL,
    waiting_area_covered TEXT NOT NULL,
    permanence TEXT NOT NULL,
    image_adequacy TEXT NOT NULL,
    confidence INTEGER NOT NULL CHECK (confidence BETWEEN 1 AND 5),
    location_recognized TEXT NOT NULL,
    review_method TEXT NOT NULL,
    submitted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blind_adjudications (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    blind_image_id TEXT NOT NULL REFERENCES blind_images(id) ON DELETE CASCADE,
    adjudicator_id TEXT NOT NULL,
    codebook_version TEXT NOT NULL,
    shade_source TEXT NOT NULL,
    coverage TEXT NOT NULL,
    waiting_area_covered TEXT NOT NULL,
    permanence TEXT NOT NULL,
    image_adequacy TEXT NOT NULL,
    confidence INTEGER NOT NULL CHECK (confidence BETWEEN 1 AND 5),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(project_id, blind_image_id)
);

CREATE TABLE IF NOT EXISTS blind_stop_adjudications (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    blind_stop_id TEXT NOT NULL REFERENCES blind_stops(id) ON DELETE CASCADE,
    adjudicator_id TEXT NOT NULL,
    codebook_version TEXT NOT NULL,
    shade_source TEXT NOT NULL,
    coverage TEXT NOT NULL,
    waiting_area_covered TEXT NOT NULL,
    permanence TEXT NOT NULL,
    image_adequacy TEXT NOT NULL,
    confidence INTEGER NOT NULL CHECK (confidence BETWEEN 1 AND 5),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(project_id, blind_stop_id)
);

CREATE TABLE IF NOT EXISTS review_history (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stop_id TEXT NOT NULL,
    actor_id TEXT,
    action TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    notes TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS releases (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version TEXT NOT NULL,
    dataset_version TEXT,
    methodology_version TEXT,
    taxonomy_version TEXT,
    import_version TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    released_at TEXT,
    artifact_manifest_json TEXT NOT NULL DEFAULT '{}',
    notes TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, version)
);

CREATE TABLE IF NOT EXISTS import_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source TEXT,
    format TEXT,
    rows INTEGER,
    imported_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_stops_project ON stops(project_id);
CREATE INDEX IF NOT EXISTS idx_images_project_stop ON images(project_id, stop_id);
CREATE INDEX IF NOT EXISTS idx_labels_project_stop ON shade_labels(project_id, stop_id);
CREATE INDEX IF NOT EXISTS idx_blind_images_project ON blind_images(project_id);
CREATE INDEX IF NOT EXISTS idx_blind_stops_project ON blind_stops(project_id);
CREATE INDEX IF NOT EXISTS idx_blind_assignments_coder ON blind_assignments(project_id, coder_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_blind_stop_assignments_coder ON blind_stop_assignments(project_id, coder_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_blind_ratings_project ON blind_ratings(project_id);
CREATE INDEX IF NOT EXISTS idx_blind_stop_ratings_project ON blind_stop_ratings(project_id);
CREATE INDEX IF NOT EXISTS idx_blind_adjudications_project ON blind_adjudications(project_id);
CREATE INDEX IF NOT EXISTS idx_blind_stop_adjudications_project ON blind_stop_adjudications(project_id);
CREATE INDEX IF NOT EXISTS idx_review_project_stop ON review_history(project_id, stop_id);
CREATE INDEX IF NOT EXISTS idx_releases_project ON releases(project_id);
"""
