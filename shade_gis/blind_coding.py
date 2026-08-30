"""Independent image- or stop-level coding, agreement, and adjudication workflows."""

from __future__ import annotations

import itertools
import json
import math
import secrets
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from platform_store import clean_scalar, connect, empty_dataframe, init_database, utc_timestamp


PHASES = ("setup", "coding", "adjudication", "closed")
PHASE_TRANSITIONS = {
    "setup": "coding",
    "coding": "adjudication",
    "adjudication": "closed",
}

SHADE_SOURCE_OPTIONS = ("none", "tree", "shelter", "building", "mixed", "unclear")
COVERAGE_OPTIONS = ("none", "low", "moderate", "high")
WAITING_AREA_OPTIONS = ("yes", "partially", "no", "unclear")
PERMANENCE_OPTIONS = ("permanent", "seasonal", "time-dependent", "unclear")
IMAGE_ADEQUACY_OPTIONS = ("sufficient", "marginal", "unusable")
LOCATION_RECOGNIZED_OPTIONS = ("no", "yes", "unsure")
ASSESSMENT_UNITS = ("image", "stop")
REVIEW_METHOD_OPTIONS = ("standardized_image", "project_imagery", "google_maps", "field_survey", "other")

RATING_FIELDS = {
    "shade_source": SHADE_SOURCE_OPTIONS,
    "coverage": COVERAGE_OPTIONS,
    "waiting_area_covered": WAITING_AREA_OPTIONS,
    "permanence": PERMANENCE_OPTIONS,
    "image_adequacy": IMAGE_ADEQUACY_OPTIONS,
}

CODEBOOK_DEFINITIONS = {
    "shade_source": {
        "none": "No visible feature is providing shade to the passenger waiting area.",
        "tree": "Vegetation is the visible source of shade.",
        "shelter": "A purpose-built passenger shelter, canopy, or awning is the visible source.",
        "building": "A nearby building or other non-shelter structure is the visible source.",
        "mixed": "Two or more source types visibly contribute shade.",
        "unclear": "The visible source cannot be determined from the image.",
    },
    "coverage": {
        "none": "No visible shade reaches the passenger waiting area.",
        "low": "Shade reaches only a small part of the passenger waiting area.",
        "moderate": "Shade reaches a substantial but incomplete part of the passenger waiting area.",
        "high": "Shade reaches most or all of the passenger waiting area.",
    },
    "waiting_area_covered": {
        "yes": "The visible passenger waiting area is covered by shade.",
        "partially": "Only part of the visible passenger waiting area is covered by shade.",
        "no": "The visible passenger waiting area is not covered by shade.",
        "unclear": "The waiting area or its coverage cannot be determined.",
    },
    "permanence": {
        "permanent": "The shade source is fixed and expected to remain available.",
        "seasonal": "The visible shade depends materially on seasonal vegetation or conditions.",
        "time-dependent": "The visible shade depends materially on sun position or time of day.",
        "unclear": "Permanence cannot be determined from the image.",
    },
    "image_adequacy": {
        "sufficient": "The image supports a confident assessment of the coding variables.",
        "marginal": "The image supports an assessment but has important limitations.",
        "unusable": "The image does not support a defensible assessment.",
    },
}

DEFAULT_IMAGE_INSTRUCTIONS = (
    "Code only what is visible in the standardized image. Do not attempt to identify the stop or infer "
    "conditions from outside knowledge. Record whether you recognize the location. Submit each assessment "
    "independently; submitted ratings are locked."
)
DEFAULT_STOP_INSTRUCTIONS = (
    "Assess the assigned transit stop independently using the evidence source you record. Do not view "
    "existing Stop-GIS labels or other reviewers' answers before submission. Record whether you already "
    "recognized the location. Submit one assessment for the stop; submitted ratings are locked."
)
DEFAULT_INSTRUCTIONS = DEFAULT_IMAGE_INSTRUCTIONS

PROTOCOL_COLUMNS = [
    "project_id",
    "phase",
    "codebook_version",
    "target_ratings",
    "assessment_unit",
    "agreement_threshold",
    "instructions",
    "created_at",
    "updated_at",
]


class BlindCodingError(ValueError):
    """Raised when a blind-coding operation would violate the protocol."""


def _clean_required(value: Any, label: str) -> str:
    text = str(clean_scalar(value) or "").strip()
    if not text:
        raise BlindCodingError(f"{label} is required")
    return text


def _validate_choice(value: Any, field: str, options: Iterable[str]) -> str:
    text = str(clean_scalar(value) or "").strip().lower()
    if text not in options:
        raise BlindCodingError(f"{field} must be one of: {', '.join(options)}")
    return text


def get_blind_protocol(project_id: str, path: Path | None = None) -> dict[str, Any]:
    """Return the saved protocol or setup defaults without creating database state."""
    init_database(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM blind_protocols WHERE project_id = ?", (project_id,)
        ).fetchone()
    if row:
        return {column: row[column] for column in PROTOCOL_COLUMNS}
    return {
        "project_id": project_id,
        "phase": "setup",
        "codebook_version": "1.0",
        "target_ratings": 3,
        "assessment_unit": "image",
        "agreement_threshold": 0.67,
        "instructions": DEFAULT_INSTRUCTIONS,
        "created_at": "",
        "updated_at": "",
    }


