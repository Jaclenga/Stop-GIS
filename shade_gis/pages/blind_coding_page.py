from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from platform_store import add_image, list_images
from shade_gis.blind_coding import (
    COVERAGE_OPTIONS,
    IMAGE_ADEQUACY_OPTIONS,
    LOCATION_RECOGNIZED_OPTIONS,
    PERMANENCE_OPTIONS,
    SHADE_SOURCE_OPTIONS,
    WAITING_AREA_OPTIONS,
    BlindCodingError,
    CODEBOOK_DEFINITIONS,
    advance_blind_phase,
    blind_adjudication_queue,
    blind_agreement_report,
    blind_progress,
    configure_blind_protocol,
    create_blind_assignments,
    export_blind_adjudications,
    export_blind_ratings,
    get_blind_protocol,
    list_coder_assignments,
    protocol_json,
    submit_blind_adjudication,
    submit_blind_rating,
)


FIELD_LABELS = {
    "shade_source": "Shade source",
    "coverage": "Coverage",
    "waiting_area_covered": "Passenger waiting area covered",
    "permanence": "Shade permanence",
    "image_adequacy": "Image adequacy",
}

PHASE_LABELS = {
    "setup": "Setup",
    "coding": "Independent review open",
    "adjudication": "Adjudication",
    "closed": "Closed",
}


def _unit_label(protocol: dict[str, Any], *, plural: bool = False) -> str:
    unit = "stop" if str(protocol.get("assessment_unit", "image")) == "stop" else "image"
    return f"{unit}s" if plural else unit


def _phase_caption(protocol: dict[str, Any]) -> None:
    st.caption(
        f"Phase: {PHASE_LABELS.get(str(protocol['phase']), str(protocol['phase']).title())} · "
        f"Codebook v{protocol['codebook_version']} · "
        f"Target {int(protocol['target_ratings'])} reviews per {_unit_label(protocol)}"
    )


def _rating_inputs(
    key_prefix: str,
    *,
    include_recognition: bool,
    assessment_unit: str = "image",
) -> dict[str, Any]:
    first, second = st.columns(2)
    with first:
        shade_source = st.selectbox(
            "Shade source", SHADE_SOURCE_OPTIONS, key=f"{key_prefix}:shade_source"
        )
        waiting_area = st.selectbox(
            "Passenger waiting area covered",
            WAITING_AREA_OPTIONS,
            key=f"{key_prefix}:waiting_area",
        )
        adequacy = st.selectbox(
            "Evidence adequacy" if assessment_unit == "stop" else "Image adequacy",
            IMAGE_ADEQUACY_OPTIONS,
            key=f"{key_prefix}:adequacy",
        )
    with second:
        coverage = st.selectbox("Coverage", COVERAGE_OPTIONS, key=f"{key_prefix}:coverage")
        permanence = st.selectbox(
            "Shade permanence", PERMANENCE_OPTIONS, key=f"{key_prefix}:permanence"
        )
        confidence = st.slider(
            "Confidence", min_value=1, max_value=5, value=3, key=f"{key_prefix}:confidence"
        )
    recognition = "no"
    if include_recognition:
        recognition = st.radio(
            "Did you recognize this location?",
            LOCATION_RECOGNIZED_OPTIONS,
            horizontal=True,
            key=f"{key_prefix}:recognized",
        )
    review_method = "standardized_image"
    if assessment_unit == "stop":
        review_method = st.selectbox(
            "Evidence source used",
            ["field_survey", "google_maps", "project_imagery", "other"],
            format_func=lambda value: {
                "field_survey": "Field survey",
                "google_maps": "Google Maps / Street View",
                "project_imagery": "Project imagery",
                "other": "Other evidence",
            }[value],
            key=f"{key_prefix}:review_method",
        )
    return {
        "shade_source": shade_source,
        "coverage": coverage,
        "waiting_area_covered": waiting_area,
        "permanence": permanence,
        "image_adequacy": adequacy,
        "confidence": confidence,
        "location_recognized": recognition,
        "review_method": review_method,
    }


