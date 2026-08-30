from typing import Any

import streamlit as st

from public_voting import (
    PUBLIC_COVERAGE_OPTIONS,
    normalize_voting_config,
    render_voting_panel,
)
from shade_gis.shade_dimensions import (
    normalize_coverage_display_taxonomy,
    normalize_source_taxonomy,
)


def render_voting_controls(
    visualization: dict[str, Any],
    taxonomy: list[dict[str, Any]],
) -> dict[str, Any]:
    voting = normalize_voting_config(visualization.get("voting"), taxonomy)
    project_key = str(st.session_state.get("active_project_id", "project"))
    key_prefix = f"voting_{project_key}"

    voting["enabled"] = st.checkbox(
        "Enable visitor voting",
        value=voting["enabled"],
        key=f"{key_prefix}_enabled",
        help="Adds voting to the selected-stop panel in Preview and the generated public app.",
    )
    if not voting["enabled"]:
        st.info("Enable visitor voting to edit voting settings.")
        visualization["voting"] = voting
        return voting

    with st.expander("Visitor Experience", expanded=True):
        voting["title"] = st.text_input(
            "Heading", value=str(voting["title"]), key=f"{key_prefix}_title"
        )
        voting["description"] = st.text_area(
            "Instructions",
            value=str(voting["description"]),
            key=f"{key_prefix}_description",
            height=100,
        )
        voting["submit_label"] = st.text_input(
            "Submit button label",
            value=str(voting["submit_label"]),
            key=f"{key_prefix}_submit_label",
        )
        voting["success_message"] = st.text_input(
            "Confirmation message",
            value=str(voting["success_message"]),
            key=f"{key_prefix}_success_message",
        )
        voting["allow_vote_changes"] = st.checkbox(
            "Allow a visitor to change an existing vote",
            value=voting["allow_vote_changes"],
            key=f"{key_prefix}_allow_changes",
            help=(
                "With abuse prevention enabled, this follows the same pseudonymous visitor across "
                "browser-session resets when request metadata is available."
            ),
        )
        voting["require_authentication"] = st.checkbox(
            "Require account sign-in",
            value=voting["require_authentication"],
            key=f"{key_prefix}_require_authentication",
            help=(
                "Strongest option. The deployed Streamlit app must have OIDC authentication "
                "configured; each provider account receives one voting identity."
            ),
        )

    with st.expander("Voting Options", expanded=True):
        voting["question"] = st.text_input(
            "Coverage question",
            value=str(voting["question"]),
            key=f"{key_prefix}_question",
        )
        voting["options"] = st.multiselect(
            "Coverage choices",
            PUBLIC_COVERAGE_OPTIONS,
            default=[option for option in voting["options"] if option in PUBLIC_COVERAGE_OPTIONS],
            key=f"{key_prefix}_options",
            help=(
                "Coverage and shade source are separate dimensions. Source labels never appear "
                "in this control."
            ),
        )
        st.caption(
            "Shade sources are recorded separately from coverage and cannot be used as coverage choices."
        )
        if not voting["options"]:
            st.warning("Select at least one coverage choice before enabling voting.")
        voting["source_question"] = st.text_input(
            "Shade source question",
            value=str(voting["source_question"]),
            key=f"{key_prefix}_source_question",
            help="Visitors can select multiple shade sources independently from coverage.",
        )

    with st.expander("Abuse Prevention", expanded=False):
        voting["abuse_protection_enabled"] = st.checkbox(
            "Limit repeated voting",
            value=voting["abuse_protection_enabled"],
            key=f"{key_prefix}_abuse_protection",
            help=(
                "Uses a one-way, server-keyed visitor fingerprint plus server-side timing limits. "
                "Raw IP addresses and browser headers are not stored."
            ),
        )
        if voting["abuse_protection_enabled"]:
            voting["enforce_network_vote_limit"] = st.checkbox(
                "Limit one anonymous vote per network and stop",
                value=voting["enforce_network_vote_limit"],
                key=f"{key_prefix}_network_vote_limit",
                help=(
                    "Prevents browser and user-agent rotation on the same IP network from creating "
                    "extra votes. Authenticated voters are limited by account instead."
                ),
            )
            voting["vote_cooldown_seconds"] = int(
                st.number_input(
                    "Minimum seconds between votes",
                    min_value=0,
                    max_value=300,
                    value=int(voting["vote_cooldown_seconds"]),
                    step=1,
                    key=f"{key_prefix}_vote_cooldown",
                    help="Applied across all stops for the same pseudonymous visitor.",
                )
            )
            if voting["enforce_network_vote_limit"]:
                voting["max_new_votes_per_network_per_hour"] = int(
                    st.number_input(
                        "Maximum new stops per network per hour",
                        min_value=1,
                        max_value=200,
                        value=int(voting["max_new_votes_per_network_per_hour"]),
                        step=1,
                        key=f"{key_prefix}_network_hourly_vote_limit",
                        help="Applied to a keyed network pseudonym; raw IP addresses are not stored.",
                    )
                )
            voting["max_new_votes_per_stop_per_hour"] = int(
                st.number_input(
                    "Maximum new votes per stop per hour",
                    min_value=5,
                    max_value=500,
                    value=int(voting["max_new_votes_per_stop_per_hour"]),
                    step=1,
                    key=f"{key_prefix}_stop_hourly_vote_limit",
                    help="Slows coordinated bursts even when attackers rotate identities and networks.",
                )
            )
            voting["max_new_votes_per_hour"] = int(
                st.number_input(
                    "Maximum new stops per visitor per hour",
                    min_value=1,
                    max_value=100,
                    value=int(voting["max_new_votes_per_hour"]),
                    step=1,
                    key=f"{key_prefix}_hourly_vote_limit",
                    help=(
                        "Changing an existing vote does not consume another new-stop allowance."
                    ),
                )
            )

    with st.expander("Result Display", expanded=False):
        voting["show_results"] = st.checkbox(
            "Show community totals and result",
            value=voting["show_results"],
            key=f"{key_prefix}_show_results",
        )
        if voting["show_results"]:
            voting["show_detailed_counts"] = st.checkbox(
                "Show detailed counts after the threshold",
                value=voting["show_detailed_counts"],
                key=f"{key_prefix}_show_detailed_counts",
                help="Counts remain hidden before the threshold to reduce feedback to manipulators.",
            )
            voting["results_label"] = st.text_input(
                "Result label",
                value=str(voting["results_label"]),
                key=f"{key_prefix}_results_label",
            )
            voting["minimum_votes_for_result"] = int(
                st.number_input(
                    "Votes required before showing a result",
                    min_value=5,
                    max_value=100,
                    value=int(voting["minimum_votes_for_result"]),
                    step=1,
                    key=f"{key_prefix}_minimum_votes",
                    help="No result or vote counts are disclosed before this threshold.",
                )
            )
            voting["minimum_consensus_percent"] = int(
                st.number_input(
                    "Minimum winning share (%)",
                    min_value=51,
                    max_value=100,
                    value=int(voting["minimum_consensus_percent"]),
                    step=1,
                    key=f"{key_prefix}_minimum_consensus_percent",
                )
            )
            voting["minimum_consensus_margin"] = int(
                st.number_input(
                    "Minimum lead in votes",
                    min_value=1,
                    max_value=25,
                    value=int(voting["minimum_consensus_margin"]),
                    step=1,
                    key=f"{key_prefix}_minimum_consensus_margin",
                )
            )

    visualization["voting"] = voting
    return voting


