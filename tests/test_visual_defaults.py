from __future__ import annotations

import base64
import copy
import io
import json

import pandas as pd
from PIL import Image

from stop_gis import public_app as published_app
from stop_gis.builder.visuals import (
    BUILTIN_CATEGORY_SYMBOLS,
    DEFAULT_VISUALIZATION,
    LEGACY_DEFAULT_METRIC_CARDS,
    RECORD_COUNT_FIELD,
    SHADE_PALETTES,
    apply_palette_to_taxonomy,
    build_deck_chart,
    build_custom_chart_data,
    build_tooltip_text,
    ensure_field_color_map,
    get_custom_charts,
    migrate_legacy_analytics_config,
    selected_dashboard_sections,
)
from stop_gis.domain.shade_dimensions import DEFAULT_COVERAGE_TAXONOMY


def test_palette_catalog_exposes_exactly_three_unique_choices():
    assert list(SHADE_PALETTES) == [
        "Default / Civic",
        "Colorblind friendly",
        "High contrast",
    ]
    assert len(SHADE_PALETTES) == len(set(SHADE_PALETTES)) == 3
    assert SHADE_PALETTES == {
        "Default / Civic": [
            "#ef4444",
            "#f59e0b",
            "#22c55e",
            "#3b82f6",
            "#a855f7",
            "#64748b",
        ],
        "Colorblind friendly": [
            "#d55e00",
            "#e69f00",
            "#009e73",
            "#949494",
            "#0072b2",
            "#cc79a7",
            "#999999",
        ],
        "High contrast": [
            "#c92a2a",
            "#e67700",
            "#2b8a3e",
            "#495057",
            "#1864ab",
            "#6741d9",
        ],
    }


def test_arbitrary_categories_default_to_colorblind_palette_and_extend_cleanly():
    values = [f"Category {index}" for index in range(8)]
    stops = pd.DataFrame({"custom_taxonomy": values})
    visualization = copy.deepcopy(DEFAULT_VISUALIZATION)

    color_map = ensure_field_color_map(visualization, stops, "custom_taxonomy")

    assert visualization["field_palettes"]["custom_taxonomy"] == "Colorblind friendly"
    assert [color_map[value] for value in values[:7]] == SHADE_PALETTES[
        "Colorblind friendly"
    ]
    assert len(set(color_map.values())) == len(values)


def test_builtin_taxonomy_defaults_to_semantic_civic_colors():
    assert [item["name"] for item in DEFAULT_COVERAGE_TAXONOMY] == [
        "No Shade",
        "Limited Shade",
        "Significant Shade",
        "Needs Review",
    ]
    assert [item["color"] for item in DEFAULT_COVERAGE_TAXONOMY] == SHADE_PALETTES[
        "Default / Civic"
    ][:4]
    assert DEFAULT_VISUALIZATION["shade_palette"] == "Default / Civic"


def test_palette_switching_changes_only_taxonomy_colors():
    taxonomy = copy.deepcopy(DEFAULT_COVERAGE_TAXONOMY)
    non_color_values = [
        {key: value for key, value in item.items() if key != "color"}
        for item in taxonomy
    ]

    apply_palette_to_taxonomy(taxonomy, "High contrast")

    assert [item["color"] for item in taxonomy] == SHADE_PALETTES["High contrast"][:4]
    assert [
        {key: value for key, value in item.items() if key != "color"}
        for item in taxonomy
    ] == non_color_values


def test_legacy_palette_names_migrate_without_remaining_visible_options():
    aliases = {
        "Default stop audit": "Default / Civic",
        "Infrastructure mix": "Default / Civic",
        "Civic map": "Default / Civic",
        "High contrast": "High contrast",
        "Colorblind friendly": "Colorblind friendly",
    }
    for legacy_name, expected in aliases.items():
        config = {"analytics_schema_version": 2, "shade_palette": legacy_name}
        assert migrate_legacy_analytics_config(config)["shade_palette"] == expected
        assert (
            published_app.normalize_published_visualization(config)["shade_palette"]
            == expected
        )


def test_builtin_map_and_legend_share_non_color_status_symbols():
    taxonomy = copy.deepcopy(DEFAULT_COVERAGE_TAXONOMY)
    stops = pd.DataFrame(
        [
            {
                "stop_id": str(index),
                "stop_name": name,
                "stop_lat": 27.95 + index * 0.001,
                "stop_lon": -82.45 - index * 0.001,
                "shading": name,
                "review_status": "Accepted",
                "priority_score": 0,
            }
            for index, name in enumerate(BUILTIN_CATEGORY_SYMBOLS)
        ]
    )
    visualization = copy.deepcopy(DEFAULT_VISUALIZATION)
    visualization["display_columns"] = ["stop_name"]

    deck = json.loads(build_deck_chart(stops, taxonomy, visualization).to_json())
    symbol_layer = next(
        layer for layer in deck["layers"] if layer["id"] == "semantic_status_symbols"
    )
    rendered_symbols = {
        row["shading"]: row["marker_symbol"] for row in symbol_layer["data"]
    }
    legend = published_app.taxonomy_legend_markup(taxonomy)

    assert rendered_symbols == BUILTIN_CATEGORY_SYMBOLS
    assert build_tooltip_text(stops, visualization).startswith(
        "Shade coverage: {shading}"
    )
    for category, symbol in BUILTIN_CATEGORY_SYMBOLS.items():
        assert category in legend
        assert f">{symbol}<" in legend


