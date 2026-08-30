from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from stop_gis.persistence.store import (
    add_adjudication,
    add_assessment,
    create_project,
    init_database,
    list_assessment_modes,
    list_assessments,
    load_project_bundle,
)
from stop_gis.assessment_modes import (
    AssessmentValidationError,
    BUILTIN_MODE_KEYS,
    STUDY_TEMPLATES,
    apply_assessment_values,
    calculate_composite_score,
    categorical_summary,
    filter_by_assessment_values,
    modes_for_template,
    normalize_mode_definition,
    reliability_for_mode,
    validate_assessment_values,
)


def test_builtin_catalog_and_templates_cover_requested_studies():
    assert {"bench", "shelter", "trash_can", "lighting", "passenger_information"}.issubset(BUILTIN_MODE_KEYS)
    assert set(STUDY_TEMPLATES) == {
        "basic_stop_amenities", "accessibility_audit", "passenger_comfort", "custom"
    }
    comfort = {mode["key"] for mode in modes_for_template("passenger_comfort") if mode["enabled"]}
    assert {"bench", "shelter", "shade_coverage", "shade_source", "cleanliness", "traffic_exposure"}.issubset(comfort)


def test_mode_validation_supports_ordinal_and_multiselect_values():
    modes = modes_for_template("passenger_comfort")
    values = validate_assessment_values(
        modes,
        {"shade_coverage": "limited", "shade_source": ["natural", "incidental"]},
    )
    assert values == {
        "shade_coverage": "limited", "shade_source": ["natural", "incidental"]
    }
    shade = {mode["key"]: mode for mode in modes}["shade_coverage"]
    assert shade["measurement_level"] == "ordinal"
    assert shade["ordering"] == ["none", "limited", "significant", "unclear"]


def test_disabled_and_invalid_categorical_values_are_rejected():
    modes = modes_for_template("basic_stop_amenities")
    with pytest.raises(AssessmentValidationError, match="disabled"):
        validate_assessment_values(modes, {"cleanliness": "good"})
    with pytest.raises(AssessmentValidationError, match="Invalid value"):
        validate_assessment_values(modes, {"bench": "luxurious"})


def test_custom_mode_definition_is_generic():
    mode = normalize_mode_definition(
        {
            "key": "snow_clearance",
            "label": "Snow clearance",
            "value_type": "categorical",
            "allowed_values": ["clear", "partial", "blocked"],
            "value_labels": {"partial": "Partly blocked"},
            "value_definitions": {"partial": "Some of the waiting area is blocked."},
            "ordering": ["blocked", "partial", "clear"],
            "enabled": True,
        }
    )
    assert mode["key"] == "snow_clearance"
    assert mode["measurement_level"] == "ordinal"
    assert mode["value_labels"] == {"partial": "Partly blocked"}
    assert mode["value_definitions"] == {
        "partial": "Some of the waiting area is blocked."
    }


