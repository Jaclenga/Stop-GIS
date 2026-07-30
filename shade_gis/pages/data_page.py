from builder_app import *
from shade_gis.data_quality import DATA_QUALITY_ISSUES, ISSUE_BY_KEY, evaluate_data_quality
from shade_gis.shade_dimensions import (
    normalize_shade_coverage,
    normalize_source_taxonomy,
    normalize_terminology,
)


DATASET_REVIEWED_STATUSES = {"Crowd Reviewed", "Expert Reviewed", "Accepted", "Archived"}
DATASET_ATTENTION_STATUSES = {"Needs Review", "Disputed"}
DATASET_STATUS_OPTIONS = ["Needs Review", "Reviewed", "Unlabeled"]
DATASET_QUEUE_PAGE_SIZES = [10, 25, 50]
DATASET_PREVIEW_PAGE_SIZES = [25, 50, 100]


def manual_entry_dataframe(records: list[dict[str, str]]) -> pd.DataFrame:
    """Build a consistently typed frame after manual form submission."""
    normalized_records = [
        {column: str(record.get(column, "") or "").strip() for column in MANUAL_ENTRY_COLUMNS}
        for record in records
    ]
    return pd.DataFrame(
        normalized_records,
        columns=MANUAL_ENTRY_COLUMNS,
        dtype=object,
    )


def dataframe_html(frame: pd.DataFrame, column_labels: dict[str, str] | None = None) -> str:
    """Build an escaped HTML table without Streamlit's PyArrow dataframe path."""
    if frame.columns.duplicated().any():
        duplicates = [str(column) for column in frame.columns[frame.columns.duplicated()].tolist()]
        raise ValueError(f"Display dataframe contains duplicate columns: {duplicates}")
    display = frame.rename(columns=column_labels or {})
    return display.to_html(
        index=False,
        escape=True,
        border=0,
        classes="data-page-table",
        na_rep="",
    )


def render_dataframe_table(frame: pd.DataFrame, column_labels: dict[str, str] | None = None) -> None:
    if frame.empty:
        st.caption("No rows to display.")
        return
    st.markdown(
        '<div style="max-width:100%;overflow-x:auto">'
        f"{dataframe_html(frame, column_labels)}"
        "</div>",
        unsafe_allow_html=True,
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
    methodology: dict[str, Any],
    taxonomy: list[dict[str, Any]],
) -> None:
    default_definitions = {
        item["shade_coverage"]: item["operational_definition"]
        for item in SHADE_COVERAGE_TAXONOMY
    }
    coverage_taxonomy = normalize_coverage_display_taxonomy(
        methodology.get("shade_coverage_taxonomy"),
        taxonomy,
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
            [0.68, 0.20, 0.12],
            vertical_alignment="center",
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
    methodology: dict[str, Any],
    taxonomy: list[dict[str, Any]],
) -> pd.DataFrame:
    return pd.DataFrame(
        normalize_coverage_display_taxonomy(
            methodology.get("shade_coverage_taxonomy"),
            taxonomy,
        ),
        columns=["code", "shade_coverage", "operational_definition"],
    )