def _render_image(source: str, display_id: str) -> None:
    if source:
        st.image(source, caption=display_id, width="stretch")
    else:
        st.error("This assignment has no accessible standardized image.")


def _render_codebook(assessment_unit: str = "image") -> None:
    rows = []
    for field, definitions in CODEBOOK_DEFINITIONS.items():
        for code, definition in definitions.items():
            rows.append(
                {
                    "Variable": (
                        "Evidence adequacy"
                        if assessment_unit == "stop" and field == "image_adequacy"
                        else FIELD_LABELS[field]
                    ),
                    "Code": code,
                    "Operational definition": definition,
                }
            )
    with st.expander("Codebook definitions", expanded=False):
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def render_coder_workflow(project_id: str, protocol: dict[str, Any]) -> None:
    st.subheader("Reviewer Experience")
    assessment_unit = str(protocol.get("assessment_unit", "image"))
    if assessment_unit == "image":
        st.info(
            "This view intentionally withholds stop IDs, routes, maps, geography, existing labels, "
            "comments, and other reviewers' answers until you submit your own assessment."
        )
    else:
        st.info(
            "This stop-level review shows location details for field or map-based assessment. Existing "
            "labels, comments, and other reviewers' answers remain hidden until independent review closes."
        )
    if protocol["phase"] != "coding":
        st.warning("The research administrator has not started Independent Review, or review has closed.")
        return
    coder_id = st.text_input(
        "Pseudonymous reviewer ID",
        help="Use the ID issued by the research administrator.",
        key="blind_coder_id",
    ).strip()
    if not coder_id:
        st.caption("Enter your reviewer ID to load only your assignments.")
        return
    assignments = list_coder_assignments(project_id, coder_id)
    if assignments.empty:
        st.warning("No assignments were found for that reviewer ID.")
        return
    completed = int(assignments["status"].eq("submitted").sum())
    st.progress(
        completed / len(assignments),
        text=f"{completed} of {len(assignments)} reviews submitted",
    )
    pending = assignments[assignments["status"] == "assigned"].copy()
    if pending.empty:
        st.success("All of your independent reviews are complete. Group results remain hidden.")
        return
    options = pending["assignment_id"].astype(str).tolist()
    selected_assignment = st.selectbox(
        f"Assigned {_unit_label(protocol)}",
        options,
        format_func=lambda assignment_id: str(
            pending.loc[pending["assignment_id"] == assignment_id, "display_id"].iloc[0]
        ),
        key="blind_coder_assignment",
    )
    assignment = pending.loc[pending["assignment_id"] == selected_assignment].iloc[0]
    display_id = str(assignment["display_id"])
    source = str(assignment.get("storage_path", "") or assignment.get("uri", "") or "")
    st.markdown(f"### {display_id}")
    if assessment_unit == "image":
        _render_image(source, display_id)
    else:
        st.markdown(f"**{assignment.get('stop_name', '')}**  ")
        st.write(f"Stop ID: {assignment.get('stop_id', '')}")
        latitude = assignment.get("stop_lat")
        longitude = assignment.get("stop_lon")
        if pd.notna(latitude) and pd.notna(longitude):
            maps_url = f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}"
            st.link_button("Open location in Google Maps", maps_url)
        evidence_sources = assignment.get("evidence_sources", [])
        if isinstance(evidence_sources, list) and evidence_sources:
            with st.expander(f"Project imagery ({len(evidence_sources)})", expanded=False):
                for index, evidence_source in enumerate(evidence_sources, start=1):
                    _render_image(str(evidence_source), f"{display_id} evidence {index}")
        else:
            st.caption("No project imagery is attached. Use the prespecified field or map evidence source.")
    st.markdown("#### Reviewer instructions")
    st.write(str(protocol["instructions"]))
    _render_codebook(assessment_unit)
    with st.form(f"blind_rating_form:{selected_assignment}"):
        rating = _rating_inputs(
            f"blind_rating:{selected_assignment}",
            include_recognition=True,
            assessment_unit=assessment_unit,
        )
        acknowledged = st.checkbox(
            f"I assessed this {_unit_label(protocol)} independently and understand that submission is final."
        )
        submitted = st.form_submit_button(
            "Submit Review", type="primary", width="stretch"
        )
    if submitted:
        if not acknowledged:
            st.error("Confirm that the rating was completed independently before submitting.")
        else:
            try:
                submit_blind_rating(project_id, selected_assignment, coder_id, rating)
            except BlindCodingError as error:
                st.error(str(error))
            else:
                st.success(f"Review for {display_id} was submitted and locked.")
                st.rerun()