def test_existing_assessment_mode_rows_load_after_value_metadata_migration(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE assessment_modes (
                project_id TEXT NOT NULL, mode_key TEXT NOT NULL, label TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '', operational_definition TEXT NOT NULL DEFAULT '',
                value_type TEXT NOT NULL, allowed_values_json TEXT NOT NULL DEFAULT '[]',
                ordering_json TEXT NOT NULL DEFAULT '[]', multiple INTEGER NOT NULL DEFAULT 0,
                allow_comment INTEGER NOT NULL DEFAULT 1, collect_confidence INTEGER NOT NULL DEFAULT 1,
                enabled INTEGER NOT NULL DEFAULT 0, required INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 1, measurement_level TEXT NOT NULL DEFAULT 'nominal',
                scoring_json TEXT NOT NULL DEFAULT '{}', display_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (project_id, mode_key)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO assessment_modes VALUES (
                'saved-project', 'shade_coverage', 'Shade coverage', 'Visible shade.',
                'Classify visible shade.', 'categorical', '["none", "unclear"]',
                '["none", "unclear"]', 0, 1, 1, 1, 0, 1, 'ordinal', '{}', '{}'
            )
            """
        )

    init_database(db_path)
    loaded = list_assessment_modes("saved-project", db_path)

    assert loaded[0]["allowed_values"] == ["none", "unclear"]
    assert loaded[0]["value_labels"] == {}
    assert loaded[0]["value_definitions"] == {}


def _project_with_modes(db_path, project, taxonomy, methodology, visualization, minimal_stops):
    modes = modes_for_template("basic_stop_amenities")
    bench = next(mode for mode in modes if mode["key"] == "bench")
    bench["value_labels"] = {"present": "Usable bench"}
    bench["value_definitions"] = {
        "present": "A designated bench is present and appears usable."
    }
    project_id = create_project(
        project, taxonomy, methodology, visualization, minimal_stops, [], db_path,
        assessment_modes=modes,
    )
    return project_id, modes


def test_modes_and_immutable_assessments_round_trip(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project_id, modes = _project_with_modes(
        db_path, project, taxonomy, methodology, visualization, minimal_stops
    )
    stored_modes = list_assessment_modes(project_id, db_path)
    assert [mode["key"] for mode in stored_modes] == [mode["key"] for mode in modes]
    stored_bench = next(mode for mode in stored_modes if mode["key"] == "bench")
    assert stored_bench["value_labels"] == {"present": "Usable bench"}
    assert stored_bench["value_definitions"]["present"].startswith(
        "A designated bench"
    )

    first_id = add_assessment(
        project_id,
        {
            "stop_id": "1001",
            "reviewer_id": "r1",
            "evidence_method": "field_audit",
            "assessment_values": {"bench": "present", "shelter": "none"},
            "comments": {"bench": "Usable seating"},
            "confidence": {"bench": 0.9, "shelter": 1.0},
        },
        db_path,
        apply_current=True,
    )
    adjudication_id = add_adjudication(
        project_id,
        {
            "stop_id": "1001",
            "reviewer_id": "admin",
            "assessment_values": {"bench": "damaged", "shelter": "none"},
            "supersedes_id": first_id,
        },
        db_path,
    )
    history = list_assessments(project_id, db_path)
    assert history["id"].tolist() == [first_id, adjudication_id]
    assert history["submission_type"].tolist() == ["independent", "adjudication"]
    assert history.iloc[0]["assessment_values"]["bench"] == "present"
    bundle = load_project_bundle(project_id, db_path)
    current = bundle["stops"].set_index("stop_id").loc["1001"]
    assert current["bench"] == "damaged"
    assert current["assessment_values"]["bench"] == "damaged"


def test_map_filter_summary_and_export_projection_are_mode_agnostic():
    frame = pd.DataFrame(
        [
            apply_assessment_values({"stop_id": "1"}, {"bench": "none", "cleanliness": "poor"}),
            apply_assessment_values({"stop_id": "2"}, {"bench": "present", "cleanliness": "good"}),
        ]
    )
    assert filter_by_assessment_values(frame, {"bench": "none"})["stop_id"].tolist() == ["1"]
    bench = next(mode for mode in modes_for_template("basic_stop_amenities") if mode["key"] == "bench")
    summary = categorical_summary(frame, bench).set_index("value")
    assert summary.loc["present", "count"] == 1
    assert summary.loc["present", "percentage"] == 50.0
    assert "assessment_values" in frame.columns


def test_optional_score_excludes_missing_unless_zero_is_explicit():
    modes = modes_for_template("basic_stop_amenities")
    profile = {"weights": {"bench": 1, "shelter": 1}, "missing": "exclude"}
    assert calculate_composite_score({"bench": "present"}, modes, profile) == 100.0
    assert calculate_composite_score(
        {"bench": "present"}, modes, {**profile, "missing": "zero"}
    ) == 50.0
    assert calculate_composite_score({}, modes, profile) is None


def test_generic_reliability_uses_configured_measurement_level():
    mode = next(mode for mode in modes_for_template("passenger_comfort") if mode["key"] == "cleanliness")
    assessments = pd.DataFrame(
        [
            {"stop_id": "1", "reviewer_id": "a", "assessment_values": {"cleanliness": "good"}},
            {"stop_id": "1", "reviewer_id": "b", "assessment_values": {"cleanliness": "fair"}},
            {"stop_id": "2", "reviewer_id": "a", "assessment_values": {"cleanliness": "poor"}},
            {"stop_id": "2", "reviewer_id": "b", "assessment_values": {"cleanliness": "poor"}},
        ]
    )
    result = reliability_for_mode(assessments, mode)
    assert result["measurement_level"] == "ordinal"
    assert result["items"] == 2
    assert result["agreement"] == 0.5


def test_existing_shade_project_is_migrated_without_rewriting_values(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project_id = create_project(
        project, taxonomy, methodology, visualization, minimal_stops, [], db_path
    )
    modes = {mode["key"]: mode for mode in list_assessment_modes(project_id, db_path)}
    assert modes["shade_coverage"]["enabled"] is True
    assert modes["shade_source"]["enabled"] is True
    bundle = load_project_bundle(project_id, db_path)
    assert bundle["stops"].set_index("stop_id").loc["1001", "shading"] == "No Shade"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM shade_taxonomy WHERE project_id = ?", (project_id,)
        ).fetchone()[0] == len(taxonomy)
