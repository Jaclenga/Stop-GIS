import math

import pandas as pd
import streamlit as st

from stop_gis.builder.app import (
    create_blank_project,
    load_project_into_session,
    save_active_project_to_store,
    set_page,
)
from stop_gis.persistence.store import (
    DuplicateProjectNameError,
    database_status,
    list_projects,
    list_review_history,
    list_shade_labels,
)
from stop_gis.builder.imports import (
    OPTIONAL_FIELDS,
    REQUIRED_STOP_FIELDS,
    clean_import_key,
    detect_zip_import_format,
    fetch_api_bytes,
    format_bytes,
    import_stop_dataset,
    max_api_bytes,
    max_upload_bytes,
    max_zip_members,
    max_zip_uncompressed_bytes,
    parse_api_response,
    parse_geojson_bytes,
    parse_gtfs_zip,
    parse_shapefile_zip,
    public_source_url,
    read_csv_bytes,
    render_mapped_import_controls,
)
from stop_gis.builder.labels import disagreement_queue_table, majority_label_table
from stop_gis.domain.shade_dimensions import normalize_shade_coverage
from stop_gis.ui.tables import (
    DATASET_PREVIEW_PAGE_SIZES,
    dataset_preview_page,
    render_dataframe_table,
)


DATASET_REVIEWED_STATUSES = {
    "Crowd Reviewed",
    "Expert Reviewed",
    "Accepted",
    "Archived",
}
DATASET_ATTENTION_STATUSES = {"Needs Review", "Disputed"}
MANUAL_ENTRY_COLUMNS = REQUIRED_STOP_FIELDS + [
    "agency",
    "routes",
    "municipality",
    "shading",
    "review_status",
    "confidence",
]


def manual_entry_dataframe(records: list[dict[str, str]]) -> pd.DataFrame:
    """Build a consistently typed frame after manual form submission."""
    normalized_records = [
        {
            column: str(record.get(column, "") or "").strip()
            for column in MANUAL_ENTRY_COLUMNS
        }
        for record in records
    ]
    return pd.DataFrame(
        normalized_records,
        columns=MANUAL_ENTRY_COLUMNS,
        dtype=object,
    )