def _registered_image_label(row: pd.Series) -> str:
    stop = str(row.get("stop_id", "") or "Unlinked")
    image_type = str(row.get("image_type", "") or "Image")
    return f"{row['id']} · stop {stop} · {image_type}"


def render_protocol_setup(project_id: str, stops: pd.DataFrame, protocol: dict[str, Any]) -> None:
    st.subheader("Study Setup")
    st.caption("Define what reviewers assess and how agreement will be evaluated.")
    with st.container(border=True):
        _render_codebook(str(protocol.get("assessment_unit", "image")))
        with st.form("blind_protocol_form"):
            assessment_unit = st.radio(
                "Assessment unit",
                ["image", "stop"],
                index=0 if str(protocol.get("assessment_unit", "image")) == "image" else 1,
                format_func=lambda value: (
                    "Standardized image" if value == "image" else "Transit stop"
                ),
                horizontal=True,
                help=(
                    "Image mode assigns each image separately. Stop mode assigns one review per stop, "
                    "even when the stop has multiple evidence images."
                ),
            )
            columns = st.columns(2)
            target_ratings = columns[0].number_input(
                "Reviews per item",
                min_value=3,
                max_value=20,
                value=int(protocol["target_ratings"]),
                step=1,
            )
            threshold = columns[1].slider(
                "Agreement threshold",
                min_value=0.0,
                max_value=1.0,
                value=float(protocol["agreement_threshold"]),
                step=0.01,
                help="Items below this agreement level are flagged for adjudication.",
            )
            instructions = st.text_area(
                "Reviewer instructions", value=str(protocol["instructions"]), height=110
            )
            with st.expander("Advanced versioning", expanded=False):
                st.caption(
                    "Saved ratings retain this version so later codebook changes remain traceable."
                )
                codebook_version = st.text_input(
                    "Codebook version", value=str(protocol["codebook_version"])
                )
            save_protocol = st.form_submit_button("Save Study Setup", type="primary")
    if save_protocol:
        try:
            configure_blind_protocol(
                project_id,
                codebook_version=codebook_version,
                target_ratings=int(target_ratings),
                assessment_unit=assessment_unit,
                agreement_threshold=float(threshold),
                instructions=instructions,
            )
        except BlindCodingError as error:
            st.error(str(error))
        else:
            st.success("Independent review protocol saved.")
            st.rerun()

    st.subheader("Review Materials")
    st.caption("Add the standardized images or transit stops that reviewers will assess.")
    stop_records = stops.reset_index(drop=True)
    saved_unit = str(protocol.get("assessment_unit", "image"))
    if saved_unit == "image":
        st.markdown("#### Review Images")
        st.caption(
            "Use cropped or de-identified images with neutral filenames. Remove readable route signs, "
            "addresses, and recognizable landmarks when practical."
        )
        st.caption("Review images are added by URL; file upload is not enabled in this workspace.")
        if not stop_records.empty:
            with st.form("blind_register_image_form"):
                stop_index = st.selectbox(
                    "Transit stop",
                    range(len(stop_records)),
                    format_func=lambda index: (
                        f"{stop_records.iloc[index].get('stop_id', '')} · "
                        f"{stop_records.iloc[index].get('stop_name', '')}"
                    ),
                )
                image_url = st.text_input("Image URL")
                register = st.form_submit_button("Add Review Image")
            if register:
                if not image_url.strip():
                    st.error("A standardized image URL is required.")
                else:
                    stop_id = str(stop_records.iloc[int(stop_index)].get("stop_id", ""))
                    add_image(
                        project_id,
                        {
                            "stop_id": stop_id,
                            "uri": image_url.strip(),
                            "image_type": "blind_coding_standardized",
                            "source": "blind protocol setup",
                        },
                    )
                    st.success("Standardized image registered with a neutral internal ID.")
                    st.rerun()
        units = list_images(project_id)
        if units.empty:
            st.info("No registered images are available yet.")
            return
        unit_labels = {
            str(row["id"]): _registered_image_label(row) for _, row in units.iterrows()
        }
        st.dataframe(
            units[
                [column for column in ["id", "stop_id", "image_type", "created_at"] if column in units]
            ],
            width="stretch",
            hide_index=True,
        )
    else:
        st.markdown("#### Transit stops")
        st.caption(
            "Each selected stop becomes one assessment unit. Attached images are supporting evidence "
            "and do not create additional ratings. Location details remain visible for field and map review."
        )
        if stop_records.empty:
            st.info("No transit stops are available yet.")
            return
        unit_labels = {
            str(row.get("stop_id", "")): f"{row.get('stop_id', '')} · {row.get('stop_name', '')}"
            for _, row in stop_records.iterrows()
        }
        st.dataframe(
            stop_records[
                [
                    column
                    for column in ["stop_id", "stop_name", "stop_lat", "stop_lon"]
                    if column in stop_records
                ]
            ],
            width="stretch",
            hide_index=True,
        )

    st.markdown("#### Reviewer Assignments")
    with st.form("blind_assignment_form"):
        coder_text = st.text_area(
            "Pseudonymous reviewer IDs",
            placeholder="REVIEWER-A\nREVIEWER-B\nREVIEWER-C",
            help=(
                f"Enter one distinct reviewer ID per line. Every selected {_unit_label(protocol)} "
                "is assigned to every reviewer."
            ),
        )
        selected_units = st.multiselect(
            _unit_label(protocol, plural=True).title(),
            list(unit_labels),
            default=list(unit_labels),
            format_func=lambda unit_id: unit_labels[unit_id],
        )
        create = st.form_submit_button("Create randomized assignments", type="primary")
    if create:
        coders = [line.strip() for line in coder_text.splitlines() if line.strip()]
        try:
            created = create_blind_assignments(project_id, coders, selected_units)
        except BlindCodingError as error:
            st.error(str(error))
        else:
            st.success(f"Created {created} new assignments. Existing assignments were preserved.")
            st.rerun()


