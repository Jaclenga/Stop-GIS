"""Reusable publication-readiness and data-quality UI."""

from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from shade_gis.data_quality import DATA_QUALITY_ISSUES, ISSUE_BY_KEY, evaluate_data_quality
from shade_gis.ui_tables import (
    DATASET_PREVIEW_PAGE_SIZES,
    dataset_preview_page,
    render_dataframe_table,
)


def render_data_quality_dashboard(
    stops: pd.DataFrame,
    images: pd.DataFrame,
    *,
    show_heading: bool = True,
) -> None:
    """Render the unified validation and publication-readiness workflow."""
    report = evaluate_data_quality(stops, images)
    if show_heading:
        st.subheader("Data Quality")
    st.caption(
        "Resolve publication-blocking stop and image issues here before previewing or deploying the study."
    )

    if report.publication_ready:
        st.success(f"Publication-ready: all {len(stops):,} stops passed the required data-quality checks.")
    elif not len(stops):
        st.warning("Not publication-ready: import at least one stop, then run the checks below.")
    else:
        st.error(
            f"Not publication-ready: {report.total_issues:,} affected record occurrence(s) "
            "must be resolved."
        )

    render_dataframe_table(report.summary_table())
    st.markdown("#### Validation checks")
    for issue in DATA_QUALITY_ISSUES:
        count = report.count(issue.key)
        check = st.columns([2.7, 0.7, 1.25], vertical_alignment="center")
        check[0].markdown(f"**{issue.label}**  \n{issue.description}")
        check[1].metric("Affected", f"{count:,}")
        if check[2].button(
            "View affected records",
            key=f"data_quality_view_{issue.key}",
            disabled=count == 0,
            width="stretch",
        ):
            st.session_state["data_quality_issue_filter"] = issue.key

    st.markdown('<div id="data-quality-affected-records"></div>', unsafe_allow_html=True)
    st.markdown("#### Affected records")
    issue_options = ["all", *[issue.key for issue in DATA_QUALITY_ISSUES]]
    current_filter = st.session_state.get("data_quality_issue_filter", "all")
    if current_filter not in issue_options:
        st.session_state["data_quality_issue_filter"] = "all"
    selected_issue = st.selectbox(
        "Filter dataset by validation issue",
        issue_options,
        format_func=lambda key: "All validation issues" if key == "all" else ISSUE_BY_KEY[key].label,
        key="data_quality_issue_filter",
    )

    if selected_issue == "all":
        affected = report.issue_records()
        display_label = "issue occurrences"
    else:
        affected = report.affected_records(selected_issue)
        display_label = ISSUE_BY_KEY[selected_issue].label.lower()
    if affected.empty:
        st.info("No affected records match this validation filter.")
        return

    paging = st.columns([1, 1, 3], vertical_alignment="bottom")
    page_size = paging[0].selectbox(
        "Quality rows per page",
        DATASET_PREVIEW_PAGE_SIZES,
        index=0,
        key="data_quality_page_size",
    )
    page_count = max(1, math.ceil(len(affected) / int(page_size)))
    current_page = st.session_state.get("data_quality_page", 1)
    if not isinstance(current_page, int) or current_page < 1 or current_page > page_count:
        st.session_state["data_quality_page"] = min(max(int(current_page or 1), 1), page_count)
    requested_page = paging[1].number_input(
        "Quality page",
        min_value=1,
        max_value=page_count,
        step=1,
        key="data_quality_page",
    )
    visible, page, page_count = dataset_preview_page(
        affected, int(requested_page), int(page_size)
    )
    paging[2].caption(
        f"{len(affected):,} {display_label} | Page {page:,} of {page_count:,}"
    )
    render_dataframe_table(visible)
