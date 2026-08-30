from __future__ import annotations

import pandas as pd

from stop_gis.pages.data_page import (
    dataset_status_metrics,
    dataset_status_table,
    manual_entry_dataframe,
    manual_entry_validation_error,
)
from stop_gis.ui.taxonomy import (
    concept_group,
    hydrate_legacy_shade_metadata,
    humanize_value,
    reset_shade_coverage_definitions,
    reset_shade_source_definitions,
    sync_legacy_shade_metadata,
    taxonomy_editor_key,
    taxonomy_edit_mode_key,
    toggle_taxonomy_edit_mode,
)
from stop_gis.domain.shade_dimensions import normalize_terminology
from stop_gis.ui.tables import dataset_preview_page
from stop_gis.assessment_modes import modes_for_template


def status_fixture() -> tuple[pd.DataFrame, pd.DataFrame]:
    stops = pd.DataFrame(
        [
            {
                "stop_id": "1001",
                "shading": "Significant Shade",
                "shade_coverage": "Significant Shade",
                "review_status": "Accepted",
            },
            {
                "stop_id": "1002",
                "shading": "Needs Review",
                "shade_coverage": "Needs Review",
                "review_status": "Needs Review",
            },
            {
                "stop_id": "1003",
                "shading": "Needs Review",
                "shade_coverage": "Needs Review",
                "review_status": "Unlabeled",
            },
            {
                "stop_id": "1004",
                "shading": "Limited Shade",
                "shade_coverage": "Limited Shade",
                "review_status": "Unlabeled",
            },
        ]
    )
    labels = pd.DataFrame(
        [
            {"stop_id": "1002", "shade_category": "No Shade"},
            {"stop_id": "1002", "shade_category": "Limited Shade"},
            {"stop_id": "1004", "shade_category": "Limited Shade"},
            {"stop_id": "1004", "shade_category": "Limited Shade"},
        ]
    )
    return stops, labels


def test_normalize_terminology_cleans_rows_and_preserves_an_intentionally_empty_list():
    assert normalize_terminology([]) == []
    assert normalize_terminology(
        [
            {"term": " Waiting Area ", "operational_definition": " Definition one. "},
            {"term": "waiting area", "operational_definition": "Duplicate."},
            {"term": float("nan"), "operational_definition": "Ignored."},
        ]
    ) == [{"term": "Waiting Area", "operational_definition": "Definition one."}]


def test_taxonomy_edit_mode_is_project_scoped_and_toggleable(monkeypatch):
    from stop_gis.ui import taxonomy as taxonomy_components

    class FakeStreamlit:
        session_state = {"active_project_id": "project-1"}

    monkeypatch.setattr(taxonomy_components, "st", FakeStreamlit)

    key = taxonomy_edit_mode_key("shade_source")
    assert key == "taxonomy_edit_mode:project-1:shade_source"
    toggle_taxonomy_edit_mode(key)
    assert FakeStreamlit.session_state[key] is True
    toggle_taxonomy_edit_mode(key)
    assert FakeStreamlit.session_state[key] is False


def test_source_definition_reset_preserves_display_labels(monkeypatch):
    from stop_gis.ui import taxonomy as taxonomy_components

    class FakeStreamlit:
        session_state = {"active_project_id": "project-1"}

    monkeypatch.setattr(taxonomy_components, "st", FakeStreamlit)
    methodology = {
        "shade_source_taxonomy": [
            {
                "code": "Natural",
                "shade_source": "Vegetation",
                "operational_definition": "Custom natural definition.",
            },
            {
                "code": "Purpose-built",
                "shade_source": "Shelter",
                "operational_definition": "Custom shelter definition.",
            },
            {
                "code": "Incidental",
                "shade_source": "Nearby structure",
                "operational_definition": "Custom incidental definition.",
            },
        ]
    }

    reset_shade_source_definitions(methodology)

    rows = {item["code"]: item for item in methodology["shade_source_taxonomy"]}
    assert rows["Natural"]["shade_source"] == "Vegetation"
    assert rows["Natural"]["operational_definition"] == (
        "Trees, palms, hedges, or other vegetation visibly shade the waiting area."
    )
    assert (
        taxonomy_editor_key("shade_source")
        == "shade_source_taxonomy_editor:project-1:1"
    )


