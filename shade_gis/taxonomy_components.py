"""Reusable taxonomy tables and editors."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from shade_gis.shade_dimensions import (
    SHADE_COVERAGE_TAXONOMY,
    SHADE_SOURCE_TAXONOMY,
    normalize_coverage_display_taxonomy,
    normalize_coverage_taxonomy,
    normalize_source_taxonomy,
    normalize_terminology,
)


def taxonomy_edit_mode_key(section: str) -> str:
    project_id = st.session_state.get("active_project_id", "draft")
    return f"taxonomy_edit_mode:{project_id}:{section}"


def toggle_taxonomy_edit_mode(mode_key: str) -> None:
    st.session_state[mode_key] = not bool(st.session_state.get(mode_key, False))


def taxonomy_editor_revision_key(section: str) -> str:
    project_id = st.session_state.get("active_project_id", "draft")
    return f"taxonomy_editor_revision:{project_id}:{section}"


def taxonomy_editor_key(section: str) -> str:
    project_id = st.session_state.get("active_project_id", "draft")
    base_key = f"{section}_taxonomy_editor:{project_id}"
    revision = int(st.session_state.get(taxonomy_editor_revision_key(section), 0) or 0)
    return f"{base_key}:{revision}" if revision else base_key


def bump_taxonomy_editor_revision(section: str) -> None:
    revision_key = taxonomy_editor_revision_key(section)
    st.session_state[revision_key] = int(st.session_state.get(revision_key, 0) or 0) + 1


def reset_shade_source_definitions(methodology: dict[str, Any]) -> None:
    default_definitions = {
        item["shade_source"]: item["operational_definition"]
        for item in SHADE_SOURCE_TAXONOMY
    }
    source_taxonomy = normalize_source_taxonomy(methodology.get("shade_source_taxonomy"))
    for item in source_taxonomy:
        item["operational_definition"] = default_definitions[item["code"]]
    methodology["shade_source_taxonomy"] = source_taxonomy
    bump_taxonomy_editor_revision("shade_source")


def reset_shade_coverage_definitions(
    methodology: dict[str, Any], taxonomy: list[dict[str, Any]]
) -> None:
    default_definitions = {
        item["shade_coverage"]: item["operational_definition"]
        for item in SHADE_COVERAGE_TAXONOMY
    }
    coverage_taxonomy = normalize_coverage_display_taxonomy(
        methodology.get("shade_coverage_taxonomy"), taxonomy
    )
    for item in coverage_taxonomy:
        item["operational_definition"] = default_definitions[item["code"]]
    methodology["shade_coverage_taxonomy"] = coverage_taxonomy

    canonical_taxonomy = normalize_coverage_taxonomy(taxonomy)
    for item in canonical_taxonomy:
        if item["name"] in default_definitions:
            item["description"] = default_definitions[item["name"]]
    taxonomy[:] = normalize_coverage_taxonomy(canonical_taxonomy)
    bump_taxonomy_editor_revision("shade_coverage")


def render_taxonomy_section_header(
    title: str,
    section: str,
    help_text: str,
    *,
    reset_callback: Any | None = None,
    reset_args: tuple[Any, ...] = (),
) -> bool:
    mode_key = taxonomy_edit_mode_key(section)
    editing = bool(st.session_state.get(mode_key, False))
    if editing and reset_callback is not None:
        title_column, reset_column, action_column = st.columns(
            [0.68, 0.20, 0.12], vertical_alignment="center"
        )
    else:
        title_column, action_column = st.columns([0.88, 0.12], vertical_alignment="center")
        reset_column = None
    with title_column:
        st.subheader(title, help=help_text)
    if reset_column is not None:
        with reset_column:
            st.button(
                "Reset definitions",
                type="secondary",
                width="stretch",
                key=f"taxonomy_reset_{section}_{st.session_state.get('active_project_id', 'draft')}",
                help="Restore the original operational definitions while keeping your display labels.",
                on_click=reset_callback,
                args=reset_args,
            )
    with action_column:
        st.button(
            "Done" if editing else "Edit",
            type="secondary",
            width="stretch",
            key=f"taxonomy_toggle_{section}_{st.session_state.get('active_project_id', 'draft')}",
            on_click=toggle_taxonomy_edit_mode,
            args=(mode_key,),
        )
    return editing


def terminology_table_frame(methodology: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(
        normalize_terminology(methodology.get("terminology")),
        columns=["term", "operational_definition"],
    )


def source_taxonomy_table_frame(methodology: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(
        normalize_source_taxonomy(methodology.get("shade_source_taxonomy")),
        columns=["code", "shade_source", "operational_definition"],
    )


def coverage_taxonomy_table_frame(
    methodology: dict[str, Any], taxonomy: list[dict[str, Any]]
) -> pd.DataFrame:
    return pd.DataFrame(
        normalize_coverage_display_taxonomy(
            methodology.get("shade_coverage_taxonomy"), taxonomy
        ),
        columns=["code", "shade_coverage", "operational_definition"],
    )


def render_terminology_editor(methodology: dict[str, Any]) -> list[dict[str, str]]:
    edited = st.data_editor(
        terminology_table_frame(methodology),
        column_config={
            "term": st.column_config.TextColumn("Term", required=True, width="small"),
            "operational_definition": st.column_config.TextColumn(
                "Operational definition", width="large"
            ),
        },
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        height="auto",
        row_height=44,
        key=f"terminology_editor:{st.session_state.get('active_project_id', 'draft')}",
    )
    normalized = normalize_terminology(edited.to_dict(orient="records"))
    methodology["terminology"] = normalized
    return normalized


def render_shade_source_taxonomy_editor(
    methodology: dict[str, Any],
) -> list[dict[str, str]]:
    edited = st.data_editor(
        source_taxonomy_table_frame(methodology),
        column_config={
            "shade_source": st.column_config.TextColumn("Shade source", width="small"),
            "operational_definition": st.column_config.TextColumn(
                "Operational definition", required=True, width="large"
            ),
        },
        column_order=["shade_source", "operational_definition"],
        num_rows="fixed",
        hide_index=True,
        width="stretch",
        height="auto",
        row_height=44,
        key=taxonomy_editor_key("shade_source"),
    )
    normalized = normalize_source_taxonomy(edited.to_dict(orient="records"))
    methodology["shade_source_taxonomy"] = normalized
    return normalized


def render_shade_coverage_taxonomy_editor(
    methodology: dict[str, Any], taxonomy: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    normalized_taxonomy = normalize_coverage_taxonomy(taxonomy)
    edited = st.data_editor(
        coverage_taxonomy_table_frame(methodology, normalized_taxonomy),
        column_config={
            "shade_coverage": st.column_config.TextColumn("Shade coverage", width="small"),
            "operational_definition": st.column_config.TextColumn(
                "Operational definition", required=True, width="large"
            ),
        },
        column_order=["shade_coverage", "operational_definition"],
        num_rows="fixed",
        hide_index=True,
        width="stretch",
        height="auto",
        row_height=44,
        key=taxonomy_editor_key("shade_coverage"),
    )
    display_taxonomy = normalize_coverage_display_taxonomy(
        edited.to_dict(orient="records"), normalized_taxonomy
    )
    methodology["shade_coverage_taxonomy"] = display_taxonomy
    definitions = {
        item["code"]: item["operational_definition"] for item in display_taxonomy
    }
    for item in normalized_taxonomy:
        if item["name"] in definitions and definitions[item["name"]]:
            item["description"] = definitions[item["name"]]
    taxonomy[:] = normalize_coverage_taxonomy(normalized_taxonomy)
    return taxonomy
