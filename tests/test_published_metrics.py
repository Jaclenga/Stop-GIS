from __future__ import annotations

import pandas as pd

import published_app


def test_clear_map_filters_restores_defaults_without_touching_other_state(monkeypatch):
    session_state = {
        "published_show_unlabeled_stops": False,
        "published_stop_search": "main",
        "published_route_filter": ["10"],
        "published_destination_filter": "hospital",
        "published_selected_stop_id": "1001",
    }
    monkeypatch.setattr(published_app.st, "session_state", session_state)
    stops = pd.DataFrame(
        [{"stop_id": "1001", "stop_name": "Main", "routes": "10"}]
    )

    published_app.clear_map_filters(stops, "published")

    assert session_state == {"published_selected_stop_id": "1001"}
    assert not published_app.map_filters_active(
        {
            "show_unlabeled": True,
            "search_query": "",
            "selected_routes": [],
            "destination_query": "",
            "categorical": {},
            "numeric": {},
        }
    )


def test_load_study_preserves_leading_zero_stop_ids(db_path, monkeypatch):
    config_path = db_path.parent / "shade_study_config.json"
    stops_path = db_path.parent / "shade_study_stops.csv"
    labels_path = db_path.parent / "shade_study_raw_labels.csv"
    config_path.write_text('{"visualization": {"priority_weights": {}}}', encoding="utf-8")
    stops_path.write_text(
        "stop_id,stop_name,stop_lat,stop_lon\n00123,Leading Zero,27.9,-82.4\n123,Numeric,28.0,-82.5\n",
        encoding="utf-8",
    )
    labels_path.write_text(
        "stop_id,labeler_id,shade_category\n00123,a,No Shade\n123,b,Limited Shade\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(published_app, "CONFIG_PATH", config_path)
    monkeypatch.setattr(published_app, "DATA_PATH", stops_path)
    monkeypatch.setattr(published_app, "RAW_LABELS_PATH", labels_path)

    _, stops, labels = published_app.load_study()

    assert stops["stop_id"].tolist() == ["00123", "123"]
    assert labels["stop_id"].tolist() == ["00123", "123"]


def test_published_majority_uses_latest_canonical_label_per_rater():
    labels = pd.DataFrame(
        [
            {
                "stop_id": "1",
                "labeler_id": "a",
                "shade_category": "No Shade",
                "created_at": "2026-08-01T10:00:00Z",
            },
            {
                "stop_id": "1",
                "labeler_id": "a",
                "shade_category": "Limited Natural Shade",
                "shade_coverage": "Limited Shade",
                "created_at": "2026-08-01T11:00:00Z",
            },
            {
                "stop_id": "1",
                "labeler_id": "b",
                "shade_category": "Limited Shade",
                "created_at": "2026-08-01T12:00:00Z",
            },
        ]
    )

    majority = published_app.majority_label_table(labels)
    counts = published_app.category_count_matrix(labels)

    assert majority.loc[0, "majority_label"] == "Limited Shade"
    assert majority.loc[0, "label_count"] == 2
    assert majority.loc[0, "agreement_pct"] == 100.0
    assert not bool(majority.loc[0, "disagreement_flag"])
    assert counts.loc["1", "Limited Shade"] == 2


def test_blank_csv_labeler_ids_remain_separate_anonymous_records():
    labels = pd.DataFrame(
        [
            {"id": "label-a", "stop_id": "1", "labeler_id": float("nan"), "shade_category": "No Shade"},
            {"id": "label-b", "stop_id": "1", "labeler_id": float("nan"), "shade_category": "Limited Shade"},
        ]
    )

    clean = published_app.latest_labels_by_rater(labels)

    assert clean["rater"].tolist() == ["anonymous-record:label-a", "anonymous-record:label-b"]


def test_safe_chart_has_no_scale_binding_and_drops_non_finite_values() -> None:
    data = pd.DataFrame(
        {
            "shade_sources": ["Natural", "Purpose-built", "Unknown"],
            "stops": [12, float("inf"), float("nan")],
        }
    )

    chart = published_app.build_safe_chart(data, "shade_sources", "stops")

    assert chart is not None
    assert "params" not in chart
    assert chart["data"]["values"] == [{"shade_sources": "Natural", "stops": 12.0}]
    assert chart["encoding"]["y"]["stack"] is None
    assert chart["encoding"]["y"]["scale"]["domain"] == [0.0, 12.0]


def test_published_app_separates_legacy_coverage_and_source_labels():
    legacy = pd.DataFrame(
        [
            {"shading": "Significant Natural Shade", "shade_coverage": "", "shade_sources": ""},
            {"shading": "Intentional Built Shade", "shade_coverage": "Significant", "shade_sources": ""},
        ]
    )

    normalized = published_app.normalize_published_stop_dimensions(legacy)

    assert normalized["shading"].tolist() == ["Significant Shade", "Significant Shade"]
    assert normalized["shade_coverage"].tolist() == ["Significant Shade", "Significant Shade"]
    assert normalized["shade_sources"].tolist() == ["Natural", "Purpose-built"]
from published_app import summary_metric_cards


def metric_by_label(metrics: list[dict[str, str]], label: str) -> dict[str, str]:
    return next(metric for metric in metrics if metric["label"] == label)


def test_summary_metric_cards_report_inventory_readiness() -> None:
    stops = pd.DataFrame(
        [
            {"stop_lat": 27.95, "stop_lon": -82.45, "shading": "No Shade", "review_status": "Unlabeled"},
            {"stop_lat": 27.96, "stop_lon": -82.46, "shading": "Limited", "review_status": "Unlabeled"},
            {"stop_lat": 27.97, "stop_lon": -82.47, "shading": "Needs Review", "review_status": "Needs Review"},
            {"stop_lat": None, "stop_lon": -82.48, "shading": "", "review_status": "Unlabeled"},
            {"stop_lat": 27.98, "stop_lon": -82.49, "shading": "Unknown", "review_status": "Unlabeled"},
        ]
    )

    metrics = summary_metric_cards(stops)

    assert metric_by_label(metrics, "Mapped stops")["value"] == "4"
    assert metric_by_label(metrics, "Mapped stops")["delta"] == "80.0% with coordinates"
    assert metric_by_label(metrics, "Classified stops")["value"] == "2"
    assert metric_by_label(metrics, "Classified stops")["delta"] == "40.0% of current view"
    assert metric_by_label(metrics, "Review backlog")["value"] == "3"
    assert metric_by_label(metrics, "Review backlog")["delta"] == "60.0% remaining"
    assert metric_by_label(metrics, "No-shade stops")["value"] == "1"
    assert metric_by_label(metrics, "No-shade stops")["delta"] == "50.0% of classified"


def test_summary_metric_cards_do_not_surface_empty_accepted_status() -> None:
    stops = pd.DataFrame(
        {
            "stop_lat": [27.95] * 2315,
            "stop_lon": [-82.45] * 2315,
            "shading": ["No Shade"] * 12 + ["Limited"] * 22 + ["Needs Review"] * 2281,
            "review_status": ["Unlabeled"] * 2315,
        }
    )

    metrics = summary_metric_cards(stops)
    labels = [metric["label"] for metric in metrics]

    assert "Accepted" not in labels
    assert metric_by_label(metrics, "Classified stops")["value"] == "34"
    assert metric_by_label(metrics, "Review backlog")["delta"] == "98.5% remaining"
    assert metric_by_label(metrics, "No-shade stops")["delta"] == "35.3% of classified"


def test_published_agreement_overview_uses_compact_metrics() -> None:
    labels = pd.DataFrame(
        [
            {
                "stop_id": "1001",
                "labeler_id": "alice",
                "labeler_role": "Contributor",
                "shade_category": "No Shade",
                "source": "crowdsourcing",
            },
            {
                "stop_id": "1001",
                "labeler_id": "bob",
                "labeler_role": "Contributor",
                "shade_category": "No Shade",
                "source": "crowdsourcing",
            },
        ]
    )
    metrics = published_app.agreement_overview_values(labels)
    queue = published_app.published_disagreement_queue(labels)

    assert metrics["stops_labeled"] == 1
    assert metrics["stops_needing_review"] == 0
    assert metrics["mean_agreement"] == 100.0
    assert queue.empty
    markup = published_app.agreement_overview_markup(metrics)
    assert "📍 Labeled" in markup
    assert "Reliability" in markup
    assert "Krippendorff α" in markup


def test_published_disagreement_queue_excludes_resolved_stops() -> None:
    labels = pd.DataFrame(
        [
            {"stop_id": "1001", "labeler_id": "alice", "shade_category": "No Shade"},
            {"stop_id": "1001", "labeler_id": "bob", "shade_category": "Limited Shade"},
        ]
    )
    stops = pd.DataFrame(
        [
            {
                "stop_id": "1001",
                "review_status": "Accepted",
                "review_resolved_at": "2026-08-01T12:00:00Z",
            }
        ]
    )

    metrics = published_app.agreement_overview_values(labels, stops)
    queue = published_app.published_disagreement_queue(labels, stops)

    assert metrics["stops_needing_review"] == 0
    assert queue.empty


def test_published_review_status_lookup_normalizes_mixed_stop_id_types() -> None:
    labels = pd.DataFrame(
        [
            {"stop_id": "1001", "labeler_id": "alice", "shade_category": "No Shade"},
            {"stop_id": "1001", "labeler_id": "bob", "shade_category": "Limited Shade"},
        ]
    )
    stops = pd.DataFrame(
        [
            {"stop_id": 1001, "review_status": "Needs Review"},
            {
                "stop_id": "1001",
                "review_status": "Accepted",
                "review_resolved_at": "2026-08-01T12:00:00Z",
            },
        ]
    )

    queue = published_app.published_disagreement_queue(labels, stops)

    assert queue.empty


def test_agreement_filter_normalizes_visible_stop_id_types() -> None:
    labels = pd.DataFrame(
        [
            {"stop_id": "1", "labeler_id": "alice", "shade_category": "No Shade"},
            {"stop_id": "2", "labeler_id": "bob", "shade_category": "Limited Shade"},
        ]
    )
    visible_stops = pd.DataFrame([{"stop_id": 1.0}])

    filtered = published_app.labels_for_visible_stops(labels, visible_stops)

    assert filtered["stop_id"].tolist() == ["1"]


def test_published_newer_label_reopens_resolved_disagreement() -> None:
    labels = pd.DataFrame(
        [
            {
                "stop_id": "1001",
                "labeler_id": "alice",
                "shade_category": "No Shade",
                "created_at": "2026-08-01T10:00:00Z",
            },
            {
                "stop_id": "1001",
                "labeler_id": "bob",
                "shade_category": "Limited Shade",
                "created_at": "2026-08-01T13:00:00Z",
            },
        ]
    )
    stops = pd.DataFrame(
        [
            {
                "stop_id": "1001",
                "review_status": "Accepted",
                "review_resolved_at": "2026-08-01T12:00:00Z",
            }
        ]
    )

    assert published_app.published_disagreement_queue(labels, stops)["stop_id"].tolist() == [
        "1001"
    ]


def test_fleiss_kappa_rejects_unequal_rater_counts() -> None:
    labels = pd.DataFrame(
        [
            ("U1", "r1", "No Shade"),
            ("U1", "r2", "No Shade"),
            ("U2", "r1", "No Shade"),
            ("U2", "r2", "No Shade"),
            ("U2", "r3", "Limited Shade"),
            ("U2", "r4", "Limited Shade"),
        ],
        columns=["stop_id", "labeler_id", "shade_category"],
    )

    assert published_app.fleiss_kappa(labels) is None


def test_taxonomy_display_hides_sort_order_but_preserves_category_order() -> None:
    taxonomy = [
        {"sort_order": 2, "name": "Limited Shade", "description": "Partial coverage"},
        {"sort_order": 1, "name": "No Shade", "description": "No coverage"},
    ]

    display = published_app.taxonomy_display_table(taxonomy)

    assert display.columns.tolist() == ["name", "description"]
    assert display["name"].tolist() == ["No Shade", "Limited Shade"]


def test_published_config_normalizes_legacy_taxonomy_and_analytics() -> None:
    config = {
        "taxonomy": [
            {"name": "No Shade", "description": "None", "color": "#dc143c", "sort_order": 1},
            {"name": "Limited Natural Shade", "description": "Some", "color": "#d69e2e", "sort_order": 2},
            {"name": "Significant Natural Shade", "description": "Most", "color": "#228b22", "sort_order": 3},
            {"name": "Intentional Built Shade", "description": "Shelter", "color": "#4682b4", "sort_order": 4},
            {"name": "Incidental Built Shade", "description": "Building", "color": "#805aaa", "sort_order": 5},
            {"name": "Needs Review", "description": "Review", "color": "#808080", "sort_order": 6},
        ],
        "visualization": {
            "metric_cards": ["Shade distribution", "Review status", "Shade sources", "Shade coverage"],
            "custom_charts": [
                {"title": "Shade Sources", "x": "shade_sources", "y": "Record count", "aggregation": "Count", "chart_type": "Bar"},
                {"title": "Shade Sources", "x": "shade_sources", "y": "Record count", "aggregation": "Count", "chart_type": "Bar"},
            ],
        },
    }

    normalized = published_app.normalize_published_config(config)

    assert [item["name"] for item in normalized["taxonomy"]] == [
        "No Shade",
        "Limited Shade",
        "Significant Shade",
        "Needs Review",
    ]
    assert normalized["visualization"]["metric_cards"] == ["Shade sources", "Shade coverage"]
    assert [chart["x"] for chart in normalized["visualization"]["custom_charts"]] == [
        "shade_sources",
        "shade_coverage",
    ]


def test_public_schema_tables_separate_coverage_from_sources() -> None:
    legacy = [
        {"name": "Limited Natural Shade", "description": "Some", "color": "#d69e2e", "sort_order": 2},
        {"name": "Intentional Built Shade", "description": "Shelter", "color": "#4682b4", "sort_order": 4},
    ]

    coverage = published_app.coverage_schema_display_table(
        legacy,
        [
            {
                "code": "No Shade",
                "shade_coverage": "Unshaded",
                "operational_definition": "No visible shade.",
            },
            {
                "code": "Limited Shade",
                "shade_coverage": "Partial Shade",
                "operational_definition": "Some visible shade.",
            },
            {
                "code": "Significant Shade",
                "shade_coverage": "Broad Shade",
                "operational_definition": "Most of the area is shaded.",
            },
        ],
    )
    sources = published_app.source_schema_display_table(
        [
            {"shade_source": "Natural", "operational_definition": "Custom natural definition."},
            {"shade_source": "Purpose-built", "operational_definition": "Custom built definition."},
            {"shade_source": "Incidental", "operational_definition": "Custom incidental definition."},
        ]
    )
    terms = published_app.terminology_display_table(
        [{"term": "Waiting Area", "operational_definition": "Project-specific definition."}]
    )
    legend = published_app.taxonomy_legend_markup(legacy)

    assert terms.columns.tolist() == ["Term", "Operational Definition"]
    assert terms["Term"].tolist() == ["Waiting Area"]
    assert terms.iloc[0]["Operational Definition"] == "Project-specific definition."
    assert coverage["Shade Coverage"].tolist() == ["Unshaded", "Partial Shade", "Broad Shade"]
    assert published_app.terminology_display_table().iloc[0]["Operational Definition"].endswith(
        "for waiting are excluded."
    )
    assert coverage.columns.tolist() == ["Shade Coverage", "Operational Definition"]
    assert sources["Shade Source"].tolist() == ["Natural", "Purpose-built", "Incidental"]
    assert sources.iloc[0]["Operational Definition"] == "Custom natural definition."
    assert "Limited Natural Shade" not in legend
    assert "Intentional Built Shade" not in legend
    assert "Limited Shade" in legend


def test_stop_detail_picker_avoids_session_state_default_warning(monkeypatch) -> None:
    selectbox_calls = []

    class FakeStreamlit:
        session_state = {}

        @staticmethod
        def info(*args, **kwargs):
            return None

        @staticmethod
        def selectbox(*args, **kwargs):
            selectbox_calls.append((args, kwargs))
            return 1

        @staticmethod
        def markdown(*args, **kwargs):
            return None

    stops = pd.DataFrame(
        [
            {
                "stop_id": "1001",
                "stop_name": "First Stop",
                "shading": "No Shade",
                "review_status": "Unlabeled",
                "priority_score": 0.0,
            },
            {
                "stop_id": "1002",
                "stop_name": "Second Stop",
                "shading": "Significant",
                "shade_sources": "Purpose-built",
                "review_status": "Accepted",
                "priority_score": 1.0,
            },
        ]
    )
    FakeStreamlit.session_state["preview_selected_stop_id"] = "1002"
    monkeypatch.setattr(published_app, "st", FakeStreamlit)

    published_app.render_stop_detail_workflow(stops, {}, "preview")

    assert FakeStreamlit.session_state["preview_stop_picker"] == 1
    assert "index" not in selectbox_calls[0][1]