def configure_blind_protocol(
    project_id: str,
    *,
    codebook_version: str,
    target_ratings: int = 3,
    assessment_unit: str = "image",
    agreement_threshold: float = 0.67,
    instructions: str = DEFAULT_INSTRUCTIONS,
    path: Path | None = None,
) -> dict[str, Any]:
    """Create or edit a protocol while it is still in setup."""
    codebook_version = _clean_required(codebook_version, "Codebook version")
    target_ratings = int(target_ratings)
    agreement_threshold = float(agreement_threshold)
    assessment_unit = _validate_choice(assessment_unit, "assessment_unit", ASSESSMENT_UNITS)
    if target_ratings < 3:
        raise BlindCodingError("Independent review requires at least three ratings per unit")
    if not 0 <= agreement_threshold <= 1:
        raise BlindCodingError("Agreement threshold must be between 0 and 1")
    instructions = str(clean_scalar(instructions) or "").strip()
    if not instructions or (assessment_unit == "stop" and instructions == DEFAULT_IMAGE_INSTRUCTIONS):
        instructions = DEFAULT_STOP_INSTRUCTIONS if assessment_unit == "stop" else DEFAULT_IMAGE_INSTRUCTIONS

    init_database(path)
    now = utc_timestamp()
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT phase, created_at, assessment_unit FROM blind_protocols WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if existing and existing["phase"] != "setup":
            raise BlindCodingError("Protocol settings are locked after coding starts")
        if existing and existing["assessment_unit"] != assessment_unit:
            assignment_count = conn.execute(
                "SELECT (SELECT COUNT(*) FROM blind_assignments WHERE project_id = ?) + "
                "(SELECT COUNT(*) FROM blind_stop_assignments WHERE project_id = ?)",
                (project_id, project_id),
            ).fetchone()[0]
            if assignment_count:
                raise BlindCodingError(
                    "Assessment unit cannot change after assignments have been created"
                )
        created_at = existing["created_at"] if existing else now
        conn.execute(
            """
            INSERT INTO blind_protocols (
                project_id, phase, codebook_version, target_ratings, assessment_unit,
                agreement_threshold, instructions, created_at, updated_at
            ) VALUES (?, 'setup', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                codebook_version = excluded.codebook_version,
                target_ratings = excluded.target_ratings,
                assessment_unit = excluded.assessment_unit,
                agreement_threshold = excluded.agreement_threshold,
                instructions = excluded.instructions,
                updated_at = excluded.updated_at
            """,
            (
                project_id,
                codebook_version,
                target_ratings,
                assessment_unit,
                agreement_threshold,
                instructions,
                created_at,
                now,
            ),
        )
        conn.commit()
    return get_blind_protocol(project_id, path)


def _new_display_id(existing: set[str], prefix: str = "IMG") -> str:
    for _ in range(1000):
        candidate = f"{prefix}-{secrets.randbelow(1_000_000):06d}"
        if candidate not in existing:
            existing.add(candidate)
            return candidate
    raise BlindCodingError("Could not generate a unique blinded image ID")