def test_coverage_definition_reset_preserves_display_labels(monkeypatch, taxonomy):
    from stop_gis.ui import taxonomy as taxonomy_components

    class FakeStreamlit:
        session_state = {"active_project_id": "project-1"}

    monkeypatch.setattr(taxonomy_components, "st", FakeStreamlit)
    methodology = {
        "shade_coverage_taxonomy": [
            {
                "code": "No Shade",
                "shade_coverage": "Unshaded",
                "operational_definition": "Custom no-shade definition.",
            },
            {
                "code": "Limited Shade",
                "shade_coverage": "Partial Shade",
                "operational_definition": "Custom limited definition.",
            },
            {
                "code": "Significant Shade",
                "shade_coverage": "Broad Shade",
                "operational_definition": "Custom significant definition.",
            },
        ]
    }

    reset_shade_coverage_definitions(methodology, taxonomy)

    rows = {item["code"]: item for item in methodology["shade_coverage_taxonomy"]}
    definitions = {item["name"]: item["description"] for item in taxonomy}
    assert rows["Limited Shade"]["shade_coverage"] == "Partial Shade"
    assert rows["Limited Shade"]["operational_definition"] == (
        "Shade visibly covers part of the waiting area, but not most of it."
    )
    assert (
        definitions["Limited Shade"] == rows["Limited Shade"]["operational_definition"]
    )
    assert (
        taxonomy_editor_key("shade_coverage")
        == "shade_coverage_taxonomy_editor:project-1:1"
    )


def test_unified_taxonomy_hydrates_saved_shade_labels_and_definitions(taxonomy):
    methodology = {
        "shade_coverage_taxonomy": [
            {"code": "No Shade", "shade_coverage": "Unshaded", "operational_definition": "None."},
            {"code": "Limited Shade", "shade_coverage": "Partial shade", "operational_definition": "Some."},
            {"code": "Significant Shade", "shade_coverage": "Broad shade", "operational_definition": "Most."},
        ],
        "shade_source_taxonomy": [
            {"code": "Natural", "shade_source": "Vegetation", "operational_definition": "Plants."},
            {"code": "Purpose-built", "shade_source": "Shelter", "operational_definition": "Designed."},
            {"code": "Incidental", "shade_source": "Nearby structure", "operational_definition": "Incidental."},
        ],
    }

    modes = hydrate_legacy_shade_metadata(
        modes_for_template("passenger_comfort"), methodology, taxonomy
    )
    by_key = {mode["key"]: mode for mode in modes}

    assert by_key["shade_coverage"]["value_labels"]["limited"] == "Partial shade"
    assert by_key["shade_coverage"]["value_definitions"]["limited"] == "Some."
    assert by_key["shade_coverage"]["value_labels"]["unclear"] == "Unknown"
    assert by_key["shade_source"]["value_labels"]["natural"] == "Vegetation"
    assert humanize_value(by_key["shade_source"], "purpose_built") == "Shelter"
    assert concept_group(by_key["shade_source"]) == "Shade"
    assert concept_group(by_key["bench"]) == "Comfort"


def test_unified_taxonomy_syncs_inline_edits_to_legacy_saved_fields(taxonomy):
    methodology = {}
    modes = hydrate_legacy_shade_metadata(
        modes_for_template("passenger_comfort"), methodology, taxonomy
    )
    by_key = {mode["key"]: mode for mode in modes}
    by_key["shade_coverage"]["value_labels"]["limited"] = "Partial shade"
    by_key["shade_coverage"]["value_definitions"]["limited"] = "Custom partial definition."
    by_key["shade_source"]["value_labels"]["natural"] = "Vegetation"
    by_key["shade_source"]["value_definitions"]["natural"] = "Custom plant definition."

    sync_legacy_shade_metadata(modes, methodology, taxonomy)

    coverage = {item["code"]: item for item in methodology["shade_coverage_taxonomy"]}
    sources = {item["code"]: item for item in methodology["shade_source_taxonomy"]}
    canonical = {item["name"]: item for item in taxonomy}
    assert coverage["Limited Shade"]["shade_coverage"] == "Partial shade"
    assert coverage["Limited Shade"]["operational_definition"] == "Custom partial definition."
    assert canonical["Limited Shade"]["description"] == "Custom partial definition."
    assert sources["Natural"]["shade_source"] == "Vegetation"
    assert sources["Natural"]["operational_definition"] == "Custom plant definition."


