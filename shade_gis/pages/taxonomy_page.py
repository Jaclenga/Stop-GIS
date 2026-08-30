import streamlit as st

from shade_gis.taxonomy_components import (
    coverage_taxonomy_table_frame,
    render_shade_coverage_taxonomy_editor,
    render_shade_source_taxonomy_editor,
    render_taxonomy_section_header,
    render_terminology_editor,
    reset_shade_coverage_definitions,
    reset_shade_source_definitions,
    source_taxonomy_table_frame,
    terminology_table_frame,
)
from shade_gis.ui_tables import render_dataframe_table
from stop_gis.assessment_components import render_assessment_mode_builder, render_scoring_builder
from stop_gis.assessment_modes import DEFAULT_SCORING_PROFILES
from stop_gis.assessment_modes import modes_for_template


def render_taxonomy_page() -> None:
    st.title("Assessment Design")
    st.markdown(
        "Define the shared terminology and coding framework used throughout this project."
    )
    methodology = st.session_state["methodology"]
    taxonomy = st.session_state["taxonomy"]

    st.markdown(
        """
        <style>
        .st-key-taxonomy_workspace {
            gap: 1.75rem;
            max-width: 1120px;
            margin: 2rem auto 2.25rem;
        }
        .st-key-taxonomy_card_terminology,
        .st-key-taxonomy_card_source,
        .st-key-taxonomy_card_coverage {
            background: #fbfcfd;
            border: 1px solid #e2e8f0;
            border-radius: 14px;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.025);
            gap: 1rem;
            padding: 1.35rem 1.5rem 1.5rem;
        }
        .st-key-taxonomy_card_terminology h3,
        .st-key-taxonomy_card_source h3,
        .st-key-taxonomy_card_coverage h3 {
            color: #172033;
            font-size: 1.3rem;
            font-weight: 700;
            letter-spacing: -0.015em;
            line-height: 1.3;
            margin: 0;
        }
        .st-key-taxonomy_card_terminology h3::before,
        .st-key-taxonomy_card_source h3::before,
        .st-key-taxonomy_card_coverage h3::before {
            color: #64748b;
            display: inline-block;
            font-family: "Material Symbols Rounded";
            font-size: 1.2rem;
            font-weight: 400;
            margin-right: 0.25rem;
            vertical-align: -0.13em;
        }
        .st-key-taxonomy_card_terminology h3::before { content: "menu_book"; }
        .st-key-taxonomy_card_source h3::before { content: "account_tree"; }
        .st-key-taxonomy_card_coverage h3::before { content: "pie_chart"; }
        div[class*="st-key-taxonomy_toggle_"] button,
        div[class*="st-key-taxonomy_reset_"] button {
            background: transparent;
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            box-shadow: none;
            color: #334155;
            font-size: 0.875rem;
            font-weight: 550;
            min-height: 2.25rem;
            padding: 0.35rem 0.75rem;
            transition: background-color 120ms ease, border-color 120ms ease, color 120ms ease;
        }
        div[class*="st-key-taxonomy_toggle_"] button:hover,
        div[class*="st-key-taxonomy_reset_"] button:hover {
            background: #f1f5f9;
            border-color: #94a3b8;
            color: #0f172a;
        }
        div[class*="st-key-taxonomy_toggle_"] button:focus-visible,
        div[class*="st-key-taxonomy_reset_"] button:focus-visible {
            border-color: #64748b;
            box-shadow: 0 0 0 3px rgba(100, 116, 139, 0.16);
            outline: none;
        }
        .st-key-terminology_table [data-testid="stDataFrame"],
        .st-key-shade_source_taxonomy_table [data-testid="stDataFrame"],
        .st-key-shade_coverage_taxonomy_table [data-testid="stDataFrame"] {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 10px;
            overflow: hidden;
        }
        .st-key-terminology_table [data-testid="stDataFrame"] > div,
        .st-key-shade_source_taxonomy_table [data-testid="stDataFrame"] > div,
        .st-key-shade_coverage_taxonomy_table [data-testid="stDataFrame"] > div {
            border-radius: 10px;
        }
        .st-key-taxonomy_workspace table.data-page-table {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-collapse: separate;
            border-radius: 10px;
            border-spacing: 0;
            color: #334155;
            overflow: hidden;
            table-layout: fixed;
            width: 100%;
        }
        .st-key-taxonomy_workspace table.data-page-table th,
        .st-key-taxonomy_workspace table.data-page-table td {
            border: 0;
            text-align: left;
            vertical-align: middle;
            white-space: normal;
        }
        .st-key-taxonomy_workspace table.data-page-table th {
            background: #f8fafc;
            border-bottom: 1px solid #e2e8f0;
            color: #64748b;
            font-size: 0.84rem;
            font-weight: 600;
            line-height: 1.35;
            padding: 0.78rem 1.05rem;
        }
        .st-key-taxonomy_workspace table.data-page-table td {
            background: #ffffff;
            border-bottom: 1px solid #edf1f5;
            font-size: 0.96rem;
            font-weight: 400;
            line-height: 1.62;
            overflow-wrap: anywhere;
            padding: 1rem 1.05rem;
        }
        .st-key-taxonomy_workspace table.data-page-table tbody tr:last-child td {
            border-bottom: 0;
        }
        .st-key-taxonomy_workspace table.data-page-table th:first-child,
        .st-key-taxonomy_workspace table.data-page-table td:first-child {
            color: #1e293b;
            font-weight: 500;
            width: 30%;
        }
        .st-key-taxonomy_workspace table.data-page-table th:last-child,
        .st-key-taxonomy_workspace table.data-page-table td:last-child {
            width: 70%;
        }
        @media (max-width: 760px) {
            .st-key-taxonomy_workspace {
                margin-top: 1.25rem;
            }
            .st-key-taxonomy_card_terminology,
            .st-key-taxonomy_card_source,
            .st-key-taxonomy_card_coverage {
                border-radius: 12px;
                padding: 1rem;
            }
            .st-key-taxonomy_workspace table.data-page-table th,
            .st-key-taxonomy_workspace table.data-page-table td {
                padding-left: 0.8rem;
                padding-right: 0.8rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="taxonomy_workspace"):
        with st.container(key="taxonomy_card_modes"):
            current_modes = st.session_state.get(
                "assessment_modes", modes_for_template("passenger_comfort")
            )
            st.session_state["assessment_modes"] = render_assessment_mode_builder(current_modes)
            st.session_state["scoring"] = render_scoring_builder(
                st.session_state.get("scoring", DEFAULT_SCORING_PROFILES)
            )

        with st.container(key="taxonomy_card_terminology"):
            terminology_editing = render_taxonomy_section_header(
                "Terminology",
                "terminology",
                "Select Edit to change definitions or add and remove terms.",
            )
            with st.container(key="terminology_table"):
                if terminology_editing:
                    render_terminology_editor(methodology)
                else:
                    render_dataframe_table(
                        terminology_table_frame(methodology),
                        {"term": "Term", "operational_definition": "Operational definition"},
                    )

        with st.container(key="taxonomy_card_source"):
            source_editing = render_taxonomy_section_header(
                "Shade source taxonomy",
                "shade_source",
                "Select Edit to change display labels and operational definitions. Stored category identities remain stable.",
                reset_callback=reset_shade_source_definitions,
                reset_args=(methodology,),
            )
            with st.container(key="shade_source_taxonomy_table"):
                if source_editing:
                    render_shade_source_taxonomy_editor(methodology)
                else:
                    render_dataframe_table(
                        source_taxonomy_table_frame(methodology).drop(columns=["code"]),
                        {"shade_source": "Shade source", "operational_definition": "Operational definition"},
                    )

        with st.container(key="taxonomy_card_coverage"):
            coverage_editing = render_taxonomy_section_header(
                "Shade coverage taxonomy",
                "shade_coverage",
                "Select Edit to change display labels and operational definitions. Stored category identities remain stable.",
                reset_callback=reset_shade_coverage_definitions,
                reset_args=(methodology, taxonomy),
            )
            with st.container(key="shade_coverage_taxonomy_table"):
                if coverage_editing:
                    render_shade_coverage_taxonomy_editor(methodology, taxonomy)
                else:
                    render_dataframe_table(
                        coverage_taxonomy_table_frame(methodology, taxonomy).drop(columns=["code"]),
                        {"shade_coverage": "Shade coverage", "operational_definition": "Operational definition"},
                    )
