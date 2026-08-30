from __future__ import annotations

import inspect

import pandas as pd

from stop_gis.persistence.store import (
    add_image,
    add_review_event,
    add_shade_label,
    create_project,
    list_images,
    list_review_history,
    list_shade_labels,
)
from stop_gis.builder.app import (
    DEFAULT_TERMINOLOGY,
    SHADE_COVERAGE_OPTIONS,
    SHADE_COVERAGE_TAXONOMY,
    SHADE_SOURCE_OPTIONS,
    SHADE_SOURCE_TAXONOMY,
    agreement_metric_summary,
    agreement_overview_metrics,
    disagreement_queue_table,
    majority_label_table,
    normalize_coverage_taxonomy,
    review_queue_label,
    review_queue_table,
)
from stop_gis.pages import labels_page


def disagreement_fixture() -> tuple[pd.DataFrame, pd.DataFrame]:
    stops = pd.DataFrame(
        [
            {"stop_id": "4254", "stop_name": "Oak Street", "review_status": "Needs Review"},
            {"stop_id": "7588", "stop_name": "Pine Street", "review_status": "Needs Review"},
            {"stop_id": "7589", "stop_name": "Elm Street", "review_status": "Needs Review"},
        ]
    )
    labels = pd.DataFrame(
        [
            {"stop_id": "4254", "shade_category": "Significant Shade", "labeler_id": "a", "created_at": "2026-07-01T10:00:00Z"},
            {"stop_id": "4254", "shade_category": "Significant Shade", "labeler_id": "b", "created_at": "2026-07-01T10:01:00Z"},
            {"stop_id": "4254", "shade_category": "Limited Shade", "labeler_id": "c", "created_at": "2026-07-01T10:02:00Z"},
            {"stop_id": "7588", "shade_category": "No Shade", "labeler_id": "a", "created_at": "2026-07-01T10:00:00Z"},
            {"stop_id": "7588", "shade_category": "Limited Shade", "labeler_id": "b", "created_at": "2026-07-01T10:01:00Z"},
            {"stop_id": "7589", "shade_category": "No Shade", "labeler_id": "a", "created_at": "2026-07-01T10:00:00Z"},
            {"stop_id": "7589", "shade_category": "No Shade", "labeler_id": "b", "created_at": "2026-07-01T10:01:00Z"},
        ]
    )
    return stops, labels


def test_disagreement_queue_excludes_unanimous_and_sorts_lowest_agreement_first():
    stops, labels = disagreement_fixture()

    queue = disagreement_queue_table(stops, labels)

    assert queue["stop_id"].tolist() == ["7588", "4254"]
    assert queue["agreement_pct"].tolist() == [50.0, 66.7]


def test_current_resolution_hides_stop_but_newer_label_reopens_it():
    stops, labels = disagreement_fixture()
    current_history = pd.DataFrame(
        [{"stop_id": "4254", "to_status": "Accepted", "created_at": "2026-07-01T11:00:00Z"}]
    )
    stale_history = pd.DataFrame(
        [{"stop_id": "4254", "to_status": "Accepted", "created_at": "2026-07-01T09:00:00Z"}]
    )

    assert disagreement_queue_table(stops, labels, current_history)["stop_id"].tolist() == ["7588"]
    assert disagreement_queue_table(stops, labels, stale_history)["stop_id"].tolist() == ["7588", "4254"]


def test_resolved_status_without_a_resolution_timestamp_does_not_hide_new_disagreement():
    stops = pd.DataFrame(
        [{"stop_id": 1, "stop_name": "Legacy", "review_status": "Accepted"}]
    )
    labels = pd.DataFrame(
        [
            {"stop_id": "1", "labeler_id": "a", "shade_category": "No Shade"},
            {"stop_id": 1, "labeler_id": "b", "shade_category": "Limited Shade"},
        ]
    )

    assert disagreement_queue_table(stops, labels)["stop_id"].tolist() == ["1"]


def test_builder_fleiss_kappa_rejects_unequal_rater_counts():
    from stop_gis.builder.labels import fleiss_kappa

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

    assert fleiss_kappa(labels) is None


def test_agreement_overview_counts_only_unresolved_disagreements():
    stops, labels = disagreement_fixture()
    history = pd.DataFrame(
        [{"stop_id": "4254", "to_status": "Accepted", "created_at": "2026-07-01T11:00:00Z"}]
    )

    metrics = agreement_overview_metrics(stops, labels, history)

    assert metrics["stops_labeled"] == 3
    assert metrics["stops_needing_review"] == 1
    assert metrics["mean_agreement"] == 72.23333333333333