def test_semantic_symbols_align_with_pin_and_center_on_other_marker_shapes():
    taxonomy = copy.deepcopy(DEFAULT_COVERAGE_TAXONOMY)
    stops = pd.DataFrame(
        [
            {
                "stop_id": "1001",
                "stop_name": "Main St",
                "stop_lat": 27.9506,
                "stop_lon": -82.4572,
                "shading": "No Shade",
                "review_status": "Accepted",
                "priority_score": 0,
            }
        ]
    )

    for shape in ["Circle", "Pin", "Square", "Diamond", "Triangle"]:
        visualization = copy.deepcopy(DEFAULT_VISUALIZATION)
        visualization.update({"marker_shape": shape, "marker_size": 24})
        for chart_builder in (build_deck_chart, published_app.build_deck_chart):
            layers = json.loads(
                chart_builder(stops, taxonomy, visualization).to_json()
            )["layers"]
            symbol_layer = next(
                layer for layer in layers if layer["id"] == "semantic_status_symbols"
            )

            assert symbol_layer["data"][0]["marker_symbol_offset"] == (
                [0, -11] if shape == "Pin" else [0, 0]
            )


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
    assert visuals_deck["useDevicePixels"] == 3


def test_citywide_map_uses_bounds_aware_detail_zoom():
    stops = pd.DataFrame(
        [
            {
                "stop_id": "northwest",
                "stop_lat": 40.50,
                "stop_lon": -80.10,
                "shading": "Needs Review",
            },
            {
                "stop_id": "southeast",
                "stop_lat": 40.36,
                "stop_lon": -79.86,
                "shading": "Needs Review",
            },
        ]
    )

    for chart_builder in (build_deck_chart, published_app.build_deck_chart):
        deck = json.loads(chart_builder(stops, [], copy.deepcopy(DEFAULT_VISUALIZATION)).to_json())
        view = deck["initialViewState"]

        assert view["zoom"] > 11
        assert abs(view["latitude"] - 40.43) < 1e-9
        assert abs(view["longitude"] - -79.98) < 1e-9


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


def test_very_dense_overviews_add_aggregation_and_keep_stops_selectable():
    stops = pd.DataFrame(
        [
            {
                "stop_id": str(index),
                "stop_name": f"Stop {index}",
                "stop_lat": 40.36 + (index % 40) * 0.003,
                "stop_lon": -80.10 + (index // 40) * 0.003,
                "shading": "Needs Review",
                "review_status": "Unlabeled",
                "priority_score": 0,
            }
            for index in range(1200)
        ]
    )

    for chart_builder in (build_deck_chart, published_app.build_deck_chart):
        deck = json.loads(
            chart_builder(stops, [], copy.deepcopy(DEFAULT_VISUALIZATION)).to_json()
        )
        layers = {layer["id"]: layer for layer in deck["layers"]}

        assert layers["stop_clusters"]["@@type"] == "GridLayer"
        assert layers["stop_clusters"]["pickable"] is False
        assert layers["stops_layer_circle"]["pickable"] is True
        assert layers["stops_layer_circle"]["data"][0]["marker_size"] == 7
        assert layers["stops_layer_circle"]["opacity"] == 0.16


def test_pittsburgh_bench_map_keeps_presence_colors_visible_without_grid_overlay():
    import stop_gis.builder.app as builder_app

    stops = builder_app.load_seed_dataset(
        builder_app.DEFAULT_TAXONOMY, builder_app.DEFAULT_PROJECT
    )
    visualization = copy.deepcopy(builder_app.DEFAULT_VISUALIZATION)
    published_app.configure_assessment_display(
        visualization, builder_app.DEFAULT_ASSESSMENT_MODES
    )

    for chart_builder in (build_deck_chart, published_app.build_deck_chart):
        deck = json.loads(chart_builder(stops, [], visualization).to_json())
        layers = {layer["id"]: layer for layer in deck["layers"]}
        points = layers["stops_layer_circle"]["data"]
        colors = {
            row["bench_presence"]: row["fill_color"]
            for row in points
            if row["bench_presence"]
        }

        assert "stop_clusters" not in layers
        assert layers["stops_layer_circle"]["opacity"] == 0.82
        assert colors["present"] == [0, 114, 178]
        assert colors["absent"] == [213, 94, 0]

    legend = published_app.field_legend_markup(
        stops, visualization, "bench_presence"
    )
    assert "Bench" in legend
    assert "No bench" in legend
    assert "Not mapped" in legend
    assert published_app.bench_evidence_summary(stops, visualization) == (
        "Bench leads: 112 · No-bench leads: 517 · Not mapped: 1,997. "
        "All source evidence is unreviewed."
    )


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
