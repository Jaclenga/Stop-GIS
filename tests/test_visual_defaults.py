from __future__ import annotations

import base64
import copy
import io
import json

import pandas as pd
from PIL import Image

import published_app
from stop_gis.pages import visuals_page
from stop_gis.builder.visuals import (
    DEFAULT_VISUALIZATION,
    LEGACY_DEFAULT_METRIC_CARDS,
    RECORD_COUNT_FIELD,
    build_deck_chart,
    build_custom_chart_data,
    get_custom_charts,
    migrate_legacy_analytics_config,
    selected_dashboard_sections,
)


def test_session_backed_color_picker_uses_only_session_state_for_default(monkeypatch):
    calls = []

    class FakeStreamlit:
        session_state = {}

        @staticmethod
        def color_picker(*args, **kwargs):
            calls.append((args, kwargs))
            return FakeStreamlit.session_state[kwargs["key"]]

    monkeypatch.setattr(visuals_page, "st", FakeStreamlit)

    selected = visuals_page.session_backed_color_picker(
        "No Shade", "#dc143c", "shade_color_0"
    )
    FakeStreamlit.session_state["shade_color_0"] = "#123456"
    selected_again = visuals_page.session_backed_color_picker(
        "No Shade", "#dc143c", "shade_color_0"
    )

    assert selected == "#dc143c"
    assert selected_again == "#123456"
    assert calls == [
        (("No Shade",), {"key": "shade_color_0"}),
        (("No Shade",), {"key": "shade_color_0"}),
    ]


def test_visual_map_render_key_changes_with_marker_controls():
    taxonomy = [{"name": "No Shade", "color": "#dc143c", "sort_order": 1}]
    visualization = copy.deepcopy(DEFAULT_VISUALIZATION)
    initial_key = visuals_page.visual_map_render_key(visualization, taxonomy)

    visualization["marker_shape"] = "Square"
    square_key = visuals_page.visual_map_render_key(visualization, taxonomy)
    visualization["marker_size"] = 22
    resized_key = visuals_page.visual_map_render_key(visualization, taxonomy)

    assert initial_key.startswith("visual_map_")
    assert len({initial_key, square_key, resized_key}) == 3


def test_public_voting_is_off_by_default_but_fully_configured():
    voting = DEFAULT_VISUALIZATION["voting"]

    assert voting["enabled"] is False
    assert voting["options"] == ["No Shade", "Limited Shade", "Significant Shade"]
    assert voting["minimum_votes_for_result"] == 10
    assert voting["minimum_consensus_percent"] == 67
    assert voting["minimum_consensus_margin"] == 2
    assert voting["allow_vote_changes"] is False
    assert voting["enforce_network_vote_limit"] is True


def test_default_custom_charts_count_sources_and_coverage():
    stops = pd.DataFrame(
        [
            {"shade_sources": "Natural", "shade_coverage": "Limited"},
            {"shade_sources": "Purpose-built", "shade_coverage": "Significant"},
        ]
    )
    visualization = {"custom_charts": []}

    charts = get_custom_charts(stops, visualization)

    assert charts == [
        {
            "title": "Shade Sources",
            "x": "shade_sources",
            "y": RECORD_COUNT_FIELD,
            "aggregation": "Count",
            "chart_type": "Bar",
        },
        {
            "title": "Shade Coverage",
            "x": "shade_coverage",
            "y": RECORD_COUNT_FIELD,
            "aggregation": "Count",
            "chart_type": "Bar",
        },
    ]
    assert DEFAULT_VISUALIZATION["custom_charts"][:2] == charts


def test_default_dashboard_charts_are_sources_and_coverage():
    stops = pd.DataFrame(
        [
            {
                "shade_sources": "Natural",
                "shade_coverage": "Limited",
                "shading": "Limited",
                "review_status": "Unlabeled",
            }
        ]
    )

    assert DEFAULT_VISUALIZATION["metric_cards"] == ["Shade sources", "Shade coverage"]
    assert selected_dashboard_sections(stops, DEFAULT_VISUALIZATION) == [
        "Shade sources",
        "Shade coverage",
    ]
    assert published_app.selected_dashboard_sections(stops, DEFAULT_VISUALIZATION) == [
        "Shade sources",
        "Shade coverage",
    ]