def test_stop_images_roundtrip_for_review(db_path, project, taxonomy, methodology, visualization, minimal_stops):
    project_id = create_project(project, taxonomy, methodology, visualization, minimal_stops, [], db_path)
    image_id = add_image(
        project_id,
        {
            "stop_id": "1001",
            "uri": "https://example.org/stop-1001.jpg",
            "image_type": "uploaded_photo",
            "source": "field audit",
            "attribution": "Test reviewer",
        },
        db_path,
    )

    images = list_images(project_id, "1001", db_path)

    assert images["id"].tolist() == [image_id]
    assert images.loc[0, "uri"] == "https://example.org/stop-1001.jpg"
    assert images.loc[0, "attribution"] == "Test reviewer"


def test_raw_labels_conflict_and_majority_are_queryable(db_path, project, taxonomy, methodology, visualization, minimal_stops):
    project_id = create_project(project, taxonomy, methodology, visualization, minimal_stops, [], db_path)
    add_shade_label(
        project_id,
        {
            "stop_id": "1001",
            "labeler_id": "alice",
            "labeler_role": "Contributor",
            "shade_category": "Limited",
            "confidence": 0.7,
            "source": "crowdsourcing",
        },
        db_path,
    )
    add_shade_label(
        project_id,
        {
            "stop_id": "1001",
            "labeler_id": "bob",
            "labeler_role": "Contributor",
            "shade_category": "No Shade",
            "confidence": 0.8,
            "source": "crowdsourcing",
        },
        db_path,
    )

    labels = list_shade_labels(project_id, "1001", db_path)
    majority = majority_label_table(labels)
    queue = review_queue_table(minimal_stops, labels)

    assert len(labels) == 2
    assert bool(majority.loc[0, "disagreement_flag"]) is True
    assert bool(majority.loc[0, "tied_majority"]) is True
    assert queue["stop_id"].tolist() == ["1001"]
    assert queue.loc[queue["stop_id"] == "1001", "label_count"].iloc[0] == 2


def test_majority_uses_only_latest_label_from_each_rater():
    labels = pd.DataFrame(
        [
            {
                "stop_id": "1001",
                "labeler_id": "a",
                "shade_category": "No Shade",
                "created_at": "2026-08-01T10:00:00Z",
            },
            {
                "stop_id": "1001",
                "labeler_id": "a",
                "shade_category": "Limited Shade",
                "created_at": "2026-08-01T11:00:00Z",
            },
            {
                "stop_id": "1001",
                "labeler_id": "b",
                "shade_category": "Limited Shade",
                "created_at": "2026-08-01T12:00:00Z",
            },
        ]
    )

    majority = majority_label_table(labels)

    assert majority.loc[0, "label_count"] == 2
    assert majority.loc[0, "agreement_pct"] == 100.0
    assert not bool(majority.loc[0, "disagreement_flag"])


def test_review_queue_excludes_stops_without_submitted_labels(minimal_stops):
    queue = review_queue_table(minimal_stops, pd.DataFrame())

    assert queue.empty


