from __future__ import annotations

import pandas as pd

from stop_gis.domain.data_quality import DATA_QUALITY_ISSUES, evaluate_data_quality


def quality_fixture() -> tuple[pd.DataFrame, pd.DataFrame]:
    stops = pd.DataFrame(
        [
            {"stop_id": "100", "stop_name": "First", "stop_lat": 27.9, "stop_lon": -82.4},
            {"stop_id": " 100 ", "stop_name": "Duplicate", "stop_lat": 91, "stop_lon": -82.5},
            {"stop_id": "", "stop_name": "", "stop_lat": None, "stop_lon": None},
            {"stop_id": "300", "stop_name": "Bad geometry", "stop_lat": "north", "stop_lon": 0},
        ]
    )
    images = pd.DataFrame(
        [
            {"id": "image-1", "stop_id": "100", "uri": "https://example.org/1.jpg"},
            {"id": "image-2", "stop_id": "", "uri": "https://example.org/2.jpg"},
            {"id": "image-3", "stop_id": "missing", "uri": "https://example.org/3.jpg"},
        ]
    )
    return stops, images


def test_data_quality_report_consolidates_all_required_checks():
    stops, images = quality_fixture()

    report = evaluate_data_quality(stops, images)

    assert report.publication_ready is False
    assert {issue.key: report.count(issue.key) for issue in DATA_QUALITY_ISSUES} == {
        "duplicate_stop_ids": 2,
        "missing_coordinates": 1,
        "missing_required_fields": 1,
        "invalid_geometries": 2,
        "orphaned_images": 2,
    }
    assert report.total_issues == 8
    assert report.summary_table()["Status"].tolist() == [
        "Needs attention",
        "Needs attention",
        "Needs attention",
        "Needs attention",
        "Needs attention",
    ]


def test_data_quality_filter_returns_affected_source_records_with_details():
    stops, images = quality_fixture()
    report = evaluate_data_quality(stops, images)

    invalid_stops = report.affected_records("invalid_geometries")
    orphaned_images = report.affected_records("orphaned_images")
    filtered_findings = report.issue_records("missing_coordinates")

    assert invalid_stops["stop_id"].tolist() == [" 100 ", "300"]
    assert invalid_stops["Source row"].tolist() == [2, 4]
    assert invalid_stops["Quality details"].str.contains("Invalid point coordinates").all()
    assert orphaned_images["id"].tolist() == ["image-2", "image-3"]
    assert filtered_findings["record_id"].tolist() == ["Stop row 3"]
    assert filtered_findings["details"].tolist() == ["Missing latitude and longitude."]


def test_publication_readiness_requires_nonempty_issue_free_dataset():
    clean_stops = pd.DataFrame(
        [{"stop_id": "100", "stop_name": "Main Street", "stop_lat": 27.9, "stop_lon": -82.4}]
    )
    associated_images = pd.DataFrame([{"id": "image-1", "stop_id": "100"}])

    ready = evaluate_data_quality(clean_stops, associated_images)
    empty = evaluate_data_quality(pd.DataFrame(), pd.DataFrame())

    assert ready.publication_ready is True
    assert ready.total_issues == 0
    assert ready.summary_table()["Status"].tolist() == ["Pass"] * 5
    assert empty.publication_ready is False