def test_legacy_default_dashboard_selection_migrates_to_sources_and_coverage():
    stops = pd.DataFrame(
        [
            {
                "shade_sources": "Natural",
                "shade_coverage": "Limited",
                "shading": "Limited",
                "review_status": "Unlabeled",
            }
        ]
    )
    visualization = {"metric_cards": LEGACY_DEFAULT_METRIC_CARDS}

    assert selected_dashboard_sections(stops, visualization) == [
        "Shade sources",
        "Shade coverage",
    ]
    assert published_app.selected_dashboard_sections(stops, visualization) == [
        "Shade sources",
        "Shade coverage",
    ]


def test_legacy_mixed_dashboard_and_duplicate_source_charts_migrate_once():
    legacy = {
        "metric_cards": [
            "Shade distribution",
            "Review status",
            "Priority stops",
            "Agreement metrics",
            "Stops without shade",
            "Stops requiring review",
            "Shade sources",
            "Shade coverage",
        ],
        "custom_charts": [
            {
                "title": "Shade Sources",
                "x": "shade_sources",
                "y": RECORD_COUNT_FIELD,
                "aggregation": "Count",
                "chart_type": "Bar",
            },
            {
                "title": "Shade Sources",
                "x": "shade_sources",
                "y": RECORD_COUNT_FIELD,
                "aggregation": "Count",
                "chart_type": "Bar",
            },
        ],
    }

    migrated = migrate_legacy_analytics_config(legacy)

    assert migrated["analytics_schema_version"] == 2
    assert migrated["metric_cards"] == ["Shade sources", "Shade coverage"]
    assert [chart["x"] for chart in migrated["custom_charts"]] == [
        "shade_sources",
        "shade_coverage",
    ]
    assert [chart["title"] for chart in migrated["custom_charts"]] == [
        "Shade Sources",
        "Shade Coverage",
    ]
    assert migrate_legacy_analytics_config(migrated) == migrated


def test_source_count_chart_splits_semicolon_values():
    stops = pd.DataFrame(
        [
            {"shade_sources": "Natural; Incidental", "shade_coverage": "Limited"},
            {
                "shade_sources": "Natural; Intentional Built",
                "shade_coverage": "Significant Shade",
            },
            {"shade_sources": "Incidental Built", "shade_coverage": "No Shade"},
            {"shade_sources": "", "shade_coverage": "No Shade"},
        ]
    )
    chart = {
        "title": "Shade Sources",
        "x": "shade_sources",
        "y": RECORD_COUNT_FIELD,
        "aggregation": "Count",
        "chart_type": "Bar",
    }

    data, x_column, y_column = build_custom_chart_data(stops, chart)
    counts = dict(zip(data[x_column], data[y_column], strict=True))

    assert x_column == "shade_sources"
    assert y_column == "records"
    assert counts == {"Natural": 2, "Incidental": 2, "Purpose-built": 1}


def test_coverage_count_chart_uses_schema_codes():
    stops = pd.DataFrame(
        [
            {"shade_coverage": "Limited"},
            {"shade_coverage": "Significant Shade"},
            {"shade_coverage": "Unknown"},
            {"shade_coverage": ""},
        ]
    )
    chart = {
        "title": "Shade Coverage",
        "x": "shade_coverage",
        "y": RECORD_COUNT_FIELD,
        "aggregation": "Count",
        "chart_type": "Bar",
    }

    data, x_column, y_column = build_custom_chart_data(stops, chart)
    counts = dict(zip(data[x_column], data[y_column], strict=True))

    assert y_column == "records"
    assert counts == {"Limited Shade": 1, "Significant Shade": 1}


def test_defaultish_custom_chart_titles_follow_schema_columns():
    stops = pd.DataFrame([{"shade_sources": "Natural", "shade_coverage": "Limited"}])
    visualization = {
        "custom_charts": [
            {
                "title": "Custom chart",
                "x": "shade_sources",
                "y": RECORD_COUNT_FIELD,
                "aggregation": "Count",
                "chart_type": "Bar",
            }
        ]
    }

    charts = get_custom_charts(stops, visualization)

    assert charts[0]["title"] == "Shade Sources"
    assert published_app.custom_chart_title(charts[0], 0) == "Shade Sources"