def create_blind_assignments(
    project_id: str,
    coder_ids: Iterable[str],
    unit_ids: Iterable[str] | None = None,
    path: Path | None = None,
) -> int:
    """Assign selected images or stops to every coder in an independent random order."""
    protocol = get_blind_protocol(project_id, path)
    if not protocol["created_at"]:
        protocol = configure_blind_protocol(
            project_id,
            codebook_version=protocol["codebook_version"],
            target_ratings=protocol["target_ratings"],
            assessment_unit=protocol["assessment_unit"],
            agreement_threshold=protocol["agreement_threshold"],
            instructions=protocol["instructions"],
            path=path,
        )
    if protocol["phase"] != "setup":
        raise BlindCodingError("Assignments can only be created during setup")
    coders = list(dict.fromkeys(_clean_required(value, "Coder ID") for value in coder_ids))
    if len(coders) < int(protocol["target_ratings"]):
        raise BlindCodingError(
            f"At least {int(protocol['target_ratings'])} distinct coders are required"
        )

    selected_ids = None if unit_ids is None else list(
        dict.fromkeys(str(value).strip() for value in unit_ids if str(value).strip())
    )
    if selected_ids == []:
        raise BlindCodingError("Select at least one assessment unit")
    init_database(path)
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        locked_protocol = conn.execute(
            "SELECT phase, assessment_unit, target_ratings FROM blind_protocols WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if not locked_protocol or locked_protocol["phase"] != "setup":
            raise BlindCodingError("Assignments can only be created during setup")
        if int(locked_protocol["target_ratings"]) != int(protocol["target_ratings"]):
            raise BlindCodingError("Protocol settings changed; reload before creating assignments")
        if str(locked_protocol["assessment_unit"]) != str(protocol["assessment_unit"]):
            raise BlindCodingError("Protocol settings changed; reload before creating assignments")
        assessment_unit = str(locked_protocol["assessment_unit"])
        source_table = "images" if assessment_unit == "image" else "stops"
        source_key = "id" if assessment_unit == "image" else "stop_id"
        unit_label = "Images" if assessment_unit == "image" else "Stops"
        if selected_ids is not None:
            placeholders = ",".join("?" for _ in selected_ids)
            source_rows = conn.execute(
                f"SELECT {source_key} AS unit_id FROM {source_table} "
                f"WHERE project_id = ? AND {source_key} IN ({placeholders}) ORDER BY {source_key}",
                [project_id, *selected_ids],
            ).fetchall()
            found = {str(row["unit_id"]) for row in source_rows}
            missing = sorted(set(selected_ids) - found)
            if missing:
                raise BlindCodingError(
                    f"{unit_label} are not registered to this project: {', '.join(missing)}"
                )
        else:
            source_rows = conn.execute(
                f"SELECT {source_key} AS unit_id FROM {source_table} "
                f"WHERE project_id = ? ORDER BY {source_key}",
                (project_id,),
            ).fetchall()
        if not source_rows:
            requirement = "standardized image" if assessment_unit == "image" else "transit stop"
            raise BlindCodingError(f"Add at least one {requirement} before assigning coders")

        blind_table = "blind_images" if assessment_unit == "image" else "blind_stops"
        blind_source_key = "image_id" if assessment_unit == "image" else "stop_id"
        existing_aliases = {
            str(row["display_id"])
            for row in conn.execute(
                f"SELECT display_id FROM {blind_table} WHERE project_id = ?", (project_id,)
            ).fetchall()
        }
        now = utc_timestamp()
        blind_rows: list[str] = []
        for source_row in source_rows:
            source_id = str(source_row["unit_id"])
            existing = conn.execute(
                f"SELECT id FROM {blind_table} WHERE project_id = ? AND {blind_source_key} = ?",
                (project_id, source_id),
            ).fetchone()
            if existing:
                blind_id = str(existing["id"])
            else:
                blind_id = str(uuid.uuid4())
                conn.execute(
                    f"INSERT INTO {blind_table} "
                    f"(id, project_id, {blind_source_key}, display_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        blind_id,
                        project_id,
                        source_id,
                        _new_display_id(existing_aliases, "IMG" if assessment_unit == "image" else "STOP"),
                        now,
                    ),
                )
            blind_rows.append(blind_id)

        created = 0
        rng = secrets.SystemRandom()
        assignment_table = "blind_assignments" if assessment_unit == "image" else "blind_stop_assignments"
        blind_fk = "blind_image_id" if assessment_unit == "image" else "blind_stop_id"
        for coder_id in coders:
            existing_units = {
                str(row[blind_fk])
                for row in conn.execute(
                    f"SELECT {blind_fk} FROM {assignment_table} WHERE project_id = ? AND coder_id = ?",
                    (project_id, coder_id),
                ).fetchall()
            }
            randomized = [blind_id for blind_id in blind_rows if blind_id not in existing_units]
            rng.shuffle(randomized)
            max_order = conn.execute(
                f"SELECT COALESCE(MAX(sort_order), 0) FROM {assignment_table} "
                "WHERE project_id = ? AND coder_id = ?",
                (project_id, coder_id),
            ).fetchone()[0]
            for sort_order, blind_id in enumerate(randomized, start=int(max_order) + 1):
                cursor = conn.execute(
                    f"INSERT OR IGNORE INTO {assignment_table} "
                    f"(id, project_id, {blind_fk}, coder_id, sort_order, status, assigned_at) "
                    "VALUES (?, ?, ?, ?, ?, 'assigned', ?)",
                    (str(uuid.uuid4()), project_id, blind_id, coder_id, sort_order, now),
                )
                created += max(cursor.rowcount, 0)
        conn.commit()
    return created