def test_review_queue_normalizes_arrow_and_unusual_status_values_to_python_objects():
    stop_ids = ["normal", 202, "missing", "empty", "numeric", "list", "dict", "tuple"]
    stops = pd.DataFrame(
        {
            "stop_id": stop_ids,
            "stop_name": [f"Stop {index}" for index in range(len(stop_ids))],
            "review_status": [
                "Accepted",
                None,
                pd.NA,
                "",
                7,
                ["Disputed"],
                {"status": "Archived"},
                ("Crowd Reviewed",),
            ],
        }
    )
    labels = pd.DataFrame(
        [{"stop_id": stop_id, "shade_category": "No Shade"} for stop_id in stop_ids]
    )

    queue = review_queue_table(stops, labels).set_index("stop_id")

    assert queue["review_status"].dtype == object
    assert all(type(value) is str for value in queue["review_status"])
    assert queue["review_status"].to_dict() == {
        "normal": "Accepted",
        "202": "Unlabeled",
        "missing": "Unlabeled",
        "empty": "Unlabeled",
        "numeric": "Needs Review",
        "list": "Needs Review",
        "dict": "Needs Review",
        "tuple": "Needs Review",
    }

    arrow_stops = pd.DataFrame(
        {
            "stop_id": ["arrow-accepted", "arrow-missing", "arrow-empty"],
            "stop_name": ["Accepted", "Missing", "Empty"],
        }
    )
    arrow_stops["review_status"] = pd.Series(
        ["Accepted", None, ""],
        dtype="string[pyarrow]",
    )
    arrow_labels = pd.DataFrame(
        [
            {"stop_id": stop_id, "shade_category": "No Shade"}
            for stop_id in arrow_stops["stop_id"]
        ]
    )

    arrow_queue = review_queue_table(arrow_stops, arrow_labels)

    assert type(arrow_stops["review_status"].array).__name__ == "ArrowStringArray"
    assert arrow_queue["review_status"].dtype == object
    assert type(arrow_queue["review_status"].array).__name__ == "NumpyExtensionArray"
    assert arrow_queue.set_index("stop_id")["review_status"].to_dict() == {
        "arrow-accepted": "Accepted",
        "arrow-missing": "Unlabeled",
        "arrow-empty": "Unlabeled",
    }


