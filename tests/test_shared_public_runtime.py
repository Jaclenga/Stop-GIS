from __future__ import annotations

from pathlib import Path

import pandas as pd

import apps.published as published_app
from stop_gis.pages import labels_page, preview_page
from stop_gis import public_app
from stop_gis.assessment_modes import modes_for_template


def test_builder_and_compatibility_entrypoints_use_one_public_implementation():
    assert published_app is public_app
    assert preview_page.published_app is public_app
    assert labels_page.published_app is public_app

    for name in [
        "normalize_published_taxonomy",
        "configure_assessment_display",
        "filter_map_stops",
        "mappable_stop_rows",
        "summary_metric_cards",
        "terminology_display_table",
    ]:
        assert getattr(published_app, name) is getattr(public_app, name)


def test_preview_files_are_thin_wrappers_without_copied_business_logic():
    root_entrypoint = Path("apps/published.py").read_text(encoding="utf-8")
    preview_entrypoint = Path("preview_app/app.py").read_text(encoding="utf-8")
    root_voting = Path("apps/public_voting.py").read_text(encoding="utf-8")
    preview_voting = Path("preview_app/public_voting.py").read_text(encoding="utf-8")

    assert preview_entrypoint == root_entrypoint
    assert preview_voting == root_voting
    assert len(root_entrypoint.splitlines()) < 30
    assert len(root_voting.splitlines()) < 25
    assert "stop_gis.public_app" not in root_entrypoint
    assert "from stop_gis import public_app" in root_entrypoint
    assert "from stop_gis import public_voting" in root_voting
    assert not Path("preview_app/stop_gis/assessment_modes.py").exists()


def test_shared_taxonomy_dimensions_filters_empty_states_and_summaries():
    taxonomy = [
        {
            "name": "Significant Shade",
            "description": "Project-specific significant shade definition.",
            "color": "#123456",
            "sort_order": 1,
        }
    ]
    normalized_taxonomy = public_app.normalize_published_taxonomy(taxonomy)
    assert normalized_taxonomy[2]["description"].startswith("Project-specific")

    modes = modes_for_template("passenger_comfort")
    configured = public_app.configure_assessment_display({}, modes)
    enabled_keys = {mode["key"] for mode in modes if mode["enabled"]}
    assert enabled_keys.issubset(configured["display_columns"])
    assert public_app.filter_label("shade_coverage") == "Shade coverage"

    stops = pd.DataFrame(
        [
            {
                "stop_id": "1",
                "stop_name": "Oak Street",
                "routes": "1; 2",
                "stop_lat": 27.9,
                "stop_lon": -82.4,
                "shading": "Significant Shade",
            },
            {
                "stop_id": "2",
                "stop_name": "Pine Street",
                "routes": "3",
                "stop_lat": None,
                "stop_lon": None,
                "shading": "Needs Review",
            },
        ]
    )
    filtered = public_app.filter_map_stops(stops, selected_routes=["2"])
    assert filtered["stop_id"].tolist() == ["1"]
    assert public_app.mappable_stop_rows(stops)["stop_id"].tolist() == ["1"]
    cards = public_app.summary_metric_cards(stops)
    assert {card["label"]: card["value"] for card in cards}["Mapped stops"] == "1"
    assert public_app.summary_metric_cards(pd.DataFrame())[0]["value"] == "0"

    terminology = [
        {"term": "Waiting area", "operational_definition": "Original definition."}
    ]
    terminology[0]["operational_definition"] = "Updated project definition."
    table = public_app.terminology_display_table(terminology)
    assert table.loc[0, "Operational Definition"] == "Updated project definition."
