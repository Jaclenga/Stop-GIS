from builder_app import *
from public_voting import (
    PUBLIC_COVERAGE_OPTIONS,
    PUBLIC_SOURCE_OPTIONS,
    coverage_display_labels,
    coverage_taxonomy_help,
    normalize_voting_config,
    source_taxonomy_help,
    source_display_labels,
)


def render_voting_controls(
    visualization: dict[str, Any],
    taxonomy: list[dict[str, Any]],
) -> dict[str, Any]:
    voting = normalize_voting_config(visualization.get("voting"), taxonomy)
    project_key = str(st.session_state.get("active_project_id", "project"))
    key_prefix = f"voting_{project_key}"

    with st.expander("Visitor Experience", expanded=True):
        voting["enabled"] = st.checkbox(
            "Enable visitor voting",
            value=voting["enabled"],
            key=f"{key_prefix}_enabled",
            help="Adds voting to the selected-stop panel in Preview and the generated public app.",
        )
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
            voting["vote_cooldown_seconds"] = int(
                st.number_input(
                    "Minimum seconds between votes",
                    min_value=0,
                    max_value=60,
                    value=int(voting["vote_cooldown_seconds"]),
                    step=1,
                    key=f"{key_prefix}_vote_cooldown",
                    help="Applied across all stops for the same pseudonymous visitor.",
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
            voting["results_label"] = st.text_input(
                "Result label",
                value=str(voting["results_label"]),
                key=f"{key_prefix}_results_label",
            )
            voting["minimum_votes_for_result"] = int(
                st.number_input(
                    "Votes required before showing a result",
                    min_value=1,
                    max_value=100,
                    value=int(voting["minimum_votes_for_result"]),
                    step=1,
                    key=f"{key_prefix}_minimum_votes",
                    help="A unique leading choice is reported after this many votes; ties stay tied.",
                )
            )

    visualization["voting"] = voting
    return voting


def render_voting_preview(
    voting: dict[str, Any],
    taxonomy: list[dict[str, Any]] | None = None,
) -> None:
    st.subheader("Live Preview")
    if not voting.get("enabled", False):
        st.info("Voting is currently hidden in the deployed app. Enable it to publish this interface.")
    with st.container(border=True):
        st.markdown(f"#### {voting['title']}")
        if voting.get("description"):
            st.markdown(str(voting["description"]))
        options = voting.get("options", [])
        if options:
            st.markdown(
                f"**{voting['question']}**",
                help=coverage_taxonomy_help(options, taxonomy),
            )
            st.radio(
                str(voting["question"]),
                options,
                disabled=True,
                key="voting_interface_preview_choice",
                label_visibility="collapsed",
                format_func=lambda option: coverage_display_labels(
                    voting.get("shade_coverage_taxonomy")
                ).get(option, option),
            )
            st.divider()
            st.markdown(
                f"**{voting['source_question']}**",
                help=source_taxonomy_help(voting.get("shade_source_taxonomy")),
            )
            for source in PUBLIC_SOURCE_OPTIONS:
                st.checkbox(
                    source_display_labels(voting.get("shade_source_taxonomy")).get(source, source),
                    disabled=True,
                    key=f"voting_interface_preview_source_{source.lower()}",
                )
            st.button(
                str(voting["submit_label"]),
                disabled=True,
                key="voting_interface_preview_submit",
                width="stretch",
            )
        else:
            st.warning("Choose at least one coverage option to complete the interface.")
        if voting.get("show_results", True):
            st.markdown(f"**{voting['results_label']}: More votes needed**")
            st.caption("Vote totals appear here in the deployed app.")


def render_advanced_deployment_guidance() -> None:
    with st.expander("Advanced Deployment", expanded=False):
        st.caption("Developer settings for durable hosted voting.")
        st.markdown(
            "Generated apps use a local SQLite vote database for development. For durable hosted "
            "voting, set `SHADE_GIS_VOTE_DATABASE_URL` as a PostgreSQL secret in the deployment "
            "environment. Streamlit Community Cloud local files are ephemeral and may be lost when "
            "the app restarts. Robustness controls store only a server-keyed pseudonym, never raw IP "
            "addresses or browser headers. Optionally set `SHADE_GIS_VOTE_FINGERPRINT_SECRET` to a "
            "stable random secret; otherwise the voting database creates and retains one automatically."
        )


def render_voting_page() -> None:
    st.title("Community Voting")
    st.markdown(
        "Configure community voting and public result reporting for the deployed app."
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
        render_advanced_deployment_guidance()
    with preview:
        render_voting_preview(voting, taxonomy)
