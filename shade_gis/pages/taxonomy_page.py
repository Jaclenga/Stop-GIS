import streamlit as st

from platform_store import list_assessments
from shade_gis.taxonomy_components import (
    bump_taxonomy_workspace_revision,
    render_taxonomy_editor,
)
from stop_gis.assessment_components import render_scoring_builder
from stop_gis.assessment_modes import (
    DEFAULT_SCORING_PROFILES,
    STUDY_TEMPLATES,
    apply_template_to_modes,
    modes_for_template,
    observed_dimension_values,
)


def _render_taxonomy_styles() -> None:
    st.markdown(
        """
        <style>
        .st-key-taxonomy_workspace {
            gap: 0.22rem;
            max-width: 960px;
            margin: 1.25rem auto 3rem;
        }
        .st-key-taxonomy_view_switcher {
            margin-bottom: 0.65rem;
        }
        div[class*="st-key-taxonomy_group_"] {
            margin: 1rem 0 0.28rem;
        }
        div[class*="st-key-taxonomy_group_"] button {
            background: transparent;
            border: 0;
            box-shadow: none;
            color: #526071;
            min-height: 1.85rem;
            padding: 0.15rem 0.1rem;
            text-align: left;
        }
        div[class*="st-key-taxonomy_group_"] button p {
            font-size: 0.76rem;
            font-weight: 760;
            letter-spacing: 0.075em;
            text-transform: uppercase;
        }
        div[class*="st-key-taxonomy_concept_"]:not([class*="st-key-taxonomy_concept_body_"]) {
            border-bottom: 1px solid #e8edf2;
            padding: 0.14rem 0;
        }
        div[class*="st-key-taxonomy_concept_"]:not([class*="st-key-taxonomy_concept_body_"]):has(.taxonomy-disabled-marker)
        div[class*="st-key-taxonomy_dimension_summary_"] button {
            opacity: 0.62;
        }
        .taxonomy-disabled-marker {
            display: none;
        }
        div[class*="st-key-taxonomy_dimension_summary_"] button,
        div[class*="st-key-taxonomy_reference_"] > div:first-child button {
            background: transparent;
            border: 0;
            border-radius: 9px;
            box-shadow: none;
            color: #172033;
            min-height: 4.15rem;
            padding: 0.42rem 0.55rem;
            text-align: left;
        }
        div[class*="st-key-taxonomy_dimension_summary_"] button:hover,
        div[class*="st-key-taxonomy_reference_"] > div:first-child button:hover {
            background: #f4f7fa;
            color: #0f172a;
        }
        div[class*="st-key-taxonomy_dimension_summary_"] button:focus-visible,
        div[class*="st-key-taxonomy_group_"] button:focus-visible,
        div[class*="st-key-taxonomy_reference_"] > div:first-child button:focus-visible {
            box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.18);
            outline: 2px solid #2563eb;
            outline-offset: 1px;
        }
        div[class*="st-key-taxonomy_dimension_summary_"] button p,
        div[class*="st-key-taxonomy_reference_"] > div:first-child button p {
            color: #526071;
            font-size: 0.875rem;
            line-height: 1.32;
            margin: 0;
        }
        div[class*="st-key-taxonomy_dimension_summary_"] button p strong,
        div[class*="st-key-taxonomy_reference_"] > div:first-child button p strong {
            color: #172033;
            font-size: 1rem;
            font-weight: 700;
        }
        div[class*="st-key-taxonomy_dimension_summary_"] button code {
            background: #e9f0f8;
            border: 1px solid #cbd8e6;
            border-radius: 999px;
            color: #24364b;
            display: inline-block;
            font-family: inherit;
            font-size: 0.77rem;
            font-weight: 650;
            line-height: 1.25;
            margin: 0.22rem 0.18rem 0 0;
            padding: 0.18rem 0.48rem;
        }
        div[class*="st-key-taxonomy_dimension_state_"] button {
            min-height: 2.2rem;
            padding-left: 0.45rem;
            padding-right: 0.45rem;
        }
        div[class*="st-key-taxonomy_dimension_state_"] button[kind="primary"] {
            background: #2563eb;
            border-color: #2563eb;
            color: #ffffff;
        }
        div[class*="st-key-taxonomy_concept_body_"] {
            border-left: 2px solid #d8e2ec;
            margin: 0 0 0.55rem 1rem;
            padding: 0.35rem 0 0.45rem 1rem;
        }
        .taxonomy-values-heading {
            color: #64748b;
            font-size: 0.72rem;
            font-weight: 760;
            letter-spacing: 0.08em;
            margin: 0.15rem 0 0.22rem;
            text-transform: uppercase;
        }
        .taxonomy-value-row {
            align-items: baseline;
            border-top: 1px solid #edf1f5;
            display: grid;
            gap: 0.8rem;
            grid-template-columns: minmax(8rem, 0.32fr) minmax(12rem, 0.68fr);
            padding: 0.36rem 0;
        }
        .taxonomy-value-name {
            color: #1e293b;
            font-size: 0.87rem;
            font-weight: 680;
        }
        .taxonomy-value-definition {
            color: #5b6776;
            font-size: 0.84rem;
        }
        .taxonomy-reference-status {
            background: #f1f5f9;
            border-radius: 999px;
            color: #475569;
            display: inline-block;
            font-size: 0.78rem;
            font-weight: 650;
            padding: 0.28rem 0.62rem;
        }
        .st-key-taxonomy_add_concept {
            background: #f7f9fb;
            border: 1px solid #dbe3ec;
            border-radius: 10px;
            margin: 0.6rem 0 1rem;
            padding: 0.85rem 1rem 1rem;
        }
        .st-key-taxonomy_add_concept h3 {
            font-size: 1.05rem;
            margin: 0 0 0.25rem;
        }
        .st-key-taxonomy_add_dimension_action button[kind="primary"] {
            background: #2563eb;
            border-color: #2563eb;
            color: #ffffff;
        }
        .st-key-taxonomy_add_dimension_action button[kind="primary"]:hover {
            background: #1d4ed8;
            border-color: #1d4ed8;
        }
        .st-key-taxonomy_advanced {
            margin-top: 2rem;
        }
        @media (max-width: 720px) {
            .st-key-taxonomy_workspace {
                margin-top: 1rem;
            }
            div[class*="st-key-taxonomy_concept_body_"] {
                margin-left: 0.35rem;
                padding-left: 0.7rem;
            }
            .taxonomy-value-row {
                gap: 0.15rem;
                grid-template-columns: 1fr;
            }
            div[class*="st-key-taxonomy_dimension_summary_"] button {
                min-height: 4.4rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_advanced_configuration() -> None:
    with st.expander("Advanced configuration", expanded=False):
        st.caption("Apply a starter set or configure optional composite scores.")
        template_labels = {
            config["label"]: key for key, config in STUDY_TEMPLATES.items()
        }
        template_label = st.selectbox(
            "Starter template", list(template_labels), key="assessment_template"
        )
        if st.button("Apply template", key="apply_assessment_template"):
            st.session_state["assessment_modes"] = apply_template_to_modes(
                st.session_state.get("assessment_modes", []),
                template_labels[template_label],
            )
            bump_taxonomy_workspace_revision()
            st.rerun()
        st.session_state["scoring"] = render_scoring_builder(
            st.session_state.get("scoring", DEFAULT_SCORING_PROFILES)
        )


def render_taxonomy_page() -> None:
    st.title("Taxonomy")
    st.markdown("Define the coding dimensions used in this project.")
    _render_taxonomy_styles()

    methodology = st.session_state["methodology"]
    taxonomy = st.session_state["taxonomy"]
    current_modes = st.session_state.get(
        "assessment_modes", modes_for_template("passenger_comfort")
    )
    stops = st.session_state.get("stops")
    observation_records = (
        stops.to_dict("records") if hasattr(stops, "to_dict") else []
    )
    project_id = st.session_state.get("active_project_id")
    if project_id:
        observation_records.extend(list_assessments(project_id).to_dict("records"))
    observed_values = observed_dimension_values(observation_records)

    with st.container(key="taxonomy_workspace"):
        st.session_state["assessment_modes"] = render_taxonomy_editor(
            current_modes, methodology, taxonomy, observed_values
        )
        with st.container(key="taxonomy_advanced"):
            _render_advanced_configuration()