def test_published_source_count_chart_splits_semicolon_values():
    stops = pd.DataFrame(
        [
            {"shade_sources": "Natural; Intentional Built"},
            {"shade_sources": "Purpose-built"},
            {"shade_sources": ""},
        ]
    )
    chart = {
        "title": "Shade Sources",
        "x": "shade_sources",
        "y": published_app.RECORD_COUNT_FIELD,
        "aggregation": "Count",
        "chart_type": "Bar",
    }

    data, x_column, y_column = published_app.chart_data(stops, chart)
    counts = dict(zip(data[x_column], data[y_column], strict=True))

    assert y_column == "stops"
    assert counts == {"Purpose-built": 2, "Natural": 1}


def test_published_map_matches_visuals_map_renderer():
    stops = pd.DataFrame(
        [
            {
                "stop_id": "1001",
                "stop_name": "Main St",
                "stop_lat": 27.9506,
                "stop_lon": -82.4572,
                "shading": "No Shade",
                "review_status": "Accepted",
                "priority_score": 75,
                "context_label": "High",
            },
            {
                "stop_id": "1002",
                "stop_name": "Central Ave",
                "stop_lat": 27.9510,
                "stop_lon": -82.4590,
                "shading": "Limited",
                "review_status": "Unlabeled",
                "priority_score": 25,
                "context_label": "Moderate",
            },
        ]
    )
    taxonomy = [
        {"name": "No Shade", "color": "#dc143c", "sort_order": 1},
        {"name": "Limited", "color": "#d69e2e", "sort_order": 2},
    ]
    visualization = copy.deepcopy(DEFAULT_VISUALIZATION)
    visualization.update(
        {
            "color_by": "Column: context_label",
            "marker_shape": "Diamond",
            "marker_size": 13,
            "marker_opacity": 0.65,
            "marker_stroke_color": "#222222",
            "marker_stroke_width": 2,
            "map_style": "Dark",
        }
    )

    visuals_deck = json.loads(
        build_deck_chart(stops, taxonomy, copy.deepcopy(visualization)).to_json()
    )
    published_deck = json.loads(
        published_app.build_deck_chart(
            stops, taxonomy, copy.deepcopy(visualization)
        ).to_json()
    )

    assert published_deck == visuals_deck
    assert visuals_deck["useDevicePixels"] == 2


def test_marker_slider_sizes_serialize_as_literal_pixels_and_scale_linearly():
    stops = pd.DataFrame(
        [
            {
                "stop_id": "1001",
                "stop_name": "Main St",
                "stop_lat": 27.9506,
                "stop_lon": -82.4572,
                "shading": "No Shade",
                "review_status": "Unlabeled",
                "priority_score": 0,
            }
        ]
    )
    slider_sizes = [4, 24, 48]

    for chart_builder in (build_deck_chart, published_app.build_deck_chart):
        rendered_circle_sizes = []
        for marker_size in slider_sizes:
            visualization = copy.deepcopy(DEFAULT_VISUALIZATION)
            visualization.update({"marker_shape": "Circle", "marker_size": marker_size})
            layer = json.loads(chart_builder(stops, [], visualization).to_json())[
                "layers"
            ][-1]

            assert layer["radiusUnits"] == "pixels"
            rendered_circle_sizes.append(layer["data"][0]["marker_size"])

        assert rendered_circle_sizes == slider_sizes

        visualization = copy.deepcopy(DEFAULT_VISUALIZATION)
        visualization.update({"marker_shape": "Diamond", "marker_size": 24})
        icon_layer = json.loads(chart_builder(stops, [], visualization).to_json())[
            "layers"
        ][-1]
        assert icon_layer["sizeUnits"] == "pixels"
        assert icon_layer["data"][0]["marker_size"] == 24