def render_terminology_editor(methodology: dict[str, Any]) -> list[dict[str, str]]:
    frame = terminology_table_frame(methodology)
    edited = st.data_editor(
        frame,
        column_config={
            "term": st.column_config.TextColumn("Term", required=True, width="small"),
            "operational_definition": st.column_config.TextColumn(
                "Operational definition",
                width="large",
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


def render_shade_source_taxonomy_editor(methodology: dict[str, Any]) -> list[dict[str, str]]:
    frame = source_taxonomy_table_frame(methodology)
    edited = st.data_editor(
        frame,
        column_config={
            "shade_source": st.column_config.TextColumn("Shade source", width="small"),
            "operational_definition": st.column_config.TextColumn(
                "Operational definition",
                required=True,
                width="large",
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
    methodology: dict[str, Any],
    taxonomy: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized_taxonomy = normalize_coverage_taxonomy(taxonomy)
    frame = coverage_taxonomy_table_frame(methodology, normalized_taxonomy)
    edited = st.data_editor(
        frame,
        column_config={
            "shade_coverage": st.column_config.TextColumn("Shade coverage", width="small"),
            "operational_definition": st.column_config.TextColumn(
                "Operational definition",
                required=True,
                width="large",
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
        edited.to_dict(orient="records"),
        normalized_taxonomy,
    )
    methodology["shade_coverage_taxonomy"] = display_taxonomy
    definitions = {
        item["code"]: item["operational_definition"]
        for item in display_taxonomy
    }
    for item in normalized_taxonomy:
        if item["name"] in definitions and definitions[item["name"]]:
            item["description"] = definitions[item["name"]]
    taxonomy[:] = normalize_coverage_taxonomy(normalized_taxonomy)
    return taxonomy


def dataset_status_table(
    stops: pd.DataFrame,
    labels: pd.DataFrame,
    review_history: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Combine canonical stop state and raw-label history into progress rows."""
    if stops.empty or "stop_id" not in stops.columns:
        return pd.DataFrame(
            columns=["stop_id", "dataset_status", "label_count", "final_label", "agreement_pct"]
        )
    status = stops.copy()
    status["stop_id"] = status["stop_id"].fillna("").astype(str).str.strip()
    majority = majority_label_table(labels)
    if not majority.empty:
        majority = majority.copy()
        majority["stop_id"] = majority["stop_id"].astype(str)
        computed_columns = [column for column in majority.columns if column != "stop_id" and column in status.columns]
        status = status.drop(columns=computed_columns)
        status = status.merge(majority, on="stop_id", how="left")
    for column, fallback in [
        ("label_count", 0),
        ("agreement_pct", None),
        ("disagreement_flag", False),
        ("review_status", "Unlabeled"),
        ("shading", ""),
        ("shade_coverage", ""),
    ]:
        if column not in status.columns:
            status[column] = fallback
        status[column] = status[column].fillna(fallback) if fallback is not None else status[column]
    status["label_count"] = pd.to_numeric(status["label_count"], errors="coerce").fillna(0).astype(int)
    status["review_status"] = status["review_status"].fillna("Unlabeled").astype(str)

    def final_label(row: pd.Series) -> str:
        coverage = normalize_shade_coverage(row.get("shade_coverage", ""), "")
        if not coverage:
            coverage = normalize_shade_coverage(row.get("shading", ""), "")
        return coverage if coverage and coverage != "Needs Review" else "Not set"

    status["final_label"] = status.apply(final_label, axis=1)
    unresolved = disagreement_queue_table(stops, labels, review_history)
    unresolved_ids = set(unresolved["stop_id"].astype(str)) if not unresolved.empty else set()
    status["unresolved_disagreement"] = status["stop_id"].isin(unresolved_ids)

    def work_status(row: pd.Series) -> str:
        review_status = str(row.get("review_status", "") or "")
        if bool(row.get("unresolved_disagreement", False)):
            return "Needs Review"
        if review_status in DATASET_REVIEWED_STATUSES:
            return "Reviewed"
        if review_status in DATASET_ATTENTION_STATUSES or bool(row.get("disagreement_flag", False)):
            return "Needs Review"
        if int(row.get("label_count", 0) or 0) == 0 and row.get("final_label") == "Not set":
            return "Unlabeled"
        return "Needs Review"

    status["dataset_status"] = status.apply(work_status, axis=1)
    status["is_labeled"] = (status["label_count"] > 0) | status["final_label"].ne("Not set")
    return status


def dataset_status_metrics(status: pd.DataFrame) -> dict[str, int | float]:
    total = len(status)
    labeled_mask = status.get("is_labeled", pd.Series(False, index=status.index)).fillna(False).astype(bool)
    labeled = int(labeled_mask.sum())
    reviewed = int((status.get("dataset_status", pd.Series(dtype=str)).eq("Reviewed") & labeled_mask).sum())
    needs_review = int(status.get("dataset_status", pd.Series(dtype=str)).eq("Needs Review").sum())
    unlabeled = int(status.get("dataset_status", pd.Series(dtype=str)).eq("Unlabeled").sum())
    return {
        "total_stops": total,
        "labeled_stops": labeled,
        "reviewed_stops": reviewed,
        "stops_needing_review": needs_review,
        "unlabeled_stops": unlabeled,
        "label_coverage": labeled / total if total else 0.0,
        "review_completion": reviewed / labeled if labeled else 0.0,
    }


def filter_dataset_work_queue(
    status: pd.DataFrame,
    selected_statuses: list[str],
    stop_search: str = "",
) -> pd.DataFrame:
    filtered = status.copy()
    if selected_statuses:
        filtered = filtered[filtered["dataset_status"].isin(selected_statuses)]
    if stop_search.strip():
        query = stop_search.strip().lower()
        filtered = filtered[filtered["stop_id"].astype(str).str.lower().str.contains(query, regex=False)]
    rank = {"Needs Review": 0, "Unlabeled": 1, "Reviewed": 2}
    filtered["status_rank"] = filtered["dataset_status"].map(rank).fillna(3)
    filtered["agreement_sort"] = pd.to_numeric(filtered["agreement_pct"], errors="coerce").fillna(101)
    return filtered.sort_values(["status_rank", "agreement_sort", "stop_id"])


def dataset_work_queue_display(status: pd.DataFrame) -> pd.DataFrame:
    records = []
    for _, row in status.iterrows():
        agreement = pd.to_numeric(pd.Series([row.get("agreement_pct")]), errors="coerce").iloc[0]
        records.append(
            {
                "Stop ID": str(row.get("stop_id", "")),
                "Status": str(row.get("dataset_status", "")),
                "Labels": int(row.get("label_count", 0) or 0),
                "Final Label": str(row.get("final_label", "Not set") or "Not set"),
                "Agreement": f"{float(agreement):.1f}%" if pd.notna(agreement) else "—",
            }
        )
    return pd.DataFrame.from_records(
        records,
        columns=["Stop ID", "Status", "Labels", "Final Label", "Agreement"],
    )


def dataset_preview_page(
    stops: pd.DataFrame,
    page: int,
    page_size: int,
) -> tuple[pd.DataFrame, int, int]:
    """Return only the requested preview slice so the UI never mounts all rows."""
    page_size = max(int(page_size), 1)
    page_count = max(1, math.ceil(len(stops) / page_size))
    safe_page = min(max(int(page), 1), page_count)
    start = (safe_page - 1) * page_size
    return stops.iloc[start : start + page_size].copy(), safe_page, page_count


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
    visible, page, page_count = dataset_preview_page(affected, int(requested_page), int(page_size))
    paging[2].caption(
        f"{len(affected):,} {display_label} | Page {page:,} of {page_count:,}"
    )
    render_dataframe_table(visible)


def render_dataset_status(
    stops: pd.DataFrame,
    labels: pd.DataFrame,
    review_history: pd.DataFrame | None = None,
) -> None:
    st.subheader("Dataset Status")
    st.caption("Project progress, remaining work, and stops that need attention.")
    status = dataset_status_table(stops, labels, review_history)
    metrics = dataset_status_metrics(status)

    cards = st.columns(4)
    cards[0].metric("Total stops", f"{metrics['total_stops']:,}")
    cards[1].metric("Labeled stops", f"{metrics['labeled_stops']:,}")
    cards[2].metric("Reviewed stops", f"{metrics['reviewed_stops']:,}")
    cards[3].metric("Needs review", f"{metrics['stops_needing_review']:,}")

    progress = st.columns(2)
    with progress[0]:
        st.markdown("**Label coverage**")
        st.progress(float(metrics["label_coverage"]))
        st.caption(
            f"{metrics['labeled_stops']:,} of {metrics['total_stops']:,} stops have a raw or final label "
            f"({float(metrics['label_coverage']) * 100:.1f}%)."
        )
    with progress[1]:
        st.markdown("**Review completion**")
        st.progress(float(metrics["review_completion"]))
        st.caption(
            f"{metrics['reviewed_stops']:,} of {metrics['labeled_stops']:,} labeled stops are reviewed "
            f"({float(metrics['review_completion']) * 100:.1f}%)."
        )

    attention_count = int(metrics["stops_needing_review"]) + int(metrics["unlabeled_stops"])
    queue_label = (
        f"Work queue · {attention_count:,} stops need attention"
        if attention_count
        else "Work queue · All caught up"
    )
    with st.expander(queue_label, expanded=False):
        st.markdown("#### Work Queue")
        st.caption(
            "Choose the stops you want to work on. Stops that need review appear first; "
            "open the labeling workspace when you are ready to resolve them."
        )
        filters = st.columns([2, 1.2])
        selected_statuses = filters[0].multiselect(
            "Show stops",
            DATASET_STATUS_OPTIONS,
            default=["Needs Review", "Unlabeled"],
            key="dataset_queue_statuses",
            help="Needs Review has submitted information to check. Unlabeled still needs a first label.",
        )
        stop_search = filters[1].text_input(
            "Find a stop",
            key="dataset_queue_stop_search",
            placeholder="Enter a stop ID",
        )
        filtered = filter_dataset_work_queue(status, selected_statuses, stop_search)
        if filtered.empty:
            st.info("No stops match these choices. Try showing another group or clearing the search.")
        else:
            paging = st.columns([1, 1, 3], vertical_alignment="bottom")
            page_size = paging[0].selectbox(
                "Stops per page",
                DATASET_QUEUE_PAGE_SIZES,
                index=1,
                key="dataset_queue_page_size",
            )
            page_count = max(1, math.ceil(len(filtered) / int(page_size)))
            current_page = st.session_state.get("dataset_queue_page", 1)
            if not isinstance(current_page, int) or current_page < 1 or current_page > page_count:
                st.session_state["dataset_queue_page"] = min(max(int(current_page or 1), 1), page_count)
            requested_page = paging[1].number_input(
                "Page",
                min_value=1,
                max_value=page_count,
                step=1,
                key="dataset_queue_page",
            )
            start = (int(requested_page) - 1) * int(page_size)
            visible = filtered.iloc[start : start + int(page_size)]
            paging[2].caption(
                f"Showing {start + 1:,}–{start + len(visible):,} of {len(filtered):,} matching stops"
            )
            render_dataframe_table(dataset_work_queue_display(visible))
            st.button(
                "Open labeling workspace →",
                type="primary",
                key="dataset_queue_open_labels",
                on_click=set_page,
                args=("Labels",),
            )

    with st.expander("Dataset Preview", expanded=False):
        st.caption("Browse the project dataset one page at a time. Only the visible page is rendered.")
        preview_controls = st.columns([1, 1, 3], vertical_alignment="bottom")
        preview_page_size = preview_controls[0].selectbox(
            "Preview rows per page",
            DATASET_PREVIEW_PAGE_SIZES,
            index=1,
            key="dataset_preview_page_size",
        )
        preview_page_count = max(1, math.ceil(len(stops) / int(preview_page_size)))
        current_preview_page = st.session_state.get("dataset_preview_page", 1)
        if (
            not isinstance(current_preview_page, int)
            or current_preview_page < 1
            or current_preview_page > preview_page_count
        ):
            st.session_state["dataset_preview_page"] = min(
                max(int(current_preview_page or 1), 1),
                preview_page_count,
            )
        requested_preview_page = preview_controls[1].number_input(
            "Preview page",
            min_value=1,
            max_value=preview_page_count,
            step=1,
            key="dataset_preview_page",
        )
        visible_stops, preview_page, preview_page_count = dataset_preview_page(
            stops,
            int(requested_preview_page),
            int(preview_page_size),
        )
        first_row = (preview_page - 1) * int(preview_page_size) + 1 if len(stops) else 0
        last_row = min(preview_page * int(preview_page_size), len(stops))
        preview_controls[2].caption(
            f"Showing rows {first_row:,}–{last_row:,} of {len(stops):,} · "
            f"Page {preview_page:,} of {preview_page_count:,}"
        )
        render_dataframe_table(visible_stops)

def render_project_storage_controls() -> None:
    projects = list_projects()
    project_ids = [project["id"] for project in projects]
    active_project_id = st.session_state.get("active_project_id")
    if active_project_id not in project_ids and project_ids:
        active_project_id = project_ids[0]

    def project_label(project_id: str) -> str:
        project = next((item for item in projects if item["id"] == project_id), {})
        name = project.get("name") or "Untitled Shade Study"
        region = project.get("region") or "No region"
        version = project.get("dataset_version") or "draft"
        return f"{name} - {region} - v{version}"

    st.subheader("Project Store")
    cols = st.columns([1.5, 0.55, 0.95], vertical_alignment="bottom")
    with cols[0]:
        if project_ids:
            selected_project_id = st.selectbox(
                "Active saved project",
                project_ids,
                index=project_ids.index(active_project_id),
                format_func=project_label,
            )
            if selected_project_id != active_project_id:
                save_active_project_to_store()
                load_project_into_session(selected_project_id)
                st.rerun()
    with cols[1]:
        if st.button("Save now", width="stretch"):
            save_active_project_to_store()
            st.success("Project saved.")
    with cols[2]:
        status = database_status()
        if status["using_fallback"]:
            st.caption(f"Database fallback: `{status['active_path']}`")
        else:
            st.caption(f"Database: `{status['active_path']}`")

    create_cols = st.columns([1.5, 0.65], vertical_alignment="bottom")
    with create_cols[0]:
        new_project_name = st.text_input("New blank project name", key="new_project_name")
    with create_cols[1]:
        if st.button("Create blank project", width="stretch"):
            new_project_id = create_blank_project(new_project_name)
            load_project_into_session(new_project_id)
            st.rerun()


def render_data_page() -> None:
    st.title("Project Data")
    render_project_storage_controls()
    project = st.session_state["project"]
    taxonomy = st.session_state["taxonomy"]
    methodology = st.session_state["methodology"]

    left, right = st.columns([1, 1])
    with left:
        st.subheader("Project")
        project["name"] = st.text_input("Project name", project["name"])
        project["agency"] = st.text_input("Transit agency", project["agency"])
        project["region"] = st.text_input("Geographic region", project["region"])
        project["owners"] = st.text_input("Owner(s)", project["owners"])
    with right:
        st.subheader("Publication")
        project["visibility"] = st.selectbox("Visibility", ["Public", "Private"], index=0 if project["visibility"] == "Public" else 1)
        project["dataset_version"] = st.text_input("Dataset version", project["dataset_version"])
        project["methodology_version"] = st.text_input("Methodology version", project["methodology_version"])
        project["description"] = st.text_area("Description", project["description"], height=118)

    st.subheader("Upload Or Map A Dataset")
    file_tab, api_tab, manual_tab = st.tabs(["File Upload", "API URL", "Manual Entry"])
    with file_tab:
        uploaded = st.file_uploader(
            "Upload GTFS, CSV, GeoJSON, or a zipped Shapefile",
            type=["zip", "txt", "csv", "geojson", "json"],
        )
        st.caption(
            f"Upload limit: {format_bytes(max_upload_bytes())}; ZIPs may contain up to "
            f"{max_zip_members()} files and expand to {format_bytes(max_zip_uncompressed_bytes())}."
        )
        if uploaded is not None:
            if getattr(uploaded, "size", 0) > max_upload_bytes():
                st.error(f"This upload is larger than the {format_bytes(max_upload_bytes())} limit.")
                return
            contents = uploaded.getvalue()
            filename = uploaded.name
            key_prefix = f"file_{clean_import_key(filename)}"
            try:
                if filename.lower().endswith(".zip"):
                    zip_format = detect_zip_import_format(contents)
                    if zip_format == "GTFS":
                        raw, metadata = parse_gtfs_zip(contents)
                        render_dataframe_table(raw.head(25))
                        if st.button("Use uploaded GTFS stops", type="primary", key=f"{key_prefix}_gtfs"):
                            mapping = {field: field for field in REQUIRED_STOP_FIELDS + OPTIONAL_FIELDS if field in raw.columns}
                            metadata.update({"original_filename": filename})
                            prepared = import_stop_dataset(
                                raw,
                                mapping,
                                project=project,
                                taxonomy=taxonomy,
                                source_name=filename,
                                import_format="GTFS",
                                metadata=metadata,
                            )
                            st.success(f"Imported {len(prepared):,} mapped stops.")
                    else:
                        raw, metadata = parse_shapefile_zip(contents)
                        metadata.update({"original_filename": filename})
                        render_mapped_import_controls(
                            raw,
                            source_name=filename,
                            import_format="Shapefile",
                            project=project,
                            taxonomy=taxonomy,
                            metadata=metadata,
                            key_prefix=key_prefix,
                            button_label="Use mapped Shapefile",
                        )
                elif filename.lower().endswith((".geojson", ".json")):
                    raw, metadata = parse_geojson_bytes(contents)
                    metadata.update({"original_filename": filename})
                    render_mapped_import_controls(
                        raw,
                        source_name=filename,
                        import_format="GeoJSON",
                        project=project,
                        taxonomy=taxonomy,
                        metadata=metadata,
                        key_prefix=key_prefix,
                        button_label="Use mapped GeoJSON",
                    )
                else:
                    raw = read_csv_bytes(contents)
                    render_mapped_import_controls(
                        raw,
                        source_name=filename,
                        import_format="CSV",
                        project=project,
                        taxonomy=taxonomy,
                        metadata={"original_filename": filename},
                        key_prefix=key_prefix,
                        button_label="Use mapped CSV",
                    )
            except Exception as error:
                st.error(f"Could not import this file: {error}")

    with api_tab:
        api_url = st.text_input("Dataset API or file URL", key="api_import_url")
        api_format = st.selectbox("Response format", ["Auto detect", "CSV", "GeoJSON"], key="api_import_format")
        st.caption(
            f"API imports accept HTTP(S) CSV or GeoJSON responses up to {format_bytes(max_api_bytes())}. "
            "Private network URLs are blocked unless enabled by deployment settings."
        )
        if st.button("Fetch API dataset", key="fetch_api_dataset"):
            if not api_url.strip():
                st.warning("Enter a URL before fetching.")
            else:
                try:
                    contents = fetch_api_bytes(api_url)
                    requested = "Auto" if api_format == "Auto detect" else api_format
                    raw, metadata = parse_api_response(contents, api_url, requested)
                    st.session_state["api_import_raw"] = raw
                    st.session_state["api_import_metadata"] = metadata
                    st.session_state["api_import_source"] = api_url
                    st.success(f"Fetched {len(raw):,} records.")
                except Exception as error:
                    st.error(f"Could not fetch this API dataset: {error}")
        api_raw = st.session_state.get("api_import_raw")
        if isinstance(api_raw, pd.DataFrame):
            api_metadata = st.session_state.get("api_import_metadata", {})
            detected = api_metadata.get("detected_format") or api_format.replace("Auto detect", "API")
            render_mapped_import_controls(
                api_raw,
                source_name=st.session_state.get("api_import_source", api_url),
                import_format=str(detected),
                project=project,
                taxonomy=taxonomy,
                metadata=api_metadata,
                key_prefix="api_import",
                button_label="Use mapped API dataset",
            )

    with manual_tab:
        st.caption(
            "Add stops to the queue with text fields. Rows without a stop ID or valid coordinates "
            "are ignored on import."
        )
        with st.form("manual_entry_form", clear_on_submit=True):
            field_columns = st.columns(2)
            manual_values: dict[str, str] = {}
            for index, column in enumerate(MANUAL_ENTRY_COLUMNS):
                label = column.replace("_", " ").title().replace("Id", "ID")
                manual_values[column] = field_columns[index % 2].text_input(
                    label,
                    key=f"manual_entry_{column}",
                )
            add_manual_entry = st.form_submit_button("Add entry")

        if add_manual_entry:
            record = {column: str(manual_values.get(column, "")).strip() for column in MANUAL_ENTRY_COLUMNS}
            if any(record.values()):
                queued_entries = list(st.session_state.get("manual_import_entries", []))
                queued_entries.append(record)
                st.session_state["manual_import_entries"] = queued_entries
                st.success(f"Added entry {len(queued_entries)} to the manual import queue.")
            else:
                st.warning("Enter at least one value before adding an entry.")

        queued_entries = list(st.session_state.get("manual_import_entries", []))
        if queued_entries:
            st.markdown("**Queued manual entries**")
            for index, record in enumerate(queued_entries):
                entry_column, remove_column = st.columns([5, 1])
                stop_id = record.get("stop_id") or "No stop ID"
                stop_name = record.get("stop_name") or "Unnamed stop"
                entry_column.caption(f"{index + 1}. {stop_id} — {stop_name}")
                if remove_column.button("Remove", key=f"remove_manual_entry_{index}"):
                    queued_entries.pop(index)
                    st.session_state["manual_import_entries"] = queued_entries
                    st.rerun()

        manual_source = st.text_input("Manual import source label", "Manual entry", key="manual_import_source")
        if st.button(
            "Use manual entries",
            type="primary",
            key="use_manual_entries",
            disabled=not queued_entries,
        ):
            manual_rows = manual_entry_dataframe(queued_entries)
            mapping = {field: field for field in REQUIRED_STOP_FIELDS + OPTIONAL_FIELDS if field in manual_rows.columns}
            prepared = import_stop_dataset(
                manual_rows,
                mapping,
                project=project,
                taxonomy=taxonomy,
                source_name=manual_source,
                import_format="Manual",
                metadata={"entry_method": "manual"},
            )
            st.session_state["manual_import_entries"] = []
            st.success(f"Imported {len(prepared):,} manually entered stops.")

    source_cols = st.columns(3)
    with source_cols[0]:
        project["source_name"] = st.text_input("Data source name", project["source_name"])
    with source_cols[1]:
        project["source_license"] = st.text_input("Source license", project["source_license"])
    with source_cols[2]:
        project["source_url"] = st.text_input("Source URL", project["source_url"])

    project_id = st.session_state.get("active_project_id")

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
    labels = list_shade_labels(project_id) if project_id else pd.DataFrame()
    review_history = list_review_history(project_id) if project_id else pd.DataFrame()
    render_dataset_status(st.session_state["stops"], labels, review_history)




