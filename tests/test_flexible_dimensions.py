from __future__ import annotations

import io
import json
import zipfile

import pandas as pd
import pytest

import published_app
from stop_gis.persistence.store import (
    add_assessment,
    create_project,
    list_assessment_modes,
    list_assessments,
    load_project_bundle,
    save_assessment_modes,
)
from stop_gis.deploy import DeploymentBundleSpec, build_deployment_bundle
from stop_gis.ui.taxonomy import (
    _dimension_row_label,
    build_custom_dimension,
    is_custom_dimension,
)
from stop_gis import assessment_components
from stop_gis.assessment_components import (
    categorical_control_kind,
    render_assessment_form,
)
from stop_gis.assessment_modes import (
    AssessmentValidationError,
    assessment_codebook,
    normalize_modes,
    validate_assessment_values,
)


def obstruction_dimension() -> dict:
    return build_custom_dimension(
        name="Perceived waiting-area obstruction",
        description="How much an obstruction affects the passenger waiting area.",
        operational_definition="Assess visible objects that reduce usable waiting space.",
        value_type="categorical",
        value_labels=["None", "Minor", "Major"],
        special_values=["Unclear"],
        sort_order=1,
    )


def test_arbitrary_dimension_generation_and_custom_values_are_source_agnostic():
    dimension = obstruction_dimension()
    dimension["value_definitions"] = {
        "major": "The obstruction substantially reduces usable waiting space."
    }

    assert dimension["key"] == "perceived_waiting_area_obstruction"
    assert dimension["label"] == "Perceived waiting-area obstruction"
    assert dimension["allowed_values"] == ["none", "minor", "major", "unclear"]
    assert dimension["value_labels"] == {
        "none": "None",
        "minor": "Minor",
        "major": "Major",
        "unclear": "Unclear",
    }
    assert dimension["enabled"] is True
    assert normalize_modes([dimension])[0]["key"] not in {
        "shade_coverage", "bench", "shelter", "lighting"
    }
    codebook = assessment_codebook([dimension])
    assert codebook["dimensions"][0]["allowed_values"][2]["definition"].startswith(
        "The obstruction"
    )


def test_machine_key_override_is_sanitized_and_special_values_are_optional():
    dimension = build_custom_dimension(
        name="Seating quality",
        description="Condition of seating.",
        operational_definition="Rate the most usable passenger seat.",
        value_type="categorical",
        value_labels=["Good", "Fair", "Poor"],
        special_values=["Not applicable"],
        key_override="Seat Quality 2026",
    )

    assert dimension["key"] == "seat_quality_2026"
    assert dimension["allowed_values"][-1] == "not_applicable"


def test_taxonomy_row_surfaces_custom_state_values_and_disabled_exception():
    dimension = obstruction_dimension()

    label = _dimension_row_label(dimension, expanded=False)

    assert is_custom_dimension(dimension) is True
    assert ":blue-badge[Custom]" in label
    assert "`None` `Minor` `Major` `Unclear`" in label
    assert "Disabled" not in label

    dimension["enabled"] = False
    assert ":gray-badge[Disabled]" in _dimension_row_label(
        dimension, expanded=True
    )


def test_dynamic_labeling_chooses_controls_from_researcher_value_count(monkeypatch):
    calls: list[tuple[str, list[str]]] = []

    class FakeStreamlit:
        @staticmethod
        def segmented_control(label, options, **kwargs):
            calls.append((label, list(options)))
            assert kwargs["format_func"]("major") == "Major"
            return "major"

        @staticmethod
        def button(*args, **kwargs):
            return True

        @staticmethod
        def caption(*args, **kwargs):
            return None

        @staticmethod
        def warning(*args, **kwargs):
            return None

        @staticmethod
        def error(*args, **kwargs):
            return None

    monkeypatch.setattr(assessment_components, "st", FakeStreamlit)
    dimension = obstruction_dimension()
    dimension["allow_comment"] = False
    dimension["collect_confidence"] = False

    payload = render_assessment_form([dimension], key_prefix="custom")

    assert categorical_control_kind(dimension) == "segmented"
    assert calls == [("Perceived waiting-area obstruction", dimension["allowed_values"])]
    assert payload["assessment_values"] == {
        "perceived_waiting_area_obstruction": "major"
    }
    many_values = {**dimension, "allowed_values": [str(index) for index in range(9)]}
    assert categorical_control_kind(many_values) == "selectbox"
    assert categorical_control_kind({**many_values, "multiple": True}) == "multiselect"


