import streamlit as st

import published_app
from builder_app import active_raw_labels, study_config_payload
from platform_store import list_assessments
from shade_gis.builder_imports import calculate_priority_scores
from shade_gis.shade_dimensions import (
    normalize_coverage_display_taxonomy,
    normalize_source_taxonomy,
)


def render_preview_page() -> None:
    project = st.session_state["project"]
    methodology = st.session_state["methodology"]
    taxonomy = st.session_state["taxonomy"]
    config = study_config_payload()
    assessment_modes = config.get("assessment_modes", [])
    visualization = published_app.configure_assessment_display(
        st.session_state["visualization"], assessment_modes
    )
    stops = published_app.materialize_assessment_columns(st.session_state["stops"])
    stops["priority_score"] = calculate_priority_scores(
        stops, visualization["priority_weights"]
    )
    stops = published_app.add_composite_scores(
        stops, assessment_modes, config.get("scoring", [])
    )
    raw_labels = active_raw_labels()
    assessments = list_assessments(str(st.session_state.get("active_project_id") or ""))
    voting = published_app.normalize_voting_config(
        visualization.get("voting"), taxonomy
    )
    voting["shade_source_taxonomy"] = normalize_source_taxonomy(
        methodology.get("shade_source_taxonomy")
    )
    voting["shade_coverage_taxonomy"] = normalize_coverage_display_taxonomy(
        methodology.get("shade_coverage_taxonomy"),
        taxonomy,
    )
    study_id = str(
        config.get("study_id") or project.get("name") or "shade-study"
    ).strip()

    st.title(project["name"])
    st.markdown(f"### {methodology['summary']}")
    st.caption(
        f"{project['agency']} | {project['region']} | dataset v{project['dataset_version']}"
    )

    if stops.empty:
        st.warning("Import a stop dataset before previewing the public app.")
        return

    filters = published_app.current_map_filters(stops, "preview")
    visible_stops = published_app.filter_map_stops(
        published_app.filter_unlabeled_stops(stops, filters["show_unlabeled"]),
        filters["search_query"],
        filters["selected_routes"],
        filters,
    )

    with st.expander("Map and analytics filters", expanded=False):
        published_app.render_map_filter_controls(stops, "preview")

    tabs = st.tabs(
        ["Map", "Analytics", "Methodology", "Exports"],
        key="preview_tabs",
        on_change="rerun",
    )
    if tabs[0].open:
        with tabs[0]:
            if visible_stops.empty:
                st.info("No stops match the current visibility settings.")
            else:
                map_cols = st.columns([2, 1])
                with map_cols[0]:
                    map_selection = st.pydeck_chart(
                        published_app.build_deck_chart(
                            visible_stops, taxonomy, visualization
                        ),
                        width="stretch",
                        height=published_app.MAP_PANEL_HEIGHT,
                        on_select="rerun",
                        selection_mode="single-object",
                        key="preview_stops_map",
                    )
                    selected_stop_id = (
                        published_app.selected_stop_id_from_map_selection(
                            map_selection, visible_stops
                        )
                    )
                    if selected_stop_id:
                        st.session_state["preview_selected_stop_id"] = selected_stop_id
                with map_cols[1]:
                    with st.container(
                        height=published_app.MAP_PANEL_HEIGHT, border=False
                    ):
                        published_app.render_stop_and_voting_panel(
                            visible_stops,
                            visualization,
                            "preview",
                            study_id,
                            taxonomy,
                            voting,
                            app_dir=published_app.APP_DIR,
                            preview=True,
                        )
            st.caption(
                f"{len(visible_stops):,} of {len(stops):,} stops match the active map filters."
            )
            if visualization.get("show_legend", True):
                published_app.render_taxonomy_legend(taxonomy)
    elif tabs[1].open:
        with tabs[1]:
            published_app.render_assessment_summaries(visible_stops, assessment_modes)
            published_app.render_issue_analytics_dashboard(
                visible_stops,
                visualization,
                raw_labels,
                include_agreement=True,
            )
            published_app.render_custom_charts(visible_stops, visualization)
    elif tabs[2].open:
        with tabs[2]:
            published_app.render_methodology(config)
    elif tabs[3].open:
        with tabs[3]:
            if visualization.get("show_downloads", True):
                published_app.render_export_files(
                    stops,
                    raw_labels,
                    config,
                    st.session_state["import_log"],
                    key_prefix="preview",
                    assessments=assessments,
                )
            else:
                st.info("Public file downloads are disabled for this study.")
            published_app.render_dataset_provenance(st.session_state["import_log"])