def test_manual_entry_dataframe_uses_plain_object_columns():
    template = manual_entry_dataframe(
        [{"stop_id": "1001", "stop_name": "Main & First"}]
    )

    assert len(template) == 1
    assert template.columns.is_unique
    assert template.dtypes.eq(object).all()
    assert template.iloc[0]["stop_id"] == "1001"
    assert template.iloc[0]["stop_name"] == "Main & First"
    assert template.iloc[0].drop(["stop_id", "stop_name"]).eq("").all()


def test_dataset_status_combines_final_labels_raw_labels_and_review_state():
    stops, labels = status_fixture()

    status = dataset_status_table(stops, labels).set_index("stop_id")
    metrics = dataset_status_metrics(status)

    assert status.loc["1001", "dataset_status"] == "Reviewed"
    assert status.loc["1001", "label_count"] == 0
    assert status.loc["1002", "dataset_status"] == "Needs Review"
    assert status.loc["1002", "label_count"] == 2
    assert status.loc["1002", "agreement_pct"] == 50.0
    assert status.loc["1003", "dataset_status"] == "Unlabeled"
    assert status.loc["1004", "dataset_status"] == "Needs Review"
    assert metrics == {
        "total_stops": 4,
        "labeled_stops": 3,
        "reviewed_stops": 1,
        "stops_needing_review": 2,
        "unlabeled_stops": 1,
        "label_coverage": 0.75,
        "review_completion": 1 / 3,
    }


def test_dataset_status_handles_empty_dataset():
    status = dataset_status_table(pd.DataFrame(), pd.DataFrame())

    assert status.empty
    assert dataset_status_metrics(status)["total_stops"] == 0


def test_dataset_status_reopens_review_when_label_is_newer_than_resolution():
    stops = pd.DataFrame(
        [{"stop_id": "2001", "shading": "No Shade", "review_status": "Accepted"}]
    )
    labels = pd.DataFrame(
        [
            {
                "stop_id": "2001",
                "shade_category": "No Shade",
                "created_at": "2026-07-01T10:00:00Z",
            },
            {
                "stop_id": "2001",
                "shade_category": "Limited Shade",
                "created_at": "2026-07-01T12:00:00Z",
            },
        ]
    )
    stale_resolution = pd.DataFrame(
        [
            {
                "stop_id": "2001",
                "to_status": "Accepted",
                "created_at": "2026-07-01T11:00:00Z",
            }
        ]
    )
    current_resolution = pd.DataFrame(
        [
            {
                "stop_id": "2001",
                "to_status": "Accepted",
                "created_at": "2026-07-01T13:00:00Z",
            }
        ]
    )

    reopened = dataset_status_table(stops, labels, stale_resolution)
    resolved = dataset_status_table(stops, labels, current_resolution)

    assert reopened.iloc[0]["dataset_status"] == "Needs Review"
    assert resolved.iloc[0]["dataset_status"] == "Reviewed"


def test_dataset_preview_returns_only_requested_page():
    stops = pd.DataFrame({"stop_id": [str(index) for index in range(2278)]})

    visible, page, page_count = dataset_preview_page(stops, page=3, page_size=50)
    final_page, final_page_number, _ = dataset_preview_page(
        stops, page=999, page_size=100
    )

    assert len(visible) == 50
    assert visible["stop_id"].tolist() == [str(index) for index in range(100, 150)]
    assert (page, page_count) == (3, 46)
    assert final_page_number == 23
    assert len(final_page) == 78
    assert final_page.iloc[0]["stop_id"] == "2200"


def test_manual_entry_validation_rejects_rows_that_import_would_drop():
    assert (
        manual_entry_validation_error({}) == "Enter a stop ID before adding this entry."
    )
    assert (
        manual_entry_validation_error(
            {"stop_id": "1001", "stop_lat": "north", "stop_lon": "-82.4"}
        )
        == "Enter numeric latitude and longitude values before adding this entry."
    )
    assert (
        manual_entry_validation_error(
            {"stop_id": "1001", "stop_lat": "91", "stop_lon": "-82.4"}
        )
        == "Latitude must be between -90 and 90."
    )
    assert (
        manual_entry_validation_error(
            {"stop_id": "1001", "stop_lat": "nan", "stop_lon": "-82.4"}
        )
        == "Enter finite latitude and longitude values before adding this entry."
    )
    assert (
        manual_entry_validation_error(
            {"stop_id": "1001", "stop_lat": "27.9", "stop_lon": "-181"}
        )
        == "Longitude must be between -180 and 180."
    )
    assert (
        manual_entry_validation_error(
            {"stop_id": "1001", "stop_lat": "27.9", "stop_lon": "-82.4"}
        )
        == ""
    )