def test_custom_dimension_observation_round_trips_and_exports_as_columns(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    dimension = obstruction_dimension()
    project_id = create_project(
        project,
        taxonomy,
        methodology,
        visualization,
        minimal_stops,
        [],
        db_path,
        assessment_modes=[dimension],
    )
    add_assessment(
        project_id,
        {
            "stop_id": "1001",
            "reviewer_id": "researcher-1",
            "assessment_values": {
                "perceived_waiting_area_obstruction": "major"
            },
        },
        db_path,
        apply_current=True,
    )

    bundle = load_project_bundle(project_id, db_path)
    assessments = list_assessments(project_id, db_path)
    current = bundle["stops"].set_index("stop_id").loc["1001"]
    assert current["perceived_waiting_area_obstruction"] == "major"
    assert current["assessment_values"]["perceived_waiting_area_obstruction"] == "major"
    assert assessments.iloc[0]["assessment_values"] == {
        "perceived_waiting_area_obstruction": "major"
    }

    config = {"assessment_modes": bundle["assessment_modes"], "import_log": []}
    exported_stops, exported_assessments = published_app.assessment_export_frames(
        bundle["stops"], assessments, bundle["assessment_modes"]
    )
    assert exported_stops.set_index("stop_id").loc[
        "1001", "perceived_waiting_area_obstruction"
    ] == "major"
    assert exported_assessments.iloc[0]["perceived_waiting_area_obstruction"] == "major"

    catalog = published_app.export_file_catalog(
        bundle["stops"], pd.DataFrame(), config, assessments=assessments
    )
    stops_csv = next(item for item in catalog if item["name"] == "Stops CSV")["data"]
    assert "perceived_waiting_area_obstruction" in pd.read_csv(
        io.BytesIO(stops_csv)
    ).columns
    codebook_file = next(
        item for item in catalog if item["name"] == "Coding Dimensions Codebook"
    )
    codebook = json.loads(codebook_file["data"])
    assert codebook["dimensions"][0]["key"] == dimension["key"]
    assert codebook["dimensions"][0]["missing_values"] == ["unclear"]


def test_publish_bundle_contains_custom_columns_and_machine_readable_codebook(
    minimal_stops
):
    dimension = obstruction_dimension()
    stops = minimal_stops.copy()
    stops["assessment_values"] = [
        {dimension["key"]: "minor"},
        {dimension["key"]: "major"},
    ]
    config = {"assessment_modes": [dimension], "project": {"name": "Custom audit"}}
    spec = DeploymentBundleSpec(
        repository="owner/custom-audit",
        project=config["project"],
        study_id="custom-audit",
        stops=stops,
        raw_labels=pd.DataFrame(),
        config_json=json.dumps(config),
        priority_weights={},
        assessments=pd.DataFrame(
            [
                {
                    "stop_id": "1001",
                    "assessment_values": {dimension["key"]: "minor"},
                }
            ]
        ),
    )

    with zipfile.ZipFile(io.BytesIO(build_deployment_bundle(spec))) as bundle:
        codebook = json.loads(bundle.read("stop_audit_codebook.json"))
        published_stops = pd.read_csv(bundle.open("shade_study_stops.csv"))
        published_assessments = pd.read_csv(
            bundle.open("stop_audit_assessments.csv")
        )

    assert codebook["dimensions"][0]["name"] == dimension["label"]
    assert dimension["key"] in published_stops.columns
    assert dimension["key"] in published_assessments.columns


def test_invalid_custom_value_is_rejected():
    with pytest.raises(AssessmentValidationError, match="Invalid value"):
        validate_assessment_values(
            [obstruction_dimension()],
            {"perceived_waiting_area_obstruction": "blocked_completely"},
        )


def test_disabling_collection_does_not_drop_historical_dimension_from_export():
    dimension = obstruction_dimension()
    dimension["enabled"] = False
    stops = pd.DataFrame(
        [
            {
                "stop_id": "1001",
                "assessment_values": {dimension["key"]: "minor"},
            }
        ]
    )

    exported, _ = published_app.assessment_export_frames(
        stops, None, [dimension]
    )

    assert exported.loc[0, dimension["key"]] == "minor"


def test_observed_dimension_cannot_be_deleted_renamed_or_retyped_without_migration(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    dimension = obstruction_dimension()
    project_id = create_project(
        project,
        taxonomy,
        methodology,
        visualization,
        minimal_stops,
        [],
        db_path,
        assessment_modes=[dimension],
    )
    add_assessment(
        project_id,
        {
            "stop_id": "1001",
            "assessment_values": {dimension["key"]: "major"},
        },
        db_path,
    )

    renamed_key = {**dimension, "key": "renamed_obstruction"}
    with pytest.raises(AssessmentValidationError, match="delete or rename"):
        save_assessment_modes(project_id, [renamed_key], db_path)
    with pytest.raises(AssessmentValidationError, match="add, remove, or reorder"):
        save_assessment_modes(
            project_id,
            [{**dimension, "allowed_values": ["none", "minor", "major"]}],
            db_path,
        )
    with pytest.raises(AssessmentValidationError, match="value type"):
        save_assessment_modes(
            project_id,
            [{**dimension, "value_type": "text", "allowed_values": []}],
            db_path,
        )

    display_rename = {**dimension, "label": "Waiting-area obstruction"}
    saved = save_assessment_modes(project_id, [display_rename], db_path)
    assert saved[0]["key"] == dimension["key"]
    assert list_assessment_modes(project_id, db_path)[0]["label"] == (
        "Waiting-area obstruction"
    )


def test_unused_dimension_can_be_deleted_while_project_keeps_another_dimension(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    dimension = obstruction_dimension()
    retained = build_custom_dimension(
        name="Field note",
        description="Optional field note.",
        operational_definition="Record relevant context.",
        value_type="text",
        sort_order=2,
    )
    project_id = create_project(
        project,
        taxonomy,
        methodology,
        visualization,
        minimal_stops,
        [],
        db_path,
        assessment_modes=[dimension, retained],
    )

    saved = save_assessment_modes(project_id, [retained], db_path)
    assert [mode["key"] for mode in saved] == ["field_note"]
    assert [mode["key"] for mode in list_assessment_modes(project_id, db_path)] == [
        "field_note"
    ]