def test_empty_label_history_avoids_arrow_backed_column_index(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project_id = create_project(project, taxonomy, methodology, visualization, minimal_stops, [], db_path)

    labels = list_shade_labels(project_id, path=db_path)

    assert labels.empty
    assert labels.columns.dtype == object
    assert labels.columns.tolist() == [
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
        "created_at",
    ]


def test_review_queue_display_uses_reviewer_friendly_columns(minimal_stops):
    queue = minimal_stops.copy()
    queue["majority_label"] = ["Intentional Built Shade", ""]
    queue["label_count"] = [3, 0]
    queue["agreement_pct"] = [66.7, 0.0]
    queue["disagreement_flag"] = [True, False]
    queue["tied_majority"] = [False, False]
    queue["priority_score"] = [12.345, 0]

    display = labels_page.review_queue_display_table(queue)

    assert display.columns.tolist() == [
        "Stop",
        "Status",
        "Current map label",
        "Most common raw label",
        "Labels",
        "Agreement",
        "Needs attention",
        "Priority",
    ]
    assert display.loc[0, "Most common raw label"] == "Needs Review"
    assert display.loc[0, "Agreement"] == "66.7%"
    assert display.loc[0, "Needs attention"] == "Disagreement"


def test_filter_review_queue_records_keeps_only_review_needed_defaults(minimal_stops):
    queue = minimal_stops.copy()
    queue["review_status"] = ["Needs Review", "Accepted"]
    queue["label_count"] = [2, 2]
    queue["agreement_pct"] = [50.0, 100.0]
    queue["disagreement_flag"] = [True, False]
    queue["tied_majority"] = [False, False]

    filtered = labels_page.filter_review_queue_records(
        queue,
        ["Needs Review", "Disputed", "Unlabeled"],
    )

    assert filtered["stop_id"].tolist() == ["1001"]


def test_review_queue_label_reads_like_review_task(minimal_stops):
    row = minimal_stops.iloc[0].copy()
    row["review_status"] = "Unlabeled"
    row["label_count"] = 3
    row["agreement_pct"] = 66.7
    row["disagreement_flag"] = False

    assert review_queue_label(row).endswith("| Unlabeled | 3 labels, 66.7% agreement")


def test_raw_label_comparison_table_hides_storage_names():
    labels = pd.DataFrame(
        [
            {
                "created_at": "2026-07-05T11:57:11-04:00",
                "shade_category": "Intentional Built Shade",
                "shade_coverage": "Significant",
                "shade_sources": "Intentional Built",
                "confidence": 0.75,
                "labeler_role": "Reviewer",
                "labeler_id": "Jack Lenga",
                "source": "manual_review",
                "notes": "",
            }
        ]
    )

    display = labels_page.raw_label_comparison_table(labels)

    assert display.loc[0, "Label"] == "Significant Shade"
    assert display.loc[0, "Coverage"] == "Significant Shade"
    assert display.loc[0, "Sources"] == "Purpose-built"
    assert display.loc[0, "Confidence"] == "75%"
    assert display.loc[0, "Reviewer"] == "Jack Lenga (Reviewer)"
    assert display.loc[0, "Input"] == "Manual Review"


def test_label_code_definition_tables_include_core_schema_terms(taxonomy):
    tables = labels_page.label_code_definition_tables(taxonomy)

    assert set(tables) == {
        "Terminology",
        "Stored fields",
        "Coverage codes",
        "Source codes",
        "Map label codes",
        "Review status codes",
    }
    assert tables["Stored fields"]["Code"].tolist() == [
        "shade_coverage",
        "shade_sources",
        "shading",
        "review_status",
        "confidence",
    ]
    assert tables["Terminology"].to_dict("records") == [
        {
            "Code": "Waiting Area",
            "Definition": (
                "The designated location where passengers would reasonably stand or sit while waiting to board "
                "the bus, including any bus stop pad, sidewalk immediately adjacent to the bus stop sign, or "
                "seating within a bus shelter. Grass, landscaping, roadway, bicycle lanes, and areas not "
                "reasonably intended for waiting are excluded."
            ),
        }
    ]
    assert DEFAULT_TERMINOLOGY[0]["term"] == "Waiting Area"
    custom_taxonomy = [dict(item) for item in taxonomy]
    custom_taxonomy[1]["description"] = "Project-specific limited definition."
    custom_tables = labels_page.label_code_definition_tables(
        custom_taxonomy,
        [{"term": "Boarding Zone", "operational_definition": "Project-specific definition."}],
        [
            {"code": "Natural", "shade_source": "Vegetation", "operational_definition": "Project-specific natural definition."},
            *SHADE_SOURCE_TAXONOMY[1:],
        ],
        [
            {"code": "No Shade", "shade_coverage": "Unshaded", "operational_definition": "No shade."},
            {"code": "Limited Shade", "shade_coverage": "Partial Shade", "operational_definition": "Project-specific limited definition."},
            {"code": "Significant Shade", "shade_coverage": "Mostly Shaded", "operational_definition": "Mostly shade."},
        ],
    )
    assert custom_tables["Terminology"].to_dict("records") == [
        {"Code": "Boarding Zone", "Definition": "Project-specific definition."}
    ]
    assert custom_tables["Coverage codes"].iloc[1]["Definition"] == "Project-specific limited definition."
    assert custom_tables["Coverage codes"].iloc[1]["Code"] == "Partial Shade"
    assert custom_tables["Source codes"].iloc[0]["Definition"] == "Project-specific natural definition."
    assert custom_tables["Source codes"].iloc[0]["Code"] == "Vegetation"
    assert tables["Coverage codes"]["Code"].tolist() == ["No Shade", "Limited Shade", "Significant Shade"]
    assert tables["Source codes"]["Code"].tolist() == ["Natural", "Purpose-built", "Incidental"]
    assert tables["Map label codes"]["Code"].tolist() == [
        "No Shade",
        "Limited Shade",
        "Significant Shade",
        "Needs Review",
    ]
    assert "Accepted" in tables["Review status codes"]["Code"].tolist()


def test_agreement_metric_summary_value_column_is_arrow_safe(db_path, project, taxonomy, methodology, visualization, minimal_stops):
    project_id = create_project(project, taxonomy, methodology, visualization, minimal_stops, [], db_path)
    for labeler_id in ["alice", "bob"]:
        add_shade_label(
            project_id,
            {
                "stop_id": "1001",
                "labeler_id": labeler_id,
                "labeler_role": "Contributor",
                "shade_category": "No Shade",
                "confidence": 0.8,
                "source": "crowdsourcing",
            },
            db_path,
        )

    summary = agreement_metric_summary(list_shade_labels(project_id, "1001", db_path), minimal_stops)

    assert summary["Value"].map(type).eq(str).all()
    assert summary.loc[summary["Metric"] == "Mean majority agreement", "Value"].iloc[0] == "100.0%"


def test_review_lifecycle_records_every_admin_action(db_path, project, taxonomy, methodology, visualization, minimal_stops):
    project_id = create_project(project, taxonomy, methodology, visualization, minimal_stops, [], db_path)
    actions = [
        ("Expert override", "Needs Review", "Expert Reviewed", "No Shade"),
        ("Mark disputed", "Expert Reviewed", "Disputed", "No Shade"),
        ("Resolve dispute", "Disputed", "Accepted", "No Shade"),
        ("Archive", "Accepted", "Archived", "No Shade"),
    ]

    for action, from_status, to_status, to_label in actions:
        add_review_event(
            project_id,
            {
                "stop_id": "1001",
                "actor_id": "expert1",
                "actor_role": "Expert",
                "action": action,
                "from_status": from_status,
                "to_status": to_status,
                "from_label": "Needs Review",
                "to_label": to_label,
                "notes": f"{action} during pytest",
            },
            db_path,
        )

    history = list_review_history(project_id, "1001", db_path)

    assert len(history) == 4
    assert set(history["action"]) == {item[0] for item in actions}
    archive = history.loc[history["action"] == "Archive"].iloc[0]
    assert archive["to_status"] == "Archived"
    assert archive["metadata_to_label"] == "No Shade"
    assert archive["metadata_actor_role"] == "Expert"


def test_review_event_accepts_pandas_scalar_metadata(db_path, project, taxonomy, methodology, visualization, minimal_stops):
    project_id = create_project(project, taxonomy, methodology, visualization, minimal_stops, [], db_path)
    selected_stop = pd.DataFrame(
        {
            "label_count": pd.Series([3], dtype="int64"),
            "agreement_pct": pd.Series([0.75], dtype="float64"),
            "confidence": pd.Series([1.0], dtype="float64"),
        }
    ).iloc[0]

    add_review_event(
        project_id,
        {
            "stop_id": "1001",
            "actor_id": "expert1",
            "actor_role": "Expert",
            "action": "Resolve dispute",
            "from_status": "Disputed",
            "to_status": "Accepted",
            "from_label": "Limited",
            "to_label": "No Shade",
            "from_confidence": selected_stop.get("confidence"),
            "to_confidence": selected_stop.get("confidence"),
            "agreement_pct": selected_stop.get("agreement_pct"),
            "label_count": selected_stop.get("label_count"),
            "notes": "Pandas scalar metadata regression test",
        },
        db_path,
    )

    history = list_review_history(project_id, "1001", db_path)

    assert history.iloc[0]["metadata_label_count"] == 3
    assert history.iloc[0]["metadata_agreement_pct"] == 0.75
    assert history.iloc[0]["metadata_to_confidence"] == 1.0


def test_apply_review_decision_updates_active_stop(monkeypatch):
    class FakeStreamlit:
        session_state = {
            "stops": pd.DataFrame(
                [
                    {
                        "stop_id": "1001",
                        "shading": "Needs Review",
                        "shade_coverage": "Unknown",
                        "shade_sources": "",
                        "confidence": 0.25,
                        "review_status": "Needs Review",
                    }
                ]
            )
        }

    monkeypatch.setattr(labels_page, "st", FakeStreamlit)

    labels_page.apply_review_decision_to_stop(
        "1001",
        "No Shade",
        "No Shade",
        "None",
        0.95,
        "Accepted",
    )

    updated = FakeStreamlit.session_state["stops"].iloc[0]
    assert updated["shading"] == "No Shade"
    assert updated["shade_coverage"] == "No Shade"
    assert updated["shade_sources"] == "None"
    assert updated["confidence"] == 0.95
    assert updated["review_status"] == "Accepted"
    assert pd.notna(pd.to_datetime(updated["review_resolved_at"], utc=True))


def test_review_status_widget_state_is_scoped_to_the_selected_action():
    source = inspect.getsource(labels_page.render_admin_review_decision)

    assert 'decision_key(f"final_status_{label_source_code(action)}")' in source


def test_selected_stop_reference_dataset_keeps_only_mappable_stop(minimal_stops):
    selected = labels_page.selected_stop_reference_dataset(minimal_stops, "1001")

    assert len(selected) == 1
    assert selected.iloc[0]["stop_id"] == "1001"
    assert selected.iloc[0]["stop_lat"] == 27.9506
    assert selected.iloc[0]["stop_lon"] == -82.4572

    no_coordinates = minimal_stops.copy()
    no_coordinates.loc[no_coordinates["stop_id"] == "1001", "stop_lat"] = None

    assert labels_page.selected_stop_reference_dataset(no_coordinates, "1001").empty


def test_stop_reference_map_datasets_include_all_points_and_selected_point(minimal_stops):
    all_stops, selected = labels_page.stop_reference_map_datasets(minimal_stops, "1002")

    assert set(all_stops["stop_id"]) == {"1001", "1002"}
    assert selected["stop_id"].tolist() == ["1002"]

    no_coordinates = minimal_stops.copy()
    no_coordinates.loc[no_coordinates["stop_id"] == "1001", "stop_lon"] = None

    all_stops, selected = labels_page.stop_reference_map_datasets(no_coordinates, "1002")

    assert all_stops["stop_id"].tolist() == ["1002"]
    assert selected["stop_id"].tolist() == ["1002"]


def test_stop_reference_deck_uses_visuals_styling_for_selected_marker(minimal_stops, taxonomy, visualization):
    visualization["marker_size"] = 24
    deck = labels_page.build_stop_reference_deck(minimal_stops, "1001", taxonomy, visualization)

    assert deck is not None
    assert [layer.id for layer in deck.layers] == [
        "stops_layer_circle",
        "semantic_status_symbols",
        "selected_reference_stop_layer",
        "selected_reference_status_symbol",
    ]
    assert len(deck.layers[0].data) == 2
    assert len(deck.layers[-2].data) == 1
    assert deck.layers[-2].data[0]["stop_id"] == "1001"
    assert deck.layers[0].data[0]["marker_size"] == 24
    assert deck.layers[-2].data[0]["marker_size"] > deck.layers[0].data[0]["marker_size"]
    assert deck.layers[-2].data[0]["fill_color"] == deck.layers[0].data[0]["fill_color"]
    assert deck.layers[-2].data[0]["fill_color"] != [255, 75, 75]
    assert deck.layers[0].opacity < deck.layers[-2].opacity
    assert deck.layers[0].opacity == 0.287
    assert deck.layers[-2].opacity == 1.0
    assert deck.layers[-1].data[0]["marker_symbol"] == "X"


def test_review_reference_deck_uses_filtered_queue_points(minimal_stops, taxonomy, visualization):
    queue = minimal_stops.loc[minimal_stops["stop_id"] == "1001"].copy()

    deck = labels_page.build_stop_reference_deck(queue, "1001", taxonomy, visualization)

    assert deck is not None
    assert len(deck.layers[0].data) == 1
    assert deck.layers[0].data[0]["stop_id"] == "1001"


def test_stop_reference_map_uses_preview_style_stable_selection_key(monkeypatch, minimal_stops, taxonomy, visualization):
    pydeck_calls = []

    class FakeStreamlit:
        @staticmethod
        def info(*args, **kwargs):
            return None

        @staticmethod
        def pydeck_chart(*args, **kwargs):
            pydeck_calls.append((args, kwargs))
            return {"selection": {}}

    monkeypatch.setattr(labels_page, "st", FakeStreamlit)

    labels_page.render_stop_reference_map(minimal_stops, "1001", taxonomy, visualization)

    assert pydeck_calls
    assert pydeck_calls[0][1]["key"] == "label_reference_map"
    assert pydeck_calls[0][1]["on_select"] == "rerun"
    assert pydeck_calls[0][1]["selection_mode"] == "single-object"


def test_reference_map_selection_returns_clicked_stop(minimal_stops):
    selection_event = {"selection": {"objects": {"stops_layer_pin": [{"stop_id": "1002"}]}}}

    assert labels_page.stop_id_from_reference_map_selection(selection_event, minimal_stops) == "1002"


def test_sync_label_stop_picker_tracks_selected_stop(monkeypatch, minimal_stops):
    class FakeStreamlit:
        session_state = {"label_selected_stop_id": "1002"}

    monkeypatch.setattr(labels_page, "st", FakeStreamlit)

    labels_page.sync_label_stop_picker(minimal_stops.reset_index(drop=True))

    assert FakeStreamlit.session_state["label_stop_index"] == 1

    FakeStreamlit.session_state["label_selected_stop_id"] = "missing"
    labels_page.sync_label_stop_picker(minimal_stops.reset_index(drop=True))

    assert FakeStreamlit.session_state["label_selected_stop_id"] == "1001"
    assert FakeStreamlit.session_state["label_stop_index"] == 0


def test_infer_shade_sources_from_category():
    assert labels_page.infer_shade_sources_from_category("Significant Natural Shade") == "Natural"
    assert labels_page.infer_shade_sources_from_category("Purpose-built Shade") == "Purpose-built"
    assert labels_page.infer_shade_sources_from_category("Constructed Shade") == "Purpose-built"
    assert labels_page.infer_shade_sources_from_category("Intentional Built Shade") == "Purpose-built"
    assert labels_page.infer_shade_sources_from_category("Incidental Shade") == "Incidental"
    assert labels_page.infer_shade_sources_from_category("Manmade Shade") == "Incidental"
    assert labels_page.infer_shade_sources_from_category("Incidental Built Shade") == "Incidental"
    assert labels_page.infer_shade_sources_from_category("No Shade") == ""
    assert labels_page.infer_shade_sources_from_category("Needs Review") == ""


def test_source_and_coverage_taxonomies_match_schema_terms():
    assert SHADE_SOURCE_OPTIONS == ["Natural", "Purpose-built", "Incidental"]
    assert SHADE_COVERAGE_OPTIONS == ["No Shade", "Limited Shade", "Significant Shade"]
    assert [item["shade_source"] for item in SHADE_SOURCE_TAXONOMY] == SHADE_SOURCE_OPTIONS
    assert [item["shade_coverage"] for item in SHADE_COVERAGE_TAXONOMY] == SHADE_COVERAGE_OPTIONS


def test_coverage_taxonomy_replaces_legacy_vegetation_only_definitions():
    taxonomy = normalize_coverage_taxonomy(
        [
            {
                "name": "Limited Shade",
                "description": "Vegetation shades part of the waiting area, but not most of it.",
            },
            {
                "name": "Significant Shade",
                "description": "Vegetation visibly covers most of the waiting area or seating area.",
            },
        ]
    )
    definitions = {item["name"]: item["description"] for item in taxonomy}

    assert definitions["Limited Shade"] == (
        "Shade visibly covers part of the waiting area, but not most of it."
    )
    assert definitions["Significant Shade"] == (
        "Shade visibly covers most of the waiting area or seating area."
    )


def test_shade_type_options_keep_sources_and_coverage_distinct(taxonomy):
    assert labels_page.shade_type_options(taxonomy) == [
        "No Shade",
        "Limited Shade",
        "Significant Shade",
        "Needs Review",
    ]


def test_shade_category_from_type_returns_coverage_only():
    assert labels_page.shade_category_from_type("Natural", "Limited") == "Limited Shade"
    assert labels_page.shade_category_from_type("Natural", "Significant") == "Significant Shade"
    assert labels_page.shade_category_from_type("Natural", "No Shade") == "No Shade"
    assert labels_page.shade_category_from_type("Purpose-built", "Significant") == "Significant Shade"


def test_normalized_shade_sources_maps_legacy_terms_to_distinct_current_sources():
    assert labels_page.normalized_shade_sources("Natural; Intentional Built; Incidental Built") == [
        "Natural",
        "Purpose-built",
        "Incidental",
    ]


def test_shade_category_from_coverage_and_sources_derives_map_label():
    assert labels_page.shade_category_from_coverage_and_sources("No Shade", ["Natural"]) == "No Shade"
    assert labels_page.shade_category_from_coverage_and_sources("Limited", ["Natural"]) == "Limited Shade"
    assert labels_page.shade_category_from_coverage_and_sources("Significant", ["Natural"]) == "Significant Shade"
    assert labels_page.shade_category_from_coverage_and_sources("Limited", ["Incidental", "Natural"]) == "Limited Shade"
    assert labels_page.shade_category_from_coverage_and_sources("Limited", ["Natural", "Purpose-built"]) == "Limited Shade"


def test_raw_label_form_state_is_scoped_to_each_stop():
    significant_stop = pd.Series(
        {
            "stop_id": "first-stop",
            "shading": "Significant Shade",
            "shade_coverage": "Significant Shade",
            "shade_sources": "Purpose-built",
        }
    )
    limited_stop = pd.Series(
        {
            "stop_id": "second-stop",
            "shading": "Limited Shade",
            "shade_coverage": "Limited Shade",
            "shade_sources": "Natural",
        }
    )

    assert labels_page.raw_label_form_defaults(significant_stop) == (
        "Significant Shade",
        ["Purpose-built"],
    )
    assert labels_page.raw_label_form_defaults(limited_stop) == ("Limited Shade", ["Natural"])
    assert labels_page.raw_label_widget_key("first-stop", "coverage") != labels_page.raw_label_widget_key(
        "second-stop", "coverage"
    )


def test_raw_label_coverage_and_source_controls_rerun_outside_form():
    source = inspect.getsource(labels_page.render_raw_label_collection)

    assert "st.form(" not in source
    assert "st.form_submit_button" not in source
    assert "submitted = st.button(" in source