def _agreement_display(report: pd.DataFrame) -> pd.DataFrame:
    if report.empty:
        return report
    display = report.copy()
    display["variable"] = display["variable"].map(lambda value: FIELD_LABELS.get(value, value))
    display["percent_agreement"] = display["percent_agreement"].map(
        lambda value: "Not enough data" if pd.isna(value) else f"{float(value) * 100:.1f}%"
    )
    display["krippendorff_alpha"] = display["krippendorff_alpha"].map(
        lambda value: "Not enough data" if pd.isna(value) else f"{float(value):.3f}"
    )
    display.columns = [column.replace("_", " ").title() for column in display.columns]
    return display


def render_admin_results(project_id: str, protocol: dict[str, Any]) -> None:
    st.subheader("Agreement before adjudication")
    st.caption(
        "These statistics use only submitted independent reviews. Coverage uses ordinal distance; "
        "the other variables use nominal distance."
    )
    report = blind_agreement_report(project_id)
    st.dataframe(_agreement_display(report), width="stretch", hide_index=True)
    queue = blind_adjudication_queue(project_id)
    low_count = int(queue.get("needs_adjudication", pd.Series(dtype=bool)).sum()) if not queue.empty else 0
    st.metric(f"{_unit_label(protocol, plural=True).title()} below the prespecified threshold", low_count)
    if not queue.empty:
        columns = [
            "display_id", "ratings_completed", "agreement", "status", "shade_source", "coverage",
            "waiting_area_covered", "permanence", "image_adequacy",
        ]
        display = queue[[column for column in columns if column in queue]].copy()
        display["agreement"] = display["agreement"].map(
            lambda value: "" if pd.isna(value) else f"{float(value) * 100:.1f}%"
        )
        display.columns = [column.replace("_", " ").title() for column in display.columns]
        st.dataframe(display, width="stretch", hide_index=True)

    ratings = export_blind_ratings(project_id)
    adjudications = export_blind_adjudications(project_id)
    downloads = st.columns(3)
    downloads[0].download_button(
        "Download submitted reviews",
        ratings.to_csv(index=False).encode("utf-8"),
        "shade_study_blind_ratings.csv",
        "text/csv",
        width="stretch",
    )
    downloads[1].download_button(
        "Download adjudications",
        adjudications.to_csv(index=False).encode("utf-8"),
        "shade_study_blind_adjudications.csv",
        "text/csv",
        width="stretch",
    )
    downloads[2].download_button(
        "Download protocol",
        protocol_json(project_id).encode("utf-8"),
        "shade_study_blind_protocol.json",
        "application/json",
        width="stretch",
    )