def blind_progress(project_id: str, path: Path | None = None) -> dict[str, int]:
    protocol = get_blind_protocol(project_id, path)
    assessment_unit = str(protocol["assessment_unit"])
    assignment_table = "blind_assignments" if assessment_unit == "image" else "blind_stop_assignments"
    blind_fk = "blind_image_id" if assessment_unit == "image" else "blind_stop_id"
    init_database(path)
    with connect(path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS assignments,
                   COUNT(DISTINCT coder_id) AS coders,
                   COUNT(DISTINCT {blind_fk}) AS units,
                   SUM(CASE WHEN status = 'submitted' THEN 1 ELSE 0 END) AS submitted
            FROM {assignment_table} WHERE project_id = ?
            """.format(blind_fk=blind_fk, assignment_table=assignment_table),
            (project_id,),
        ).fetchone()
    assignments = int(row["assignments"] or 0)
    submitted = int(row["submitted"] or 0)
    progress = {
        "assignments": assignments,
        "submitted": submitted,
        "remaining": max(assignments - submitted, 0),
        "coders": int(row["coders"] or 0),
    }
    progress["images" if assessment_unit == "image" else "stops"] = int(row["units"] or 0)
    return progress


def advance_blind_phase(project_id: str, path: Path | None = None) -> str:
    """Advance setup -> coding -> adjudication -> closed after required checks."""
    init_database(path)
    now = utc_timestamp()
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        protocol = conn.execute(
            "SELECT * FROM blind_protocols WHERE project_id = ?", (project_id,)
        ).fetchone()
        if not protocol:
            raise BlindCodingError("Configure the blind protocol before advancing it")
        current = str(protocol["phase"])
        next_phase = PHASE_TRANSITIONS.get(current)
        if not next_phase:
            raise BlindCodingError("The blind protocol is already closed")
        assessment_unit = str(protocol["assessment_unit"])
        assignment_table = "blind_assignments" if assessment_unit == "image" else "blind_stop_assignments"
        blind_fk = "blind_image_id" if assessment_unit == "image" else "blind_stop_id"
        progress = conn.execute(
            f"""
            SELECT COUNT(*) AS assignments,
                   SUM(CASE WHEN status = 'submitted' THEN 1 ELSE 0 END) AS submitted,
                   COUNT(DISTINCT {blind_fk}) AS units
            FROM {assignment_table} WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
        assignments = int(progress["assignments"] or 0)
        remaining = max(assignments - int(progress["submitted"] or 0), 0)
        if current == "setup":
            if int(progress["units"] or 0) == 0 or assignments == 0:
                raise BlindCodingError("Create assignments before starting coding")
            under_target = conn.execute(
                f"SELECT COUNT(*) FROM (SELECT {blind_fk} FROM {assignment_table} "
                "WHERE project_id = ? GROUP BY " + blind_fk + " HAVING COUNT(DISTINCT coder_id) < ?)",
                (project_id, int(protocol["target_ratings"])),
            ).fetchone()[0]
            if under_target:
                raise BlindCodingError("Every assessment unit must have the target number of coders")
        elif current == "coding" and remaining:
            raise BlindCodingError(
                f"Coding cannot close while {remaining} assignments remain"
            )
        elif current == "adjudication":
            queue = blind_adjudication_queue(
                project_id,
                path,
                _connection=conn,
                _protocol=protocol,
            )
            pending = queue[
                queue["needs_adjudication"].astype(bool)
                & queue["status"].ne("Adjudicated")
            ] if not queue.empty else queue
            if not pending.empty:
                decision_label = "decision remains" if len(pending) == 1 else "decisions remain"
                raise BlindCodingError(
                    f"Adjudication cannot close while {len(pending)} required {decision_label}"
                )
        cursor = conn.execute(
            "UPDATE blind_protocols SET phase = ?, updated_at = ? WHERE project_id = ? AND phase = ?",
            (next_phase, now, project_id, current),
        )
        if cursor.rowcount != 1:
            raise BlindCodingError("Protocol phase changed; reload before trying again")
        conn.commit()
    return next_phase


def list_coder_assignments(
    project_id: str,
    coder_id: str,
    path: Path | None = None,
) -> pd.DataFrame:
    """Return only the neutral fields needed by one coder's interface."""
    coder_id = _clean_required(coder_id, "Coder ID")
    protocol = get_blind_protocol(project_id, path)
    assessment_unit = str(protocol["assessment_unit"])
    init_database(path)
    with connect(path) as conn:
        if assessment_unit == "image":
            rows = conn.execute(
                """
                SELECT a.id AS assignment_id, bi.display_id, i.uri, i.storage_path,
                       a.sort_order, a.status, a.submitted_at
                FROM blind_assignments AS a
                JOIN blind_images AS bi ON bi.id = a.blind_image_id
                JOIN images AS i ON i.id = bi.image_id
                WHERE a.project_id = ? AND a.coder_id = ?
                ORDER BY a.sort_order, bi.display_id
                """,
                (project_id, coder_id),
            ).fetchall()
            columns = ["assignment_id", "display_id", "uri", "storage_path", "sort_order", "status", "submitted_at"]
            return pd.DataFrame([dict(row) for row in rows]) if rows else empty_dataframe(columns)
        rows = conn.execute(
            """
            SELECT a.id AS assignment_id, bs.display_id, s.stop_id, s.stop_name,
                   s.stop_lat, s.stop_lon, a.sort_order, a.status, a.submitted_at
            FROM blind_stop_assignments AS a
            JOIN blind_stops AS bs ON bs.id = a.blind_stop_id
            JOIN stops AS s ON s.project_id = bs.project_id AND s.stop_id = bs.stop_id
            WHERE a.project_id = ? AND a.coder_id = ?
            ORDER BY a.sort_order, bs.display_id
            """,
            (project_id, coder_id),
        ).fetchall()
        records = [dict(row) for row in rows]
        for record in records:
            evidence = conn.execute(
                "SELECT uri, storage_path FROM images WHERE project_id = ? AND stop_id = ? ORDER BY created_at",
                (project_id, record["stop_id"]),
            ).fetchall()
            record["evidence_sources"] = [
                str(item["storage_path"] or item["uri"] or "") for item in evidence
                if item["storage_path"] or item["uri"]
            ]
    columns = [
        "assignment_id", "display_id", "stop_id", "stop_name", "stop_lat", "stop_lon",
        "evidence_sources", "sort_order", "status", "submitted_at",
    ]
    return pd.DataFrame(records) if records else empty_dataframe(columns)


def _validated_rating(rating: dict[str, Any]) -> dict[str, Any]:
    clean = {
        field: _validate_choice(rating.get(field), field, options)
        for field, options in RATING_FIELDS.items()
    }
    clean["location_recognized"] = _validate_choice(
        rating.get("location_recognized"), "location_recognized", LOCATION_RECOGNIZED_OPTIONS
    )
    try:
        clean["confidence"] = int(rating.get("confidence"))
    except (TypeError, ValueError) as error:
        raise BlindCodingError("Confidence must be an integer from 1 to 5") from error
    if clean["confidence"] not in range(1, 6):
        raise BlindCodingError("Confidence must be an integer from 1 to 5")
    clean["review_method"] = _validate_choice(
        rating.get("review_method", "standardized_image"),
        "review_method",
        REVIEW_METHOD_OPTIONS,
    )
    return clean


def submit_blind_rating(
    project_id: str,
    assignment_id: str,
    coder_id: str,
    rating: dict[str, Any],
    path: Path | None = None,
) -> str:
    """Lock one independent rating. Duplicate submissions are rejected."""
    coder_id = _clean_required(coder_id, "Coder ID")
    clean = _validated_rating(rating)
    protocol = get_blind_protocol(project_id, path)
    if protocol["phase"] != "coding":
        raise BlindCodingError("Ratings can only be submitted while coding is open")
    now = utc_timestamp()
    rating_id = str(uuid.uuid4())
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        locked_protocol = conn.execute(
            "SELECT phase, assessment_unit, codebook_version FROM blind_protocols WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if not locked_protocol or locked_protocol["phase"] != "coding":
            raise BlindCodingError("Ratings can only be submitted while coding is open")
        assessment_unit = str(locked_protocol["assessment_unit"])
        assignment_table = "blind_assignments" if assessment_unit == "image" else "blind_stop_assignments"
        rating_table = "blind_ratings" if assessment_unit == "image" else "blind_stop_ratings"
        assignment = conn.execute(
            f"SELECT status FROM {assignment_table} "
            "WHERE id = ? AND project_id = ? AND coder_id = ?",
            (assignment_id, project_id, coder_id),
        ).fetchone()
        if not assignment:
            raise BlindCodingError("Assignment was not found for this coder")
        if assignment["status"] == "submitted":
            raise BlindCodingError("This rating is locked and cannot be replaced")
        conn.execute(
            """
            INSERT INTO {rating_table} (
                id, project_id, assignment_id, codebook_version, shade_source, coverage,
                waiting_area_covered, permanence, image_adequacy, confidence,
                location_recognized, review_method, submitted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """.format(rating_table=rating_table),
            (
                rating_id,
                project_id,
                assignment_id,
                locked_protocol["codebook_version"],
                clean["shade_source"],
                clean["coverage"],
                clean["waiting_area_covered"],
                clean["permanence"],
                clean["image_adequacy"],
                clean["confidence"],
                clean["location_recognized"],
                clean["review_method"],
                now,
            ),
        )
        conn.execute(
            f"UPDATE {assignment_table} SET status = 'submitted', submitted_at = ? WHERE id = ?",
            (now, assignment_id),
        )
        conn.commit()
    return rating_id


def _require_results_open(project_id: str, path: Path | None = None) -> dict[str, Any]:
    protocol = get_blind_protocol(project_id, path)
    if protocol["phase"] in {"setup", "coding"}:
        raise BlindCodingError("Agreement and consensus remain hidden until coding closes")
    return protocol


def export_blind_ratings(project_id: str, path: Path | None = None) -> pd.DataFrame:
    """Return the administrator research export after the independent coding phase."""
    protocol = _require_results_open(project_id, path)
    assessment_unit = str(protocol["assessment_unit"])
    with connect(path) as conn:
        if assessment_unit == "image":
            rows = conn.execute(
                """
                SELECT 'image' AS assessment_unit, bi.display_id AS blinded_unit_id,
                       bi.display_id AS blinded_image_id, bi.image_id, i.stop_id,
                       a.coder_id AS rater_id, r.codebook_version, r.shade_source,
                       r.coverage, r.waiting_area_covered, r.permanence,
                       r.image_adequacy, r.confidence, r.location_recognized,
                       r.review_method, r.submitted_at
                FROM blind_ratings AS r
                JOIN blind_assignments AS a ON a.id = r.assignment_id
                JOIN blind_images AS bi ON bi.id = a.blind_image_id
                JOIN images AS i ON i.id = bi.image_id
                WHERE r.project_id = ?
                ORDER BY bi.display_id, a.coder_id
                """,
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT 'stop' AS assessment_unit, bs.display_id AS blinded_unit_id,
                       NULL AS blinded_image_id, NULL AS image_id, bs.stop_id,
                       a.coder_id AS rater_id, r.codebook_version, r.shade_source,
                       r.coverage, r.waiting_area_covered, r.permanence,
                       r.image_adequacy, r.confidence, r.location_recognized,
                       r.review_method, r.submitted_at
                FROM blind_stop_ratings AS r
                JOIN blind_stop_assignments AS a ON a.id = r.assignment_id
                JOIN blind_stops AS bs ON bs.id = a.blind_stop_id
                WHERE r.project_id = ?
                ORDER BY bs.display_id, a.coder_id
                """,
                (project_id,),
            ).fetchall()
    columns = [
        "assessment_unit", "blinded_unit_id", "blinded_image_id", "image_id", "stop_id",
        "rater_id", "codebook_version", *RATING_FIELDS, "confidence",
        "location_recognized", "review_method", "submitted_at",
    ]
    return pd.DataFrame([dict(row) for row in rows]) if rows else empty_dataframe(columns)


def _analysis_ratings(
    project_id: str,
    path: Path | None = None,
    *,
    _connection: Any | None = None,
    _protocol: Any | None = None,
) -> pd.DataFrame:
    protocol = _protocol or _require_results_open(project_id, path)
    assessment_unit = str(protocol["assessment_unit"])

    def load_rows(conn: Any) -> list[Any]:
        if assessment_unit == "image":
            return conn.execute(
                """
                SELECT bi.id AS blind_unit_id, bi.id AS blind_image_id,
                       NULL AS blind_stop_id, bi.display_id, r.shade_source, r.coverage,
                       r.waiting_area_covered, r.permanence, r.image_adequacy
                FROM blind_ratings AS r
                JOIN blind_assignments AS a ON a.id = r.assignment_id
                JOIN blind_images AS bi ON bi.id = a.blind_image_id
                WHERE r.project_id = ? ORDER BY bi.display_id
                """,
                (project_id,),
            ).fetchall()
        return conn.execute(
                """
                SELECT bs.id AS blind_unit_id, NULL AS blind_image_id,
                       bs.id AS blind_stop_id, bs.display_id, r.shade_source, r.coverage,
                       r.waiting_area_covered, r.permanence, r.image_adequacy
                FROM blind_stop_ratings AS r
                JOIN blind_stop_assignments AS a ON a.id = r.assignment_id
                JOIN blind_stops AS bs ON bs.id = a.blind_stop_id
                WHERE r.project_id = ? ORDER BY bs.display_id
                """,
                (project_id,),
        ).fetchall()

    if _connection is None:
        with connect(path) as conn:
            rows = load_rows(conn)
    else:
        rows = load_rows(_connection)
    columns = ["blind_unit_id", "blind_image_id", "blind_stop_id", "display_id", *RATING_FIELDS]
    return pd.DataFrame([dict(row) for row in rows]) if rows else empty_dataframe(columns)


def pairwise_percent_agreement(
    ratings: pd.DataFrame,
    field: str,
    unit_field: str = "blind_image_id",
) -> float | None:
    if ratings.empty or field not in ratings or unit_field not in ratings:
        return None
    agreements = 0
    comparisons = 0
    for _, group in ratings.dropna(subset=[field]).groupby(unit_field):
        values = group[field].astype(str).tolist()
        for left, right in itertools.combinations(values, 2):
            comparisons += 1
            agreements += int(left == right)
    return agreements / comparisons if comparisons else None


def krippendorff_alpha(
    ratings: pd.DataFrame,
    field: str,
    *,
    ordered_categories: Iterable[str] | None = None,
    unit_field: str = "blind_image_id",
) -> float | None:
    """Krippendorff alpha with nominal or squared ordinal disagreement."""
    if ratings.empty or field not in ratings or unit_field not in ratings:
        return None
    clean = ratings[[unit_field, field]].dropna()
    clean[field] = clean[field].astype(str)
    grouped = [group[field].tolist() for _, group in clean.groupby(unit_field) if len(group) >= 2]
    if not grouped:
        return None
    categories = list(ordered_categories or sorted(clean[field].unique().tolist()))
    category_index = {category: index for index, category in enumerate(categories)}

    def distance(left: str, right: str) -> float:
        if ordered_categories is None:
            return 0.0 if left == right else 1.0
        if left not in category_index or right not in category_index or len(categories) < 2:
            return 0.0 if left == right else 1.0
        scale = len(categories) - 1
        return ((category_index[left] - category_index[right]) / scale) ** 2

    observed_numerator = 0.0
    observed_denominator = 0
    for values in grouped:
        unit_denominator = len(values) - 1
        for left, right in itertools.permutations(values, 2):
            observed_numerator += distance(left, right) / unit_denominator
        # Coincidence matrices contribute n values per unit, independently of
        # how many raters happened to score that unit.
        observed_denominator += len(values)
    if observed_denominator == 0:
        return None
    observed = observed_numerator / observed_denominator

    population = list(itertools.chain.from_iterable(grouped))
    if len(population) < 2:
        return None
    expected_numerator = 0.0
    expected_denominator = 0
    for left, right in itertools.permutations(population, 2):
        expected_numerator += distance(left, right)
        expected_denominator += 1
    expected = expected_numerator / expected_denominator
    if math.isclose(expected, 0.0):
        return 1.0 if math.isclose(observed, 0.0) else None
    return 1.0 - observed / expected


def blind_agreement_report(project_id: str, path: Path | None = None) -> pd.DataFrame:
    ratings = _analysis_ratings(project_id, path)
    records = []
    for field in RATING_FIELDS:
        alpha = krippendorff_alpha(
            ratings,
            field,
            ordered_categories=COVERAGE_OPTIONS if field == "coverage" else None,
            unit_field="blind_unit_id",
        )
        percent = pairwise_percent_agreement(ratings, field, unit_field="blind_unit_id")
        records.append(
            {
                "variable": field,
                "ratings": int(ratings[field].notna().sum()) if field in ratings else 0,
                "units": int(ratings.loc[ratings[field].notna(), "blind_unit_id"].nunique()) if field in ratings else 0,
                "percent_agreement": percent,
                "krippendorff_alpha": alpha,
                "measurement": "ordinal" if field == "coverage" else "nominal",
            }
        )
    return pd.DataFrame.from_records(records)


def _item_field_summary(group: pd.DataFrame, field: str) -> tuple[str, float | None]:
    values = group[field].dropna().astype(str).tolist()
    if not values:
        return "", None
    counts = Counter(values)
    summary = ", ".join(f"{count} {value}" for value, count in sorted(counts.items()))
    pairs = list(itertools.combinations(values, 2))
    agreement = sum(left == right for left, right in pairs) / len(pairs) if pairs else None
    return summary, agreement


def blind_adjudication_queue(
    project_id: str,
    path: Path | None = None,
    *,
    _connection: Any | None = None,
    _protocol: Any | None = None,
) -> pd.DataFrame:
    protocol = _protocol or _require_results_open(project_id, path)
    assessment_unit = str(protocol["assessment_unit"])
    ratings = _analysis_ratings(
        project_id,
        path,
        _connection=_connection,
        _protocol=protocol,
    )
    if ratings.empty:
        return empty_dataframe(
            ["blind_unit_id", "blind_image_id", "blind_stop_id", "display_id", "ratings_completed", "agreement", "status", *RATING_FIELDS]
        )
    def load_details(conn: Any) -> dict[str, dict[str, Any]]:
        if assessment_unit == "image":
            detail_rows = conn.execute(
                """
                SELECT bi.id AS blind_unit_id, i.uri, i.storage_path,
                       CASE WHEN ba.id IS NULL THEN 0 ELSE 1 END AS adjudicated
                FROM blind_images AS bi
                JOIN images AS i ON i.id = bi.image_id
                LEFT JOIN blind_adjudications AS ba
                  ON ba.blind_image_id = bi.id AND ba.project_id = bi.project_id
                WHERE bi.project_id = ?
                """,
                (project_id,),
            ).fetchall()
        else:
            detail_rows = conn.execute(
                """
                SELECT bs.id AS blind_unit_id, s.stop_id, s.stop_name, s.stop_lat, s.stop_lon,
                       CASE WHEN ba.id IS NULL THEN 0 ELSE 1 END AS adjudicated
                FROM blind_stops AS bs
                JOIN stops AS s ON s.project_id = bs.project_id AND s.stop_id = bs.stop_id
                LEFT JOIN blind_stop_adjudications AS ba
                  ON ba.blind_stop_id = bs.id AND ba.project_id = bs.project_id
                WHERE bs.project_id = ?
                """,
                (project_id,),
            ).fetchall()
        loaded_details = {str(row["blind_unit_id"]): dict(row) for row in detail_rows}
        if assessment_unit == "stop":
            for detail in loaded_details.values():
                evidence = conn.execute(
                    "SELECT uri, storage_path FROM images WHERE project_id = ? AND stop_id = ? "
                    "ORDER BY created_at",
                    (project_id, detail["stop_id"]),
                ).fetchall()
                detail["evidence_sources"] = [
                    str(item["storage_path"] or item["uri"] or "")
                    for item in evidence
                    if item["storage_path"] or item["uri"]
                ]
        return loaded_details

    if _connection is None:
        with connect(path) as conn:
            details = load_details(conn)
    else:
        details = load_details(_connection)
    records = []
    threshold = float(protocol["agreement_threshold"])
    for blind_unit_id, group in ratings.groupby("blind_unit_id", sort=False):
        record: dict[str, Any] = {
            "blind_unit_id": blind_unit_id,
            "blind_image_id": blind_unit_id if assessment_unit == "image" else None,
            "blind_stop_id": blind_unit_id if assessment_unit == "stop" else None,
            "display_id": str(group["display_id"].iloc[0]),
            "ratings_completed": len(group),
        }
        agreements = []
        for field in RATING_FIELDS:
            summary, agreement = _item_field_summary(group, field)
            record[field] = summary
            if agreement is not None:
                agreements.append(agreement)
        record["agreement"] = sum(agreements) / len(agreements) if agreements else None
        record["lowest_field_agreement"] = min(agreements) if agreements else None
        detail = details.get(str(blind_unit_id), {})
        record["uri"] = detail.get("uri", "")
        record["storage_path"] = detail.get("storage_path", "")
        record["stop_id"] = detail.get("stop_id", "")
        record["stop_name"] = detail.get("stop_name", "")
        record["stop_lat"] = detail.get("stop_lat")
        record["stop_lon"] = detail.get("stop_lon")
        record["evidence_sources"] = detail.get("evidence_sources", [])
        record["status"] = "Adjudicated" if detail.get("adjudicated") else "Pending"
        record["needs_adjudication"] = bool(
            record["lowest_field_agreement"] is not None
            and record["lowest_field_agreement"] < threshold
        )
        records.append(record)
    queue = pd.DataFrame.from_records(records)
    return queue.sort_values(
        ["needs_adjudication", "status", "agreement", "display_id"],
        ascending=[False, False, True, True],
    ).reset_index(drop=True)


def submit_blind_adjudication(
    project_id: str,
    blind_unit_id: str,
    adjudicator_id: str,
    decision: dict[str, Any],
    *,
    notes: str = "",
    path: Path | None = None,
) -> str:
    adjudicator_id = _clean_required(adjudicator_id, "Adjudicator ID")
    clean = _validated_rating({**decision, "location_recognized": "no"})
    adjudication_id = str(uuid.uuid4())
    now = utc_timestamp()
    init_database(path)
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        protocol = conn.execute(
            "SELECT phase, assessment_unit, codebook_version FROM blind_protocols WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if not protocol or protocol["phase"] != "adjudication":
            raise BlindCodingError("Adjudication is only available after coding closes")
        assessment_unit = str(protocol["assessment_unit"])
        blind_table = "blind_images" if assessment_unit == "image" else "blind_stops"
        adjudication_table = "blind_adjudications" if assessment_unit == "image" else "blind_stop_adjudications"
        blind_fk = "blind_image_id" if assessment_unit == "image" else "blind_stop_id"
        unit = conn.execute(
            f"SELECT 1 FROM {blind_table} WHERE id = ? AND project_id = ?",
            (blind_unit_id, project_id),
        ).fetchone()
        if not unit:
            raise BlindCodingError("Blinded assessment unit was not found")
        existing = conn.execute(
            f"SELECT 1 FROM {adjudication_table} WHERE project_id = ? AND {blind_fk} = ?",
            (project_id, blind_unit_id),
        ).fetchone()
        if existing:
            raise BlindCodingError("This adjudicated decision is already locked")
        conn.execute(
            """
            INSERT INTO {adjudication_table} (
                id, project_id, {blind_fk}, adjudicator_id, codebook_version,
                shade_source, coverage, waiting_area_covered, permanence,
                image_adequacy, confidence, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """.format(adjudication_table=adjudication_table, blind_fk=blind_fk),
            (
                adjudication_id,
                project_id,
                blind_unit_id,
                adjudicator_id,
                protocol["codebook_version"],
                clean["shade_source"],
                clean["coverage"],
                clean["waiting_area_covered"],
                clean["permanence"],
                clean["image_adequacy"],
                clean["confidence"],
                str(clean_scalar(notes) or "").strip(),
                now,
            ),
        )
        conn.commit()
    return adjudication_id


def export_blind_adjudications(project_id: str, path: Path | None = None) -> pd.DataFrame:
    protocol = _require_results_open(project_id, path)
    assessment_unit = str(protocol["assessment_unit"])
    with connect(path) as conn:
        if assessment_unit == "image":
            rows = conn.execute(
                """
                SELECT 'image' AS assessment_unit, bi.display_id AS blinded_unit_id,
                       bi.display_id AS blinded_image_id, bi.image_id, i.stop_id,
                       ba.adjudicator_id, ba.codebook_version, ba.shade_source, ba.coverage,
                       ba.waiting_area_covered, ba.permanence, ba.image_adequacy,
                       ba.confidence, ba.notes, ba.created_at
                FROM blind_adjudications AS ba
                JOIN blind_images AS bi ON bi.id = ba.blind_image_id
                JOIN images AS i ON i.id = bi.image_id
                WHERE ba.project_id = ? ORDER BY bi.display_id
                """,
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT 'stop' AS assessment_unit, bs.display_id AS blinded_unit_id,
                       NULL AS blinded_image_id, NULL AS image_id, bs.stop_id,
                       ba.adjudicator_id, ba.codebook_version, ba.shade_source, ba.coverage,
                       ba.waiting_area_covered, ba.permanence, ba.image_adequacy,
                       ba.confidence, ba.notes, ba.created_at
                FROM blind_stop_adjudications AS ba
                JOIN blind_stops AS bs ON bs.id = ba.blind_stop_id
                WHERE ba.project_id = ? ORDER BY bs.display_id
                """,
                (project_id,),
            ).fetchall()
    columns = [
        "assessment_unit", "blinded_unit_id", "blinded_image_id", "image_id", "stop_id",
        "adjudicator_id", "codebook_version", *RATING_FIELDS, "confidence", "notes", "created_at",
    ]
    return pd.DataFrame([dict(row) for row in rows]) if rows else empty_dataframe(columns)


def protocol_json(project_id: str, path: Path | None = None) -> str:
    """Export the prespecified protocol without exposing submitted ratings."""
    protocol = get_blind_protocol(project_id, path)
    payload = {
        **{key: protocol[key] for key in ["phase", "codebook_version", "target_ratings", "assessment_unit", "agreement_threshold", "instructions"]},
        "coding_fields": {field: list(options) for field, options in RATING_FIELDS.items()},
        "definitions": CODEBOOK_DEFINITIONS,
        "confidence": {"minimum": 1, "maximum": 5},
        "location_recognized": list(LOCATION_RECOGNIZED_OPTIONS),
        "submission_policy": "immutable",
        "rating_unit_policy": "one_rating_per_coder_per_assessment_unit",
        "results_visibility": "withheld_until_adjudication_phase",
    }
    return json.dumps(payload, indent=2)
