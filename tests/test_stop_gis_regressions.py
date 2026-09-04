from __future__ import annotations

import pandas as pd
import pytest

from stop_gis import public_app

from stop_gis.persistence.store import (
    ProjectConflictError,
    add_adjudication,
    add_assessment,
    create_project,
    list_assessments,
    load_project_bundle,
    save_project_bundle,
)
from stop_gis.public_app import (
    categorical_map_filter_columns,
    categorical_filter_options,
    configure_assessment_display,
    export_file_catalog,
    filter_map_stops,
)
from stop_gis.assessment_modes import (
    AssessmentValidationError,
    assessment_values_from_record,
    calculate_composite_score,
    categorical_summary,
    modes_for_template,
    normalize_mode_definition,
    reliability_for_mode,
)


def _project(db_path, minimal_stops):
    modes = modes_for_template("basic_stop_amenities")
    project_id = create_project(
        {"name": "Regression audit"},
        [],
        {},
        {},
        minimal_stops,
        [],
        db_path,
        assessment_modes=modes,
    )
    return project_id, modes


@pytest.mark.parametrize("apply_current", [False, True])
def test_assessment_write_invalidates_stale_bundle_and_preserves_history(
    db_path, minimal_stops, apply_current
):
    project_id, _ = _project(db_path, minimal_stops)
    stale = load_project_bundle(project_id, db_path)
    add_assessment(
        project_id,
        {"stop_id": "1001", "assessment_values": {"bench": "present"}},
        db_path,
        apply_current=apply_current,
    )

    with pytest.raises(ProjectConflictError):
        save_project_bundle(
            project_id,
            stale["project"],
            stale["taxonomy"],
            stale["methodology"],
            stale["visualization"],
            stale["stops"].iloc[0:0],
            stale["import_log"],
            db_path,
            assessment_modes=stale["assessment_modes"],
            scoring=stale["scoring"],
        )
    assert len(list_assessments(project_id, db_path)) == 1


def test_adjudication_cannot_supersede_another_project_or_stop(db_path, minimal_stops):
    first_project, modes = _project(db_path, minimal_stops)
    second_project = create_project(
        {"name": "Second"}, [], {}, {}, minimal_stops, [], db_path, assessment_modes=modes
    )
    original = add_assessment(
        first_project,
        {"stop_id": "1001", "assessment_values": {"bench": "present"}},
        db_path,
    )
    with pytest.raises(AssessmentValidationError, match="same project and stop"):
        add_adjudication(
            second_project,
            {
                "stop_id": "1001",
                "supersedes_id": original,
                "assessment_values": {"bench": "none"},
            },
            db_path,
            apply_current=False,
        )


def test_missing_values_do_not_become_nan_assessment_categories():
    assert assessment_values_from_record(
        {"shade_coverage": float("nan"), "shade_sources": pd.NA}
    ) == {}
    bench = next(
        mode for mode in modes_for_template("basic_stop_amenities") if mode["key"] == "bench"
    )
    summary = categorical_summary(pd.DataFrame([{"bench": float("nan")}]), bench)
    assert "nan" not in summary["value"].tolist()


def test_multiselect_published_filter_matches_individual_values():
    frame = pd.DataFrame(
        [
            {
                "stop_id": "1",
                "shade_source": "natural; incidental",
                "assessment_values": {"shade_source": ["natural", "incidental"]},
            },
            {
                "stop_id": "2",
                "shade_source": "natural",
                "assessment_values": {"shade_source": ["natural"]},
            },
        ]
    )
    from stop_gis.assessment_modes import materialize_assessment_columns

    frame = materialize_assessment_columns(frame)
    assert categorical_filter_options(frame, "shade_source") == ["incidental", "natural"]
    filtered = filter_map_stops(
        frame, filters={"categorical": {"shade_source": ["natural"]}}
    )
    assert filtered["stop_id"].tolist() == ["1", "2"]


def test_boolean_string_false_scores_zero():
    mode = normalize_mode_definition(
        {"key": "working", "value_type": "boolean", "enabled": True}
    )
    assert calculate_composite_score(
        {"working": "false"}, [mode], {"weights": {"working": 1}}
    ) == 0.0