def render_admin_workflow(
    project_id: str,
    stops: pd.DataFrame,
    protocol: dict[str, Any],
) -> None:
    if protocol["phase"] == "setup":
        render_protocol_setup(project_id, stops, protocol)

    progress = blind_progress(project_id)
    st.subheader("Study Progress")
    if progress["assignments"] == 0:
        st.info(
            "No review assignments have been created yet. Add review materials and assign reviewers "
            "to begin the study."
        )
    else:
        with st.container(border=True):
            metrics = st.columns(4)
            unit_key = (
                "stops" if str(protocol.get("assessment_unit", "image")) == "stop" else "images"
            )
            metrics[0].metric("Review items", progress[unit_key])
            metrics[1].metric("Assigned reviewers", progress["coders"])
            metrics[2].metric("Submitted reviews", progress["submitted"])
            metrics[3].metric("Reviews remaining", progress["remaining"])

    if protocol["phase"] in {"adjudication", "closed"}:
        render_admin_results(project_id, protocol)
    elif protocol["phase"] == "coding":
        st.info(
            f"Agreement, consensus, and per-{_unit_label(protocol)} rating summaries are withheld "
            "while coding is open."
        )
        st.download_button(
            "Download prespecified protocol",
            protocol_json(project_id).encode("utf-8"),
            "shade_study_blind_protocol.json",
            "application/json",
        )

    action_labels = {
        "setup": "Start Independent Review",
        "coding": "Close Review and Reveal Agreement",
        "adjudication": "Close experiment",
    }
    label = action_labels.get(str(protocol["phase"]))
    if label:
        disabled = (
            protocol["phase"] == "setup" and progress["assignments"] == 0
        ) or (
            protocol["phase"] == "coding" and progress["remaining"] > 0
        )
        if st.button(label, type="primary", disabled=disabled, key="advance_blind_phase"):
            try:
                next_phase = advance_blind_phase(project_id)
            except BlindCodingError as error:
                st.error(str(error))
            else:
                st.success(f"Protocol advanced to {PHASE_LABELS[next_phase]}.")
                st.rerun()
        if protocol["phase"] == "setup" and disabled:
            st.caption("Create reviewer assignments before starting Independent Review.")
        elif disabled:
            st.caption("Every assignment must be submitted before agreement can be revealed.")