def dataset_status_table(
    stops: pd.DataFrame,
    labels: pd.DataFrame,
    review_history: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Combine canonical stop state and raw-label history into progress rows."""
    if stops.empty or "stop_id" not in stops.columns:
        return pd.DataFrame(
            columns=[
                "stop_id",
                "dataset_status",
                "label_count",
                "final_label",
                "agreement_pct",
            ]
        )
    status = stops.copy()
    status["stop_id"] = status["stop_id"].fillna("").astype(str).str.strip()
    majority = majority_label_table(labels)
    if not majority.empty:
        majority = majority.copy()
        majority["stop_id"] = majority["stop_id"].astype(str)
        computed_columns = [
            column
            for column in majority.columns
            if column != "stop_id" and column in status.columns
        ]
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
        status[column] = (
            status[column].fillna(fallback) if fallback is not None else status[column]
        )
    status["label_count"] = (
        pd.to_numeric(status["label_count"], errors="coerce").fillna(0).astype(int)
    )
    status["review_status"] = status["review_status"].fillna("Unlabeled").astype(str)

    def final_label(row: pd.Series) -> str:
        coverage = normalize_shade_coverage(row.get("shade_coverage", ""), "")
        if not coverage:
            coverage = normalize_shade_coverage(row.get("shading", ""), "")
        return coverage if coverage and coverage != "Needs Review" else "Not set"

    status["final_label"] = status.apply(final_label, axis=1)
    unresolved = disagreement_queue_table(stops, labels, review_history)
    unresolved_ids = (
        set(unresolved["stop_id"].astype(str)) if not unresolved.empty else set()
    )
    status["unresolved_disagreement"] = status["stop_id"].isin(unresolved_ids)
    if "assessment_values" in status.columns:
        status["_has_assessment_values"] = status["assessment_values"].map(
            lambda value: (
                bool(value)
                if isinstance(value, dict)
                else str(value or "").strip() not in {"", "{}", "nan"}
            )
        )
    else:
        status["_has_assessment_values"] = False

    def work_status(row: pd.Series) -> str:
        review_status = str(row.get("review_status", "") or "")
        if bool(row.get("unresolved_disagreement", False)):
            return "Needs Review"
        if review_status in DATASET_REVIEWED_STATUSES:
            return "Reviewed"
        if review_status in DATASET_ATTENTION_STATUSES or bool(
            row.get("disagreement_flag", False)
        ):
            return "Needs Review"
        if (
            int(row.get("label_count", 0) or 0) == 0
            and row.get("final_label") == "Not set"
            and not bool(row.get("_has_assessment_values", False))
        ):
            return "Unlabeled"
        return "Needs Review"

    status["dataset_status"] = status.apply(work_status, axis=1)
    status["is_labeled"] = (
        (status["label_count"] > 0)
        | status["final_label"].ne("Not set")
        | status["_has_assessment_values"]
    )
    return status.drop(columns=["_has_assessment_values"])


def manual_entry_validation_error(record: dict[str, str]) -> str:
    """Return an actionable error for a manual row that import would discard."""
    if not str(record.get("stop_id") or "").strip():
        return "Enter a stop ID before adding this entry."
    try:
        latitude = float(str(record.get("stop_lat") or "").strip())
        longitude = float(str(record.get("stop_lon") or "").strip())
    except ValueError:
        return "Enter numeric latitude and longitude values before adding this entry."
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        return "Enter finite latitude and longitude values before adding this entry."
    if not -90 <= latitude <= 90:
        return "Latitude must be between -90 and 90."
    if not -180 <= longitude <= 180:
        return "Longitude must be between -180 and 180."
    return ""


def dataset_status_metrics(status: pd.DataFrame) -> dict[str, int | float]:
    total = len(status)
    labeled_mask = (
        status.get("is_labeled", pd.Series(False, index=status.index))
        .fillna(False)
        .astype(bool)
    )
    labeled = int(labeled_mask.sum())
    reviewed = int(
        (
            status.get("dataset_status", pd.Series(dtype=str)).eq("Reviewed")
            & labeled_mask
        ).sum()
    )
    needs_review = int(
        status.get("dataset_status", pd.Series(dtype=str)).eq("Needs Review").sum()
    )
    unlabeled = int(
        status.get("dataset_status", pd.Series(dtype=str)).eq("Unlabeled").sum()
    )
    return {
        "total_stops": total,
        "labeled_stops": labeled,
        "reviewed_stops": reviewed,
        "stops_needing_review": needs_review,
        "unlabeled_stops": unlabeled,
        "label_coverage": labeled / total if total else 0.0,
        "review_completion": reviewed / labeled if labeled else 0.0,
    }


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

    attention_count = int(metrics["stops_needing_review"]) + int(
        metrics["unlabeled_stops"]
    )
    queue_message = (
        f"{attention_count:,} stops need labeling or moderator attention."
        if attention_count
        else "All stops are labeled and reviewed."
    )
    queue_status = st.columns([3, 1], vertical_alignment="center")
    queue_status[0].info(queue_message)
    queue_status[1].button(
        "Open Dataset Review →",
        type="primary" if attention_count else "secondary",
        width="stretch",
        key="dataset_open_labels",
        on_click=set_page,
        args=("Labels",),
    )

    with st.expander("Dataset Preview", expanded=False):
        st.caption(
            "Browse the project dataset one page at a time. Only the visible page is rendered."
        )
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
        name = project.get("name") or "Untitled Stop Audit"
        region = project.get("region") or "No region"
        version = project.get("dataset_version") or "draft"
        return f"{name} - {region} - v{version}"

    st.subheader("Project Store")
    st.caption(
        "Changes throughout the workspace save automatically to the active project."
    )
    cols = st.columns([1.7, 1], vertical_alignment="bottom")
    with cols[0]:
        if project_ids:
            selected_project_id = st.selectbox(
                "Active saved project",
                project_ids,
                index=project_ids.index(active_project_id),
                format_func=project_label,
            )
            if selected_project_id != active_project_id:
                if save_active_project_to_store():
                    load_project_into_session(selected_project_id)
                    st.rerun()
    with cols[1]:
        status = database_status()
        if status["using_fallback"]:
            st.caption(f"Database fallback: `{status['active_path']}`")
        else:
            st.caption(f"Database: `{status['active_path']}`")

    create_cols = st.columns([1.5, 0.65], vertical_alignment="bottom")
    with create_cols[0]:
        new_project_name = st.text_input(
            "New blank project name", key="new_project_name"
        )
    with create_cols[1]:
        if st.button("Create blank project", width="stretch"):
            try:
                new_project_id = create_blank_project(new_project_name)
            except DuplicateProjectNameError as error:
                st.error(str(error))
                return
            load_project_into_session(new_project_id)
            st.rerun()


def render_data_page() -> None:
    st.title("Project Data")
    render_project_storage_controls()
    project = st.session_state["project"]
    taxonomy = st.session_state["taxonomy"]

    left, right = st.columns([1, 1])
    with left:
        st.subheader("Project")
        project["name"] = st.text_input("Project name", project["name"])
        project["agency"] = st.text_input("Transit agency", project["agency"])
        project["region"] = st.text_input("Geographic region", project["region"])
        project["owners"] = st.text_input("Owner(s)", project["owners"])
    with right:
        st.subheader("Publication")
        project["visibility"] = st.selectbox(
            "Publication intent",
            ["Public", "Private"],
            index=0 if project["visibility"] == "Public" else 1,
            help=(
                "Sets the project badge and intended audience only. It does not publish the website, "
                "change repository access, or make project data public."
            ),
        )
        project["dataset_version"] = st.text_input(
            "Dataset version", project["dataset_version"]
        )
        project["methodology_version"] = st.text_input(
            "Methodology version", project["methodology_version"]
        )
        project["description"] = st.text_area(
            "Description", project["description"], height=118
        )

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
                st.error(
                    f"This upload is larger than the {format_bytes(max_upload_bytes())} limit."
                )
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
                        if st.button(
                            "Use uploaded GTFS stops",
                            type="primary",
                            key=f"{key_prefix}_gtfs",
                        ):
                            mapping = {
                                field: field
                                for field in REQUIRED_STOP_FIELDS + OPTIONAL_FIELDS
                                if field in raw.columns
                            }
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
                            assessment_modes=st.session_state.get("assessment_modes", []),
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
                        assessment_modes=st.session_state.get("assessment_modes", []),
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
                        assessment_modes=st.session_state.get("assessment_modes", []),
                        metadata={"original_filename": filename},
                        key_prefix=key_prefix,
                        button_label="Use mapped CSV",
                    )
            except Exception as error:
                st.error(f"Could not import this file: {error}")

    with api_tab:
        api_url = st.text_input("Dataset API or file URL", key="api_import_url")
        api_format = st.selectbox(
            "Response format",
            ["Auto detect", "CSV", "GeoJSON"],
            key="api_import_format",
        )
        st.caption(
            f"API imports accept HTTP(S) CSV or GeoJSON responses up to {format_bytes(max_api_bytes())}. "
            "Private network URLs are blocked unless enabled by deployment settings."
        )
        if st.button("Fetch API dataset", key="fetch_api_dataset"):
            for key in ("api_import_raw", "api_import_metadata", "api_import_source"):
                st.session_state.pop(key, None)
            if not api_url.strip():
                st.warning("Enter a URL before fetching.")
            else:
                try:
                    contents = fetch_api_bytes(api_url)
                    requested = "Auto" if api_format == "Auto detect" else api_format
                    raw, metadata = parse_api_response(contents, api_url, requested)
                    st.session_state["api_import_raw"] = raw
                    st.session_state["api_import_metadata"] = metadata
                    st.session_state["api_import_source"] = public_source_url(api_url)
                    st.success(f"Fetched {len(raw):,} records.")
                except Exception as error:
                    st.error(f"Could not fetch this API dataset: {error}")
        api_raw = st.session_state.get("api_import_raw")
        if isinstance(api_raw, pd.DataFrame):
            api_metadata = st.session_state.get("api_import_metadata", {})
            detected = api_metadata.get("detected_format") or api_format.replace(
                "Auto detect", "API"
            )
            render_mapped_import_controls(
                api_raw,
                source_name=st.session_state.get(
                    "api_import_source", public_source_url(api_url)
                ),
                import_format=str(detected),
                project=project,
                taxonomy=taxonomy,
                assessment_modes=st.session_state.get("assessment_modes", []),
                metadata=api_metadata,
                key_prefix="api_import",
                button_label="Use mapped API dataset",
            )

    with manual_tab:
        st.caption(
            "Add stops to the queue with text fields. A stop ID and valid latitude/longitude are "
            "required; invalid entries are explained before they reach the queue."
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
            record = {
                column: str(manual_values.get(column, "")).strip()
                for column in MANUAL_ENTRY_COLUMNS
            }
            validation_error = manual_entry_validation_error(record)
            if validation_error:
                st.error(validation_error)
            else:
                queued_entries = list(st.session_state.get("manual_import_entries", []))
                queued_entries.append(record)
                st.session_state["manual_import_entries"] = queued_entries
                st.success(
                    f"Added entry {len(queued_entries)} to the manual import queue."
                )

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

        manual_source = st.text_input(
            "Manual import source label", "Manual entry", key="manual_import_source"
        )
        if st.button(
            "Use manual entries",
            type="primary",
            key="use_manual_entries",
            disabled=not queued_entries,
        ):
            manual_rows = manual_entry_dataframe(queued_entries)
            mapping = {
                field: field
                for field in REQUIRED_STOP_FIELDS + OPTIONAL_FIELDS
                if field in manual_rows.columns
            }
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
        project["source_name"] = st.text_input(
            "Data source name", project["source_name"]
        )
    with source_cols[1]:
        project["source_license"] = st.text_input(
            "Source license", project["source_license"]
        )
    with source_cols[2]:
        project["source_url"] = st.text_input("Source URL", project["source_url"])

    project_id = st.session_state.get("active_project_id")
    labels = list_shade_labels(project_id) if project_id else pd.DataFrame()
    review_history = list_review_history(project_id) if project_id else pd.DataFrame()
    render_dataset_status(st.session_state["stops"], labels, review_history)