def test_map_and_export_flags_remove_hidden_mode_and_nested_value():
    hidden = normalize_mode_definition(
        {
            "key": "bench",
            "value_type": "categorical",
            "allowed_values": ["none", "present"],
            "enabled": False,
            "display": {"map": False, "filter": False, "summary": False, "export": False},
        }
    )
    visualization = configure_assessment_display(
        {"display_columns": ["stop_name", "bench"]}, [hidden]
    )
    assert visualization["display_columns"] == ["stop_name"]

    assessments = pd.DataFrame(
        [{"id": "a", "assessment_values": {"bench": "present"}, "created_at": "2026-01-01"}]
    )
    catalog = export_file_catalog(
        pd.DataFrame(
            [
                {
                    "stop_id": "1",
                    "stop_lat": 1.0,
                    "stop_lon": 2.0,
                    "bench": "present",
                    "assessment_values": {"bench": "present"},
                }
            ]
        ),
        pd.DataFrame(),
        {"assessment_modes": [hidden]},
        assessments=assessments,
    )
    stops_csv = next(item for item in catalog if item["name"] == "Stops CSV")["data"].decode()
    raw_csv = next(item for item in catalog if item["name"] == "Raw Assessments CSV")["data"].decode()
    assert "bench" not in stops_csv
    assert "present" not in raw_csv

    hidden_shade = normalize_mode_definition(
        {
            "key": "shade_coverage",
            "value_type": "categorical",
            "allowed_values": ["none", "limited", "significant", "unclear"],
            "enabled": False,
            "display": {"map": False, "filter": False, "summary": False, "export": False},
        }
    )
    shade_frame = pd.DataFrame(
        [
            {
                "stop_id": "1",
                "stop_lat": 1.0,
                "stop_lon": 2.0,
                "shading": "No Shade",
                "shade_coverage": "No Shade",
            },
            {
                "stop_id": "2",
                "stop_lat": 2.0,
                "stop_lon": 3.0,
                "shading": "Limited Shade",
                "shade_coverage": "Limited Shade",
            },
        ]
    )
    shade_visualization = configure_assessment_display(
        {"display_columns": ["stop_name", "shading", "shade_coverage"]},
        [hidden_shade],
    )
    assert shade_visualization["display_columns"] == ["stop_name"]
    assert "shading" not in categorical_map_filter_columns(shade_frame)
    shade_catalog = export_file_catalog(
        shade_frame, pd.DataFrame(), {"assessment_modes": [hidden_shade]}
    )
    shade_header = shade_catalog[0]["data"].decode().splitlines()[0]
    assert "shading" not in shade_header
    assert "shade_coverage" not in shade_header