def render_voting_preview(voting: dict[str, Any], taxonomy: list[dict[str, Any]]) -> None:
    st.subheader("Live Preview")
    if not voting.get("enabled", False):
        st.info("Community voting is currently hidden in the deployed app. Enable it to publish this interface.")
    with st.container(border=True):
        render_voting_panel(
            {"stop_id": "preview", "stop_name": "Preview stop"},
            "builder-preview",
            taxonomy,
            voting,
            preview=True,
        )


def render_persistent_storage_guidance() -> None:
    with st.container(border=True):
        st.markdown("#### Persistent voting storage")
        st.caption(
            "Use PostgreSQL in your own provider account for durable hosted voting. "
            "Stop-GIS never owns or centrally stores it."
        )
        if st.button("Open deployment setup", key="open_voting_deployment_setup"):
            st.session_state["page"] = "Deploy"
            st.rerun()


def render_voting_page() -> None:
    st.title("Community Voting")
    st.markdown(
        "Configure how community input contributes to consensus and public result reporting."
    )

    visualization = st.session_state["visualization"]
    taxonomy = st.session_state["taxonomy"]
    source_taxonomy = normalize_source_taxonomy(
        st.session_state.get("methodology", {}).get("shade_source_taxonomy")
    )
    coverage_taxonomy = normalize_coverage_display_taxonomy(
        st.session_state.get("methodology", {}).get("shade_coverage_taxonomy"),
        taxonomy,
    )
    controls, preview = st.columns([0.95, 1.05], gap="large")

    with controls:
        st.subheader("Configuration")
        voting = render_voting_controls(visualization, taxonomy)
        voting["shade_source_taxonomy"] = source_taxonomy
        voting["shade_coverage_taxonomy"] = coverage_taxonomy
        if voting["enabled"]:
            render_persistent_storage_guidance()
    with preview:
        render_voting_preview(voting, taxonomy)
