from builder_app import *


def session_backed_color_picker(label: str, default: Any, key: str) -> str:
    """Initialize a keyed color once without also passing a widget default."""
    if key not in st.session_state:
        st.session_state[key] = normalize_hex_color(default)
    return st.color_picker(label, key=key)


def render_palette_controls(
    visualization: dict[str, Any],
    stops: pd.DataFrame,
    taxonomy: list[dict[str, Any]],
    color_options: dict[str, str],
) -> None:
    field = color_options.get(
        visualization.get("color_by", "Shade coverage"), "shading"
    )
    st.markdown("#### Color Palette")
    if field == "shading":
        previous_palette = visualization.get("shade_palette", "Custom")
        palette_options = ["Custom"] + list(SHADE_PALETTES)
        if previous_palette not in palette_options:
            previous_palette = "Custom"
        selected_palette = st.selectbox(
            "Premade shade palette",
            palette_options,
            index=palette_options.index(previous_palette),
        )
        if selected_palette != "Custom" and selected_palette != previous_palette:
            palette = SHADE_PALETTES[selected_palette]
            for index, item in enumerate(taxonomy):
                color = palette[index % len(palette)]
                item["color"] = color
                st.session_state[f"shade_color_{index}"] = color
        visualization["shade_palette"] = selected_palette

        grid = st.columns(2)
        for index, item in enumerate(taxonomy):
            name = str(item.get("name", "")).strip() or f"Category {index + 1}"
            with grid[index % 2]:
                item["color"] = session_backed_color_picker(
                    name,
                    normalize_hex_color(item.get("color", "#808080")),
                    key=f"shade_color_{index}",
                )
        return

    if field == "review_status":
        review_colors = visualization.setdefault("review_status_colors", {})
        grid = st.columns(2)
        for index, status in enumerate(REVIEW_STATUS_COLORS):
            review_colors.setdefault(status, rgb_to_hex(REVIEW_STATUS_COLORS[status]))
            with grid[index % 2]:
                review_colors[status] = st.color_picker(
                    status,
                    normalize_hex_color(review_colors[status]),
                    key=f"review_color_{status}",
                )
        return

    if field == "priority_score":
        priority_colors = visualization.setdefault(
            "priority_colors", DEFAULT_VISUALIZATION["priority_colors"].copy()
        )
        grid = st.columns(3)
        with grid[0]:
            priority_colors["low"] = st.color_picker(
                "Low score",
                normalize_hex_color(
                    priority_colors.get("low"),
                    DEFAULT_VISUALIZATION["priority_colors"]["low"],
                ),
                key="priority_color_low",
            )
        with grid[1]:
            priority_colors["mid"] = st.color_picker(
                "Mid score",
                normalize_hex_color(
                    priority_colors.get("mid"),
                    DEFAULT_VISUALIZATION["priority_colors"]["mid"],
                ),
                key="priority_color_mid",
            )
        with grid[2]:
            priority_colors["high"] = st.color_picker(
                "High score",
                normalize_hex_color(
                    priority_colors.get("high"),
                    DEFAULT_VISUALIZATION["priority_colors"]["high"],
                ),
                key="priority_color_high",
            )
        return

    color_map = ensure_field_color_map(visualization, stops, field)
    values = field_values_for_colors(stops, field)
    if not values:
        st.caption("No values are available for the selected column.")
        return
    total_unique = (
        stops[field]
        .fillna("Unknown")
        .astype(str)
        .str.strip()
        .replace("", "Unknown")
        .nunique()
    )
    if total_unique > len(values):
        st.caption(f"Showing colors for the first {len(values)} values in this column.")
    grid = st.columns(2)
    for index, value in enumerate(values):
        with grid[index % 2]:
            color_map[value] = st.color_picker(
                value[:80],
                normalize_hex_color(
                    color_map.get(value, COLOR_PALETTE[index % len(COLOR_PALETTE)])
                ),
                key=f"field_color_{field}_{index}",
            )


def gis_overlay_id(name: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "gis-overlay").lower()).strip("-")
    return f"{slug or 'gis-overlay'}-{index + 1}"