def test_disabled_shade_is_removed_from_runtime_map_and_stop_details(monkeypatch):
    hidden_shade = normalize_mode_definition(
        {
            "key": "shade_coverage",
            "label": "Shade coverage",
            "value_type": "categorical",
            "allowed_values": ["none", "limited", "significant", "unclear"],
            "enabled": False,
        }
    )
    enabled_bench = normalize_mode_definition(
        {
            "key": "bench_presence",
            "label": "Bench presence",
            "value_type": "categorical",
            "allowed_values": ["present", "absent", "unclear"],
            "enabled": True,
        }
    )
    visualization = configure_assessment_display(
        {
            "color_by": "Shade coverage",
            "display_columns": ["stop_id", "shade_coverage", "bench_presence"],
            "metric_cards": ["Shade coverage", "Review status"],
            "custom_charts": [
                {"x": "shade_coverage", "y": "Record count"},
                {"x": "bench_presence", "y": "Record count"},
            ],
            "priority_weights": {"ridership": 0.5, "low_shade": 0.5},
            "voting": {"enabled": True},
        },
        [hidden_shade, enabled_bench],
    )

    assert visualization["color_by"] == "Review status"
    assert visualization["display_columns"] == ["stop_id", "bench_presence"]
    assert visualization["metric_cards"] == ["Review status"]
    assert "Agreement metrics" not in public_app.selected_dashboard_sections(
        pd.DataFrame({"review_status": ["Unlabeled"], "priority_score": [0]}),
        visualization,
    )
    assert visualization["custom_charts"] == [
        {"x": "bench_presence", "y": "Record count"}
    ]
    assert visualization["priority_weights"]["low_shade"] == 0
    assert visualization["voting"]["enabled"] is False
    assert public_app.should_show_taxonomy_legend(visualization) is False

    class StreamlitStub:
        def __init__(self):
            self.session_state = {}
            self.markdown_calls = []

        def info(self, value):
            self.markdown_calls.append(str(value))

        def selectbox(self, _label, options, **_kwargs):
            return list(options)[0]

        def markdown(self, value, **_kwargs):
            self.markdown_calls.append(str(value))

    stub = StreamlitStub()
    monkeypatch.setattr(public_app, "st", stub)
    stops = pd.DataFrame(
        [
            {
                "stop_id": "1",
                "stop_name": "Main Street",
                "stop_lat": 40.44,
                "stop_lon": -80.0,
                "routes": "1",
                "shading": "Needs Review",
                "shade_coverage": "Needs Review",
                "bench_presence": "unclear",
                "review_status": "Unlabeled",
                "priority_score": 0,
            }
        ]
    )

    public_app.render_stop_detail_workflow(stops, visualization, "bench_test")
    rendered = "\n".join(stub.markdown_calls)
    assert "Shade" not in rendered
    assert "Bench presence" in rendered


def test_bench_only_mode_list_treats_omitted_shade_dimensions_as_disabled():
    bench = normalize_mode_definition(
        {
            "key": "bench",
            "label": "Bench",
            "value_type": "categorical",
            "allowed_values": ["none", "present", "unclear"],
            "enabled": True,
        }
    )

    visualization = configure_assessment_display(
        {
            "color_by": "Shade coverage",
            "display_columns": ["stop_id", "shading", "bench"],
            "metric_cards": ["Shade coverage", "Agreement metrics", "Review status"],
        },
        [bench],
    )

    assert visualization["_shade_enabled"] is False
    assert visualization["color_by"] == "Review status"
    assert visualization["display_columns"] == ["stop_id", "bench"]
    assert visualization["metric_cards"] == ["Review status"]


def test_reliability_uses_krippendorff_unit_weighting_and_independent_rows_only():
    mode = normalize_mode_definition(
        {
            "key": "condition",
            "value_type": "categorical",
            "allowed_values": ["yes", "no"],
            "enabled": True,
        }
    )
    rows = [
        {"id": "a1", "stop_id": "a", "reviewer_id": "r1", "submission_type": "independent", "assessment_values": {"condition": "yes"}},
        {"id": "a2", "stop_id": "a", "reviewer_id": "r2", "submission_type": "independent", "assessment_values": {"condition": "no"}},
    ]
    rows.extend(
        {
            "id": f"b{index}",
            "stop_id": "b",
            "reviewer_id": f"r{index}",
            "submission_type": "independent",
            "assessment_values": {"condition": "yes"},
        }
        for index in range(10)
    )
    rows.append(
        {"id": "adjudicated", "stop_id": "a", "reviewer_id": "admin", "submission_type": "adjudication", "assessment_values": {"condition": "yes"}}
    )
    result = reliability_for_mode(pd.DataFrame(rows), mode)
    assert result["items"] == 2
    assert result["agreement"] == pytest.approx(10 / 12, abs=0.0001)
    assert result["krippendorff_alpha"] == 0.0


def test_anonymous_independent_assessments_remain_distinct_for_reliability():
    mode = normalize_mode_definition(
        {
            "key": "condition",
            "value_type": "categorical",
            "allowed_values": ["yes", "no"],
            "enabled": True,
        }
    )
    assessments = pd.DataFrame(
        [
            {"id": "one", "stop_id": "1", "reviewer_id": "", "assessment_values": {"condition": "yes"}},
            {"id": "two", "stop_id": "1", "reviewer_id": "", "assessment_values": {"condition": "no"}},
        ]
    )
    assert reliability_for_mode(assessments, mode)["items"] == 1