def render_adjudicator_workflow(project_id: str, protocol: dict[str, Any]) -> None:
    st.subheader("Identity-blinded adjudication")
    if protocol["phase"] != "adjudication":
        st.warning("Adjudication becomes available only after all independent reviews are submitted.")
        return
    adjudicator_id = st.text_input("Pseudonymous adjudicator ID", key="blind_adjudicator_id").strip()
    if not adjudicator_id:
        st.caption("Enter the adjudicator ID issued by the research administrator.")
        return
    queue = blind_adjudication_queue(project_id)
    queue = queue[(queue["needs_adjudication"]) & (queue["status"] == "Pending")].copy()
    if queue.empty:
        st.success("No low-agreement review items remain for adjudication.")
        return
    selected_id = st.selectbox(
        f"Blinded {_unit_label(protocol)}",
        queue["blind_unit_id"].astype(str).tolist(),
        format_func=lambda blind_id: str(
            queue.loc[queue["blind_unit_id"] == blind_id, "display_id"].iloc[0]
        ),
        key="blind_adjudication_unit",
    )
    item = queue.loc[queue["blind_unit_id"] == selected_id].iloc[0]
    display_id = str(item["display_id"])
    st.markdown(f"### {display_id}")
    if str(protocol.get("assessment_unit", "image")) == "image":
        _render_image(str(item.get("storage_path", "") or item.get("uri", "") or ""), display_id)
    else:
        st.write(f"{item.get('stop_name', '')} · Stop ID {item.get('stop_id', '')}")
        evidence_sources = item.get("evidence_sources", [])
        if isinstance(evidence_sources, list) and evidence_sources:
            with st.expander(f"Project imagery ({len(evidence_sources)})", expanded=False):
                for index, evidence_source in enumerate(evidence_sources, start=1):
                    _render_image(str(evidence_source), f"{display_id} evidence {index}")
    st.caption("Original reviewer identities are withheld. Counts below are the competing submitted codes.")
    _render_codebook(str(protocol.get("assessment_unit", "image")))
    summary = pd.DataFrame(
        [
            {"Variable": FIELD_LABELS[field], "Competing codes": item[field]}
            for field in FIELD_LABELS
        ]
    )
    st.dataframe(summary, width="stretch", hide_index=True)
    with st.form(f"blind_adjudication_form:{selected_id}"):
        decision = _rating_inputs(
            f"blind_adjudication:{selected_id}",
            include_recognition=False,
            assessment_unit=str(protocol.get("assessment_unit", "image")),
        )
        notes = st.text_area("Adjudication rationale")
        submitted = st.form_submit_button("Save locked adjudication", type="primary", width="stretch")
    if submitted:
        try:
            submit_blind_adjudication(
                project_id, selected_id, adjudicator_id, decision, notes=notes
            )
        except BlindCodingError as error:
            st.error(str(error))
        else:
            st.success(f"Adjudicated decision for {display_id} was stored separately from raw ratings.")
            st.rerun()


def render_blind_coding_page() -> None:
    st.title("Independent Review")
    st.markdown(
        "Collect independent assessments by image or transit stop, then reveal agreement and send "
        "low-agreement cases for adjudication."
    )
    st.info("Reviewers cannot see existing ratings until they submit their own.")
    project_id = str(st.session_state.get("active_project_id") or "")
    stops = st.session_state.get("stops", pd.DataFrame())
    if not project_id:
        st.warning("Save or load a project before configuring Independent Review.")
        return
    protocol = get_blind_protocol(project_id)
    _phase_caption(protocol)
    if st.session_state.get("blind_workspace_role") == "Coder":
        st.session_state["blind_workspace_role"] = "Reviewer"
    with st.expander("Admin preview", expanded=False):
        st.caption(
            "Preview each study interface inside this trusted builder. This control does not grant "
            "permissions and must not replace deployment access controls."
        )
        role = st.radio(
            "View as",
            ["Research administrator", "Reviewer", "Adjudicator"],
            horizontal=True,
            key="blind_workspace_role",
        )
    if role == "Reviewer":
        render_coder_workflow(project_id, protocol)
    elif role == "Adjudicator":
        render_adjudicator_workflow(project_id, protocol)
    else:
        render_admin_workflow(project_id, stops, protocol)