def test_non_circle_markers_use_a_browser_loadable_raster_icon_atlas():
    stops = pd.DataFrame(
        [
            {
                "stop_id": "1001",
                "stop_name": "Main St",
                "stop_lat": 27.9506,
                "stop_lon": -82.4572,
                "shading": "No Shade",
                "review_status": "Unlabeled",
                "priority_score": 0,
            }
        ]
    )
    taxonomy = [{"name": "No Shade", "color": "#dc143c", "sort_order": 1}]

    for shape in ["Pin", "Square", "Diamond", "Triangle"]:
        visualization = copy.deepcopy(DEFAULT_VISUALIZATION)
        visualization["marker_shape"] = shape
        for chart_builder in (build_deck_chart, published_app.build_deck_chart):
            layer = json.loads(chart_builder(stops, taxonomy, visualization).to_json())[
                "layers"
            ][-1]
            icon_url = layer["iconAtlas"]

            assert icon_url.startswith("data:image/png;base64,")
            png = base64.b64decode(icon_url.partition(",")[2])
            assert png.startswith(b"\x89PNG\r\n\x1a\n")
            with Image.open(io.BytesIO(png)) as image:
                assert image.size == (128, 128)
                assert image.mode == "RGBA"
            icon_name = layer["data"][0]["icon_name"]
            assert layer["getIcon"] == "@@=icon_name"
            assert layer["iconMapping"][icon_name]["anchorY"] == (
                120 if shape == "Pin" else 64
            )


def test_dense_overviews_honor_selected_marker_shape_and_size():
    stops = pd.DataFrame(
        [
            {
                "stop_id": str(index),
                "stop_name": f"Stop {index}",
                "stop_lat": 27.8 + index * 0.0001,
                "stop_lon": -82.6 + index * 0.0001,
                "shading": "No Shade",
                "review_status": "Unlabeled",
                "priority_score": 0,
            }
            for index in range(500)
        ]
    )
    taxonomy = [{"name": "No Shade", "color": "#dc143c", "sort_order": 1}]
    visualization = copy.deepcopy(DEFAULT_VISUALIZATION)
    visualization.update({"marker_shape": "Square", "marker_size": 24})

    for chart_builder in (build_deck_chart, published_app.build_deck_chart):
        layer = json.loads(chart_builder(stops, taxonomy, visualization).to_json())[
            "layers"
        ][-1]

        assert layer["@@type"] == "IconLayer"
        assert layer["id"] == "stops_layer_square"
        assert layer["data"][0]["marker_size"] == 24
        assert layer["pickable"] is True


def test_each_marker_shape_gets_a_distinct_deck_layer_id():
    stops = pd.DataFrame(
        [
            {
                "stop_id": "1001",
                "stop_name": "Main St",
                "stop_lat": 27.9506,
                "stop_lon": -82.4572,
                "shading": "No Shade",
                "review_status": "Unlabeled",
                "priority_score": 0,
            }
        ]
    )
    taxonomy = [{"name": "No Shade", "color": "#dc143c", "sort_order": 1}]

    layer_ids = []
    for shape in ["Circle", "Pin", "Square", "Diamond", "Triangle"]:
        visualization = copy.deepcopy(DEFAULT_VISUALIZATION)
        visualization["marker_shape"] = shape
        layer_ids.append(build_deck_chart(stops, taxonomy, visualization).layers[-1].id)

    assert layer_ids == [
        "stops_layer_circle",
        "stops_layer_pin",
        "stops_layer_square",
        "stops_layer_diamond",
        "stops_layer_triangle",
    ]


def test_default_map_marker_size_is_seven_for_builder_and_published_maps():
    stops = pd.DataFrame(
        [
            {
                "stop_id": "1001",
                "stop_name": "Main St",
                "stop_lat": 27.9506,
                "stop_lon": -82.4572,
                "shading": "No Shade",
                "review_status": "Unlabeled",
                "priority_score": 0,
            }
        ]
    )
    visualization = copy.deepcopy(DEFAULT_VISUALIZATION)
    visualization.update({"marker_shape": "Diamond"})
    visualization.pop("marker_size")

    assert DEFAULT_VISUALIZATION["marker_size"] == 7
    for chart_builder in (build_deck_chart, published_app.build_deck_chart):
        layer = json.loads(chart_builder(stops, [], visualization).to_json())["layers"][
            -1
        ]
        assert layer["data"][0]["marker_size"] == 7