def parse_uploaded_gis_overlay(
    contents: bytes, filename: str
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".zip":
        geojson, metadata = parse_shapefile_overlay_zip(contents)
        return "Shapefile", geojson, metadata
    geojson, metadata = parse_geojson_overlay_bytes(contents)
    return "GeoJSON", geojson, metadata


def append_import_log(entry: dict[str, Any]) -> None:
    import_log = st.session_state.setdefault("import_log", [])
    import_log.append(entry)


def render_gis_overlay_controls(visualization: dict[str, Any]) -> None:
    st.subheader("GIS Overlays")
    overlays = clean_gis_overlays(visualization)
    uploaded = st.file_uploader(
        "Upload GeoJSON or zipped Shapefile overlay",
        type=["geojson", "json", "zip"],
        key="gis_overlay_upload",
        help="Use this for real map layers such as routes, shelters, boundaries, service areas, or destinations.",
    )
    st.caption(
        f"Overlay upload limit: {format_bytes(max_upload_bytes())}; ZIPs may expand to "
        f"{format_bytes(max_zip_uncompressed_bytes())}."
    )
    overlay_cols = st.columns([1.2, 1, 1])
    overlay_name = overlay_cols[0].text_input(
        "Overlay name", key="gis_overlay_name", placeholder="Transit service area"
    )
    overlay_category = overlay_cols[1].selectbox(
        "Overlay category", GIS_OVERLAY_CATEGORIES, key="gis_overlay_category"
    )
    overlay_color = overlay_cols[2].color_picker(
        "Overlay color", "#2563eb", key="gis_overlay_color"
    )
    source_cols = st.columns([1, 1])
    overlay_source = source_cols[0].text_input(
        "Source", key="gis_overlay_source", placeholder="Agency, dataset, or URL"
    )
    overlay_license = source_cols[1].text_input(
        "License", key="gis_overlay_license", placeholder="Optional"
    )
    style_cols = st.columns([1, 1, 1])
    overlay_opacity = style_cols[0].slider(
        "Overlay opacity", 0.05, 1.0, 0.35, 0.05, key="gis_overlay_opacity"
    )
    overlay_line_width = style_cols[1].slider(
        "Line width", 1, 12, 2, 1, key="gis_overlay_line_width"
    )
    overlay_visible = style_cols[2].checkbox(
        "Visible by default", value=True, key="gis_overlay_visible"
    )

    if st.button("Add GIS overlay", type="primary", disabled=uploaded is None):
        if uploaded is None:
            st.warning("Upload a GeoJSON file or zipped Shapefile first.")
        elif getattr(uploaded, "size", 0) > max_upload_bytes():
            st.error(
                f"This overlay is larger than the {format_bytes(max_upload_bytes())} limit."
            )
        else:
            try:
                overlay_format, geojson, metadata = parse_uploaded_gis_overlay(
                    uploaded.getvalue(), uploaded.name
                )
                name = (
                    overlay_name.strip()
                    or Path(uploaded.name).stem.replace("_", " ").title()
                )
                overlay = {
                    "id": gis_overlay_id(name, len(overlays)),
                    "name": name,
                    "category": overlay_category,
                    "source": overlay_source.strip(),
                    "license": overlay_license.strip(),
                    "filename": uploaded.name,
                    "format": overlay_format,
                    "color": normalize_hex_color(overlay_color),
                    "opacity": overlay_opacity,
                    "line_width": overlay_line_width,
                    "visible": overlay_visible,
                    "metadata": metadata,
                    "imported_at": timestamp_with_timezone(),
                    "geojson": geojson,
                }
                overlays.append(overlay)
                visualization["gis_overlays"] = overlays
                append_import_log(
                    {
                        "source": name,
                        "format": f"GIS overlay: {overlay_format}",
                        "rows": int(metadata.get("features", 0)),
                        "imported_at": overlay["imported_at"],
                        "original_filename": uploaded.name,
                        "metadata": {
                            "category": overlay_category,
                            "geometry_types": metadata.get("geometry_types", ""),
                            "source": overlay_source.strip(),
                            "license": overlay_license.strip(),
                        },
                    }
                )
                st.success(
                    f"Added {name} with {metadata.get('features', 0)} feature(s)."
                )
                st.rerun()
            except Exception as error:
                st.error(f"Could not import GIS overlay: {error}")

    if not overlays:
        st.caption("No uploaded GIS overlays yet.")
        return

    delete_index: int | None = None
    for index, overlay in enumerate(overlays):
        label = f"{overlay.get('name', f'GIS overlay {index + 1}')} ({overlay.get('category', 'Other')})"
        with st.expander(label, expanded=False):
            edit_cols = st.columns([1, 1, 1])
            overlay["visible"] = edit_cols[0].checkbox(
                "Visible",
                value=bool(overlay.get("visible", True)),
                key=f"gis_overlay_visible_{index}",
            )
            overlay["color"] = edit_cols[1].color_picker(
                "Color",
                normalize_hex_color(
                    overlay.get("color", COLOR_PALETTE[index % len(COLOR_PALETTE)])
                ),
                key=f"gis_overlay_color_{index}",
            )
            overlay["opacity"] = edit_cols[2].slider(
                "Opacity",
                0.05,
                1.0,
                float(overlay.get("opacity", 0.35)),
                0.05,
                key=f"gis_overlay_opacity_{index}",
            )
            overlay["line_width"] = st.slider(
                "Line width",
                1,
                12,
                int(overlay.get("line_width", 2)),
                1,
                key=f"gis_overlay_width_{index}",
            )
            overlay["name"] = st.text_input(
                "Name", str(overlay.get("name", "")), key=f"gis_overlay_name_{index}"
            )
            category = str(overlay.get("category", "Other"))
            if category not in GIS_OVERLAY_CATEGORIES:
                category = "Other"
            overlay["category"] = st.selectbox(
                "Category",
                GIS_OVERLAY_CATEGORIES,
                index=GIS_OVERLAY_CATEGORIES.index(category),
                key=f"gis_overlay_category_{index}",
            )
            meta = overlay.get("metadata", {})
            st.caption(
                f"{overlay.get('format', 'GIS')} | {meta.get('features', 0)} feature(s) | "
                f"{meta.get('geometry_types', 'Unknown geometry')}"
            )
            if st.button("Remove overlay", key=f"remove_gis_overlay_{index}"):
                delete_index = index

    if delete_index is not None:
        overlays.pop(delete_index)
        visualization["gis_overlays"] = overlays
        st.rerun()
    else:
        visualization["gis_overlays"] = overlays


def render_visuals_page() -> None:
    st.title("Metrics And Visualizations")
    visualization = st.session_state["visualization"]
    stops = st.session_state["stops"]
    taxonomy = st.session_state["taxonomy"]

    controls, preview = st.columns([0.85, 1.15])
    with controls:
        with st.expander("Visualization Controls", expanded=True):
            with st.container(height=VISUAL_MAP_HEIGHT, border=False):
                color_options = get_color_options(stops)
                if visualization.get("color_by") not in color_options:
                    visualization["color_by"] = "Shade coverage"
                color_labels = list(color_options)
                visualization["color_by"] = st.selectbox(
                    "Color stops by",
                    color_labels,
                    index=color_labels.index(visualization["color_by"]),
                )
                marker_shape = visualization.get("marker_shape", "Circle")
                if marker_shape not in MARKER_SHAPES:
                    marker_shape = "Circle"
                visualization["marker_shape"] = st.selectbox(
                    "Marker shape",
                    MARKER_SHAPES,
                    index=MARKER_SHAPES.index(marker_shape),
                )
                visualization["marker_size"] = st.slider(
                    "Marker size",
                    4,
                    48,
                    int(visualization.get("marker_size", 7)),
                    1,
                )
                visualization["marker_opacity"] = st.slider(
                    "Marker opacity",
                    0.1,
                    1.0,
                    float(visualization.get("marker_opacity", 0.82)),
                    0.05,
                )
                visualization["marker_stroke_color"] = st.color_picker(
                    "Marker outline",
                    normalize_hex_color(
                        visualization.get("marker_stroke_color", "#141414"), "#141414"
                    ),
                )
                visualization["marker_stroke_width"] = st.slider(
                    "Outline width",
                    0,
                    6,
                    int(visualization.get("marker_stroke_width", 1)),
                    1,
                )
                map_style = visualization.get("map_style", "High contrast")
                if map_style not in MAP_STYLES:
                    map_style = "High contrast"
                visualization["map_style"] = st.selectbox(
                    "Base map style",
                    list(MAP_STYLES),
                    index=list(MAP_STYLES).index(map_style),
                )
                overlay_options = get_available_overlays(stops)
                visualization["overlays"] = clean_selected_options(
                    visualization.get("overlays", []), overlay_options
                )
                if overlay_options:
                    visualization["overlays"] = st.multiselect(
                        "Dataset-backed context fields",
                        overlay_options,
                        default=visualization["overlays"],
                        help=(
                            "These options expose contextual fields that are already attached to stop rows, "
                            "such as routes, ridership, shelters, destinations, or local project attributes. They only appear "
                            "when those columns have usable values: at least one non-null cell with text or data "
                            "after blank spaces are trimmed."
                        ),
                    )
                else:
                    visualization["overlays"] = []
                    st.caption(
                        "No optional context layers are available in the active dataset."
                    )

                st.divider()
                render_gis_overlay_controls(visualization)

                metric_options = get_available_metric_cards(stops)
                visualization["metric_cards"] = clean_selected_options(
                    visualization.get("metric_cards", []), metric_options
                )
                if metric_options:
                    visualization["metric_cards"] = st.multiselect(
                        "Dashboard summaries",
                        metric_options,
                        default=visualization["metric_cards"],
                    )
                else:
                    visualization["metric_cards"] = []
                    st.caption(
                        "No dashboard summaries are available for the active dataset yet."
                    )

                st.subheader("Custom Chart")
                charts = get_custom_charts(stops, visualization)
                chart_columns = get_chart_column_options(stops)
                if chart_columns:
                    chart_count = st.number_input(
                        "Number of custom charts",
                        min_value=1,
                        max_value=MAX_CUSTOM_CHARTS,
                        value=len(charts),
                        step=1,
                        help="Configure up to 10 charts for the public Analytics tab.",
                    )
                    chart_count = int(chart_count)
                    while len(charts) < chart_count:
                        charts.append(
                            ensure_custom_chart_defaults(
                                stops,
                                json.loads(json.dumps(DEFAULT_CUSTOM_CHART)),
                                len(charts),
                            )
                        )
                    charts = charts[:chart_count]
                    for index, chart in enumerate(charts):
                        chart = ensure_custom_chart_defaults(stops, chart, index)
                        with st.expander(
                            chart.get("title", f"Custom chart {index + 1}"),
                            expanded=index == 0,
                        ):
                            chart["title"] = st.text_input(
                                "Chart title",
                                chart.get("title", f"Custom chart {index + 1}"),
                                key=f"custom_chart_title_{index}",
                            )
                            chart["x"] = st.selectbox(
                                "X column",
                                chart_columns,
                                index=chart_columns.index(chart["x"]),
                                format_func=display_label,
                                key=f"custom_chart_x_{index}",
                            )
                            y_options = [RECORD_COUNT_FIELD] + chart_columns
                            chart["y"] = st.selectbox(
                                "Y column",
                                y_options,
                                index=y_options.index(chart["y"]),
                                format_func=lambda value: (
                                    value
                                    if value == RECORD_COUNT_FIELD
                                    else display_label(value)
                                ),
                                key=f"custom_chart_y_{index}",
                            )
                            chart["aggregation"] = st.selectbox(
                                "Y aggregation",
                                CHART_AGGREGATIONS,
                                index=CHART_AGGREGATIONS.index(chart["aggregation"]),
                                key=f"custom_chart_aggregation_{index}",
                            )
                            chart["chart_type"] = st.selectbox(
                                "Chart type",
                                CHART_TYPES,
                                index=CHART_TYPES.index(chart["chart_type"]),
                                key=f"custom_chart_type_{index}",
                            )
                    visualization["custom_charts"] = charts
                else:
                    st.caption("Import a dataset before configuring a custom chart.")

                current_display_columns = get_selected_display_columns(
                    stops, visualization
                )
                display_columns = st.multiselect(
                    "Published data columns",
                    get_display_column_options(stops),
                    default=current_display_columns,
                    format_func=display_label,
                    help="Choose which stop fields appear in the public analytics data table and map hover details.",
                )
                if display_columns:
                    visualization["display_columns"] = display_columns
                else:
                    st.warning("Select at least one column for the public data table.")
                    visualization["display_columns"] = current_display_columns
                visualization["show_legend"] = st.checkbox(
                    "Show legend", value=visualization["show_legend"]
                )
                visualization["show_downloads"] = st.checkbox(
                    "Show public downloads", value=visualization["show_downloads"]
                )

                st.divider()
                render_palette_controls(visualization, stops, taxonomy, color_options)
                st.divider()
                st.subheader("Priority Formula")
                weights = visualization["priority_weights"]
                priority_factors = []
                if has_column_data(stops, "ridership"):
                    priority_factors.append(("ridership", "Ridership weight"))
                if "shading" in stops.columns:
                    priority_factors.append(("low_shade", "Low shade weight"))

                if priority_factors:
                    for key, label in priority_factors:
                        weights[key] = st.slider(
                            label, 0.0, 1.0, float(weights.get(key, 0.0)), 0.05
                        )
                else:
                    st.caption(
                        "No priority factors are available in the active dataset."
                    )
                st.caption(
                    "The preview stores the selected formula version with exported configuration."
                )

    st.session_state["stops"]["priority_score"] = calculate_priority_scores(
        stops, visualization["priority_weights"]
    )
    display_columns = get_selected_display_columns(
        st.session_state["stops"], visualization
    )

    with preview:
        st.subheader("Map Preview")
        if stops.empty:
            st.warning("Import a dataset before configuring the map.")
        else:
            st.pydeck_chart(
                build_deck_chart(stops, st.session_state["taxonomy"], visualization),
                width="stretch",
                height=VISUAL_MAP_HEIGHT,
            )
    st.subheader("Custom Chart Preview")
    if stops.empty:
        st.info("Import a dataset to preview a custom chart.")
    else:
        render_custom_charts(st.session_state["stops"], visualization)

    st.subheader("Data Table Preview")
    if stops.empty:
        st.info("Import a dataset to preview selected data columns.")
    else:
        st.dataframe(
            st.session_state["stops"].loc[:, display_columns].head(20),
            width="stretch",
            hide_index=True,
        )

    st.subheader("Available Fields")
    active_columns = get_active_data_columns(stops)
    field_summary = pd.DataFrame(
        [
            {"field": column, "non_null_values": int(stops[column].notna().sum())}
            for column in active_columns
        ]
    )
    st.dataframe(field_summary, width="stretch", hide_index=True)
