"""Concept-first taxonomy editing components."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping
from typing import Any

import streamlit as st

from stop_gis.domain.shade_dimensions import (
    SHADE_COVERAGE_TAXONOMY,
    SHADE_SOURCE_TAXONOMY,
    normalize_coverage_display_taxonomy,
    normalize_coverage_taxonomy,
    normalize_source_taxonomy,
    normalize_terminology,
)
from stop_gis.assessment_modes import (
    AssessmentValidationError,
    BUILTIN_MODE_KEYS,
    mode_value_label,
    normalize_modes,
)


CONCEPT_GROUPS = ("Shade", "Comfort", "Accessibility", "Safety", "Information", "Other")

CUSTOM_RESPONSE_TYPES = {
    "Categorical choices": "categorical",
    "Yes / No": "boolean",
    "Number": "number",
    "Text": "text",
}

_CONCEPT_GROUP_BY_KEY = {
    "shade_coverage": "Shade",
    "shade_source": "Shade",
    "bench": "Comfort",
    "shelter": "Comfort",
    "trash_can": "Comfort",
    "lighting": "Comfort",
    "cleanliness": "Comfort",
    "bike_rack": "Comfort",
    "sidewalk_connection": "Accessibility",
    "boarding_pad": "Accessibility",
    "wheelchair_accessibility": "Accessibility",
    "curb_ramp": "Accessibility",
    "crosswalk": "Safety",
    "traffic_exposure": "Safety",
    "passenger_information": "Information",
}

_COVERAGE_MODE_TO_LEGACY = {
    "none": "No Shade",
    "limited": "Limited Shade",
    "significant": "Significant Shade",
    "unclear": "Needs Review",
}
_SOURCE_MODE_TO_LEGACY = {
    "natural": "Natural",
    "purpose_built": "Purpose-built",
    "incidental": "Incidental",
}
def taxonomy_edit_mode_key(section: str) -> str:
    """Retain project-scoped UI keys used by older sessions and reset helpers."""
    project_id = st.session_state.get("active_project_id", "draft")
    return f"taxonomy_edit_mode:{project_id}:{section}"


def toggle_taxonomy_edit_mode(mode_key: str) -> None:
    st.session_state[mode_key] = not bool(st.session_state.get(mode_key, False))


def taxonomy_editor_revision_key(section: str) -> str:
    project_id = st.session_state.get("active_project_id", "draft")
    return f"taxonomy_editor_revision:{project_id}:{section}"


def taxonomy_editor_key(section: str) -> str:
    project_id = st.session_state.get("active_project_id", "draft")
    revision = int(st.session_state.get(taxonomy_editor_revision_key(section), 0) or 0)
    base_key = f"{section}_taxonomy_editor:{project_id}"
    return f"{base_key}:{revision}" if revision else base_key


def bump_taxonomy_editor_revision(section: str) -> None:
    revision_key = taxonomy_editor_revision_key(section)
    st.session_state[revision_key] = int(st.session_state.get(revision_key, 0) or 0) + 1


def _workspace_revision_key() -> str:
    project_id = st.session_state.get("active_project_id", "draft")
    return f"taxonomy_workspace_revision:{project_id}"


def bump_taxonomy_workspace_revision() -> None:
    key = _workspace_revision_key()
    st.session_state[key] = int(st.session_state.get(key, 0) or 0) + 1


def _widget_key(name: str) -> str:
    project_id = st.session_state.get("active_project_id", "draft")
    revision = int(st.session_state.get(_workspace_revision_key(), 0) or 0)
    return f"taxonomy:{project_id}:{revision}:{name}"


def _toggle_state(key: str) -> None:
    st.session_state[key] = not bool(st.session_state.get(key, False))


def concept_group(mode: Mapping[str, Any]) -> str:
    return _CONCEPT_GROUP_BY_KEY.get(str(mode.get("key", "")), "Other")


def humanize_value(mode: Mapping[str, Any], code: str) -> str:
    return mode_value_label(mode, code)


def is_custom_dimension(mode: Mapping[str, Any]) -> bool:
    """Return whether a dimension was created by a researcher."""
    return str(mode.get("key", "")) not in BUILTIN_MODE_KEYS


def _dimension_value_labels(mode: Mapping[str, Any], *, limit: int = 6) -> list[str]:
    value_type = str(mode.get("value_type", "categorical"))
    if value_type == "boolean":
        return ["Yes", "No"]
    if value_type == "number":
        return ["Number"]
    if value_type == "text":
        return ["Text response"]
    values = list(mode.get("input_values", mode.get("allowed_values", [])))
    labels = [humanize_value(mode, value) for value in values[:limit]]
    if len(values) > limit:
        labels.append(f"+{len(values) - limit} more")
    return labels


def _dimension_row_label(mode: Mapping[str, Any], *, expanded: bool) -> str:
    badges = []
    if is_custom_dimension(mode):
        badges.append(":blue-badge[Custom]")
    if not mode.get("enabled", False):
        badges.append(":gray-badge[Disabled]")
    badge_text = f"  {' '.join(badges)}" if badges else ""
    description = _one_sentence(mode.get("description"), fallback="No description yet.")
    chips = " ".join(f"`{label}`" for label in _dimension_value_labels(mode))
    chevron = "▼" if expanded else "›"
    return f"{chevron}  **{mode['label']}**{badge_text}  \n{description}  \n{chips}"


def _one_sentence(text: Any, *, fallback: str) -> str:
    value = " ".join(str(text or "").split()).strip()
    if not value:
        return fallback
    match = re.match(r"^(.+?[.!?])(?:\s|$)", value)
    return match.group(1) if match else value


def _slug(value: str, *, fallback: str = "concept") -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_") or fallback
    if slug[0].isdigit():
        slug = f"value_{slug}"
    return slug


def build_custom_dimension(
    *,
    name: str,
    description: str,
    operational_definition: str,
    value_type: str,
    value_labels: Iterable[str] = (),
    special_values: Iterable[str] = (),
    key_override: str = "",
    sort_order: int = 1,
) -> dict[str, Any]:
    """Create a normalized arbitrary dimension from researcher-facing inputs."""
    key = _slug(key_override or name)
    labels: dict[str, str] = {}
    codes: list[str] = []
    candidates = list(value_labels) if value_type == "categorical" else []
    candidates.extend(special_values if value_type == "categorical" else [])
    for raw_label in candidates:
        label = str(raw_label or "").strip()
        if not label:
            continue
        base = _slug(label, fallback="value")
        if base in codes:
            continue
        codes.append(base)
        labels[base] = label
    candidate = {
        "key": key,
        "label": name,
        "description": description,
        "operational_definition": operational_definition,
        "value_type": value_type,
        "allowed_values": codes,
        "value_labels": labels,
        "enabled": True,
        "sort_order": sort_order,
    }
    return normalize_modes([candidate])[0]


def reset_shade_source_definitions(methodology: dict[str, Any]) -> None:
    defaults = {
        item["shade_source"]: item["operational_definition"]
        for item in SHADE_SOURCE_TAXONOMY
    }
    source_taxonomy = normalize_source_taxonomy(methodology.get("shade_source_taxonomy"))
    for item in source_taxonomy:
        item["operational_definition"] = defaults[item["code"]]
    methodology["shade_source_taxonomy"] = source_taxonomy
    bump_taxonomy_editor_revision("shade_source")


def reset_shade_coverage_definitions(
    methodology: dict[str, Any], taxonomy: list[dict[str, Any]]
) -> None:
    defaults = {
        item["shade_coverage"]: item["operational_definition"]
        for item in SHADE_COVERAGE_TAXONOMY
    }
    coverage_taxonomy = normalize_coverage_display_taxonomy(
        methodology.get("shade_coverage_taxonomy"), taxonomy
    )
    for item in coverage_taxonomy:
        item["operational_definition"] = defaults[item["code"]]
    methodology["shade_coverage_taxonomy"] = coverage_taxonomy

    canonical_taxonomy = normalize_coverage_taxonomy(taxonomy)
    for item in canonical_taxonomy:
        if item["name"] in defaults:
            item["description"] = defaults[item["name"]]
    taxonomy[:] = normalize_coverage_taxonomy(canonical_taxonomy)
    bump_taxonomy_editor_revision("shade_coverage")


def hydrate_legacy_shade_metadata(
    modes: Iterable[Mapping[str, Any]],
    methodology: Mapping[str, Any],
    taxonomy: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Bring legacy shade labels and definitions into the unified editor."""
    normalized = normalize_modes(modes)
    by_key = {mode["key"]: mode for mode in normalized}

    coverage = by_key.get("shade_coverage")
    if coverage:
        labels = dict(coverage.get("value_labels", {}))
        definitions = dict(coverage.get("value_definitions", {}))
        display_rows = {
            item["code"]: item
            for item in normalize_coverage_display_taxonomy(
                methodology.get("shade_coverage_taxonomy"), taxonomy
            )
        }
        canonical_rows = {
            item["name"]: item for item in normalize_coverage_taxonomy(taxonomy)
        }
        for mode_code, legacy_code in _COVERAGE_MODE_TO_LEGACY.items():
            if mode_code not in coverage["allowed_values"]:
                continue
            if legacy_code in display_rows:
                labels.setdefault(mode_code, display_rows[legacy_code]["shade_coverage"])
                definitions.setdefault(
                    mode_code, display_rows[legacy_code]["operational_definition"]
                )
            elif legacy_code in canonical_rows:
                definitions.setdefault(mode_code, canonical_rows[legacy_code]["description"])
        labels.setdefault("unclear", "Unknown")
        coverage["value_labels"] = labels
        coverage["value_definitions"] = definitions

    source = by_key.get("shade_source")
    if source:
        labels = dict(source.get("value_labels", {}))
        definitions = dict(source.get("value_definitions", {}))
        source_rows = {
            item["code"]: item
            for item in normalize_source_taxonomy(methodology.get("shade_source_taxonomy"))
        }
        for mode_code, legacy_code in _SOURCE_MODE_TO_LEGACY.items():
            if mode_code not in source["allowed_values"] or legacy_code not in source_rows:
                continue
            labels.setdefault(mode_code, source_rows[legacy_code]["shade_source"])
            definitions.setdefault(
                mode_code, source_rows[legacy_code]["operational_definition"]
            )
        labels.setdefault("unclear", "Unknown")
        source["value_labels"] = labels
        source["value_definitions"] = definitions
    return normalized


def sync_legacy_shade_metadata(
    modes: Iterable[Mapping[str, Any]],
    methodology: dict[str, Any],
    taxonomy: list[dict[str, Any]],
) -> None:
    """Keep established shade fields compatible with the unified editor."""
    by_key = {mode["key"]: mode for mode in normalize_modes(modes)}

    coverage = by_key.get("shade_coverage")
    if coverage:
        labels = coverage.get("value_labels", {})
        definitions = coverage.get("value_definitions", {})
        display_rows = normalize_coverage_display_taxonomy(
            methodology.get("shade_coverage_taxonomy"), taxonomy
        )
        display_by_code = {item["code"]: item for item in display_rows}
        canonical = normalize_coverage_taxonomy(taxonomy)
        canonical_by_name = {item["name"]: item for item in canonical}
        for mode_code, legacy_code in _COVERAGE_MODE_TO_LEGACY.items():
            definition = str(definitions.get(mode_code, "") or "").strip()
            label = str(labels.get(mode_code, "") or "").strip()
            if legacy_code in display_by_code:
                if label:
                    display_by_code[legacy_code]["shade_coverage"] = label
                if definition:
                    display_by_code[legacy_code]["operational_definition"] = definition
            if legacy_code in canonical_by_name and definition:
                canonical_by_name[legacy_code]["description"] = definition
        methodology["shade_coverage_taxonomy"] = normalize_coverage_display_taxonomy(
            display_rows, canonical
        )
        taxonomy[:] = normalize_coverage_taxonomy(canonical)

    source = by_key.get("shade_source")
    if source:
        labels = source.get("value_labels", {})
        definitions = source.get("value_definitions", {})
        source_rows = normalize_source_taxonomy(methodology.get("shade_source_taxonomy"))
        source_by_code = {item["code"]: item for item in source_rows}
        for mode_code, legacy_code in _SOURCE_MODE_TO_LEGACY.items():
            if legacy_code not in source_by_code:
                continue
            label = str(labels.get(mode_code, "") or "").strip()
            definition = str(definitions.get(mode_code, "") or "").strip()
            if label:
                source_by_code[legacy_code]["shade_source"] = label
            if definition:
                source_by_code[legacy_code]["operational_definition"] = definition
        methodology["shade_source_taxonomy"] = normalize_source_taxonomy(source_rows)


def _commit_modes(modes: list[dict[str, Any]]) -> None:
    st.session_state["assessment_modes"] = normalize_modes(modes)
    bump_taxonomy_workspace_revision()
    st.rerun()


def _render_add_concept(modes: list[dict[str, Any]]) -> None:
    open_key = taxonomy_edit_mode_key("add_concept")
    if not st.session_state.get(open_key, False):
        return
    with st.container(key="taxonomy_add_concept"):
        st.subheader("Add custom measure")
        st.caption(
            "Create a project-specific question, such as whether Braille is present."
        )
        response_label = st.selectbox(
            "Response type",
            list(CUSTOM_RESPONSE_TYPES),
            help=(
                "Use Yes / No for presence checks, categorical choices for a code list, "
                "Number for measurements, or Text for notes."
            ),
            key=_widget_key("add_concept_response_type"),
        )
        value_type = CUSTOM_RESPONSE_TYPES[response_label]
        with st.form(_widget_key("add_concept_form"), border=False):
            label = st.text_input(
                "Measure name",
                placeholder=(
                    "Braille present"
                    if value_type == "boolean"
                    else "Tree canopy quality"
                ),
            )
            description = st.text_input(
                "Description",
                placeholder=(
                    "Whether Braille is available on passenger information or signage."
                    if value_type == "boolean"
                    else "Condition of tree canopy around the stop."
                ),
            )
            allowed_values = ""
            if value_type == "categorical":
                allowed_values = st.text_area(
                    "Choices",
                    placeholder="Good\nFair\nPoor\nUnclear",
                    height=122,
                    help="Enter one short choice per line.",
                )
            elif value_type == "boolean":
                st.caption("Reviewers will choose Yes, No, or Not assessed.")
            elif value_type == "number":
                st.caption("Reviewers will enter a number or leave the measure unassessed.")
            else:
                st.caption("Reviewers will enter a short text response.")
            with st.expander("Advanced", expanded=False):
                operational_definition = st.text_area(
                    "Reviewer guidance",
                    placeholder="Add precise instructions only when the short description is not enough.",
                )
                schema_key = st.text_input(
                    "Schema key (optional)",
                    placeholder="Generated automatically from the name",
                    help="This stable key is used in saved data and exports.",
                )
            cancel_col, submit_col = st.columns([0.68, 0.32])
            cancelled = cancel_col.form_submit_button("Cancel", width="stretch")
            submitted = submit_col.form_submit_button(
                "Add custom measure", type="primary", width="stretch"
            )
        if cancelled:
            st.session_state[open_key] = False
            st.rerun()
        if not submitted:
            return
        if not label.strip():
            st.error("Enter a dimension name.")
            return
        values = [
            piece.strip()
            for piece in re.split(r"[\n,]+", allowed_values)
            if piece.strip()
        ]
        try:
            candidate = build_custom_dimension(
                name=label,
                description=description,
                operational_definition=operational_definition or description,
                value_type=value_type,
                value_labels=values,
                key_override=schema_key,
                sort_order=len(modes) + 1,
            )
            updated = normalize_modes([*modes, candidate])
        except AssessmentValidationError as error:
            st.error(str(error))
            return
        st.session_state[open_key] = False
        _commit_modes(updated)


def _render_value_editor(
    mode: dict[str, Any], modes: list[dict[str, Any]], *, has_observations: bool
) -> None:
    values = list(mode["allowed_values"])
    labels = dict(mode.get("value_labels", {}))
    definitions = dict(mode.get("value_definitions", {}))
    scoring = dict(mode.get("scoring", {}))
    current_inputs = list(mode.get("input_values", values))
    selected_inputs = st.multiselect(
        "Reviewer input choices",
        values,
        default=[value for value in values if value in current_inputs],
        format_func=lambda value: humanize_value(mode, value),
        key=_widget_key(f"input_values:{mode['key']}"),
        help=(
            "Choose which values reviewers can enter. Disabled choices remain in the "
            "dataset schema so historical observations stay valid."
        ),
    )
    if selected_inputs:
        selected = set(selected_inputs)
        mode["input_values"] = [value for value in values if value in selected]
    else:
        st.warning("Keep at least one reviewer input choice enabled.")
        mode["input_values"] = current_inputs or list(values)
    if has_observations:
        st.info(
            "Stored observations use this dimension. Display labels and definitions "
            "can still change, but existing values cannot be reordered or removed."
        )
    for index, code in enumerate(values):
        label_col, definition_col, up_col, down_col, remove_col = st.columns(
            [0.25, 0.43, 0.08, 0.08, 0.16], vertical_alignment="bottom"
        )
        labels[code] = label_col.text_input(
            "Display label",
            value=humanize_value({**mode, "value_labels": labels}, code),
            key=_widget_key(f"value_label:{mode['key']}:{code}"),
            label_visibility="collapsed",
        ).strip() or humanize_value(mode, code)
        definitions[code] = definition_col.text_input(
            f"Definition for {labels[code]}",
            value=definitions.get(code, ""),
            placeholder="When should reviewers choose this?",
            key=_widget_key(f"value_definition:{mode['key']}:{code}"),
            label_visibility="collapsed",
        ).strip()
        if up_col.button(
            "↑", key=_widget_key(f"value_up:{mode['key']}:{code}"),
            disabled=index == 0 or has_observations,
            help=f"Move {labels[code]} up",
        ):
            values[index - 1], values[index] = values[index], values[index - 1]
            mode["allowed_values"] = values
            if mode["measurement_level"] == "ordinal":
                mode["ordering"] = list(values)
            _commit_modes(modes)
        if down_col.button(
            "↓", key=_widget_key(f"value_down:{mode['key']}:{code}"),
            disabled=index == len(values) - 1 or has_observations,
            help=f"Move {labels[code]} down",
        ):
            values[index + 1], values[index] = values[index], values[index + 1]
            mode["allowed_values"] = values
            if mode["measurement_level"] == "ordinal":
                mode["ordering"] = list(values)
            _commit_modes(modes)
        if remove_col.button(
            "×", key=_widget_key(f"value_remove:{mode['key']}:{code}"),
            disabled=len(values) <= 1 or has_observations,
            help=(
                "Migrate existing observations before removing values."
                if has_observations
                else f"Remove {labels[code]}"
            ),
        ):
            mode["allowed_values"] = [value for value in values if value != code]
            mode["input_values"] = [
                value for value in mode["input_values"] if value != code
            ]
            mode["ordering"] = [value for value in mode["ordering"] if value != code]
            labels.pop(code, None)
            definitions.pop(code, None)
            scoring.pop(code, None)
            mode["value_labels"] = labels
            mode["value_definitions"] = definitions
            mode["scoring"] = scoring
            _commit_modes(modes)

    mode["value_labels"] = labels
    mode["value_definitions"] = {
        code: definition for code, definition in definitions.items() if definition
    }
    mode["scoring"] = scoring
    add_col, action_col = st.columns([0.78, 0.22], vertical_alignment="bottom")
    new_label = add_col.text_input(
        "New value", placeholder="Add a value", disabled=has_observations,
        key=_widget_key(f"new_value:{mode['key']}")
    )
    if action_col.button(
        "Add value", key=_widget_key(f"add_value:{mode['key']}"),
        width="stretch", disabled=not new_label.strip() or has_observations,
        help=(
            "Migrate existing observations before adding values."
            if has_observations
            else None
        ),
    ):
        base = _slug(new_label, fallback="value")
        code = base
        suffix = 2
        while code in mode["allowed_values"]:
            code = f"{base}_{suffix}"
            suffix += 1
        mode["allowed_values"].append(code)
        mode.setdefault("input_values", []).append(code)
        mode["value_labels"][code] = new_label.strip()
        if mode["measurement_level"] == "ordinal":
            mode["ordering"] = list(mode["allowed_values"])
        _commit_modes(modes)


def _render_schema_details(mode: dict[str, Any], *, has_observations: bool) -> None:
    with st.expander("Schema details", expanded=False):
        st.text_input(
            "Schema key", value=mode["key"], disabled=True,
            help="Stable identifier used in saved data and exports.",
            key=_widget_key(f"schema_key:{mode['key']}"),
        )
        type_col, measurement_col, order_col = st.columns(3)
        previous_type = mode["value_type"]
        mode["value_type"] = type_col.selectbox(
            "Value type", ["categorical", "boolean", "number", "text"],
            index=["categorical", "boolean", "number", "text"].index(previous_type),
            disabled=has_observations,
            key=_widget_key(f"value_type:{mode['key']}"),
        )
        if mode["value_type"] == "categorical" and not mode["allowed_values"]:
            mode["allowed_values"] = ["unknown"]
            mode["value_labels"] = {"unknown": "Unknown"}
        levels = ["nominal", "ordinal", "interval", "ratio"]
        mode["measurement_level"] = measurement_col.selectbox(
            "Measurement", levels, index=levels.index(mode["measurement_level"]),
            disabled=has_observations,
            key=_widget_key(f"measurement:{mode['key']}"),
        )
        mode["ordering"] = (
            list(mode["allowed_values"])
            if mode["measurement_level"] == "ordinal"
            else []
        )
        mode["sort_order"] = int(order_col.number_input(
            "Order", min_value=1, step=1, value=int(mode["sort_order"]),
            key=_widget_key(f"sort_order:{mode['key']}"),
        ))
        option_left, option_right = st.columns(2)
        mode["required"] = option_left.toggle(
            "Required response", value=mode["required"],
            key=_widget_key(f"required:{mode['key']}"),
        )
        mode["multiple"] = option_right.toggle(
            "Allow multiple values", value=mode["multiple"],
            disabled=mode["value_type"] != "categorical" or has_observations,
            key=_widget_key(f"multiple:{mode['key']}"),
        )
        if mode["value_type"] != "categorical":
            mode["multiple"] = False
        mode["allow_comment"] = option_left.toggle(
            "Allow reviewer comments", value=mode["allow_comment"],
            key=_widget_key(f"comments:{mode['key']}"),
        )
        mode["collect_confidence"] = option_right.toggle(
            "Collect confidence", value=mode["collect_confidence"],
            key=_widget_key(f"confidence:{mode['key']}"),
        )
        st.caption("Use this concept in")
        display = dict(mode["display"])
        display_left, display_right = st.columns(2)
        display["map"] = display_left.toggle(
            "Maps", value=display["map"], key=_widget_key(f"map:{mode['key']}")
        )
        display["filter"] = display_right.toggle(
            "Filters", value=display["filter"], key=_widget_key(f"filter:{mode['key']}")
        )
        display["summary"] = display_left.toggle(
            "Summaries", value=display["summary"], key=_widget_key(f"summary:{mode['key']}")
        )
        display["export"] = display_right.toggle(
            "Exports", value=display["export"], key=_widget_key(f"export:{mode['key']}")
        )
        mode["display"] = display
        if mode["allowed_values"]:
            st.caption("Optional value scores")
            scoring = dict(mode.get("scoring", {}))
            for code in mode["allowed_values"]:
                score_text = st.text_input(
                    f"Score for {humanize_value(mode, code)}",
                    value=str(scoring[code]) if code in scoring else "",
                    placeholder="Not scored",
                    key=_widget_key(f"score:{mode['key']}:{code}"),
                ).strip()
                if not score_text:
                    scoring.pop(code, None)
                else:
                    try:
                        scoring[code] = float(score_text)
                    except ValueError:
                        st.warning(f"Enter a number for {humanize_value(mode, code)}, or leave it blank.")
            mode["scoring"] = scoring


def _render_concept(
    mode: dict[str, Any],
    modes: list[dict[str, Any]],
    observed_values: Mapping[str, set[str]],
) -> None:
    key = mode["key"]
    has_observations = bool(observed_values.get(key))
    expanded_key = taxonomy_edit_mode_key(f"concept:{key}")
    expanded = bool(st.session_state.get(expanded_key, False))
    with st.container(key=f"taxonomy_concept_{_slug(key)}"):
        if not mode["enabled"]:
            st.markdown(
                '<span class="taxonomy-disabled-marker" aria-hidden="true"></span>',
                unsafe_allow_html=True,
            )
        with st.container(key=f"taxonomy_dimension_row_{_slug(key)}"):
            summary_col, state_col = st.columns(
                [0.84, 0.16], vertical_alignment="center"
            )
            with summary_col.container(key=f"taxonomy_dimension_summary_{_slug(key)}"):
                st.button(
                    _dimension_row_label(mode, expanded=expanded),
                    key=_widget_key(f"concept_trigger:{key}"),
                    on_click=_toggle_state, args=(expanded_key,), width="stretch",
                    help=f"{'Collapse' if expanded else 'Expand'} {mode['label']}",
                )
            with state_col.container(key=f"taxonomy_dimension_state_{_slug(key)}"):
                if st.button(
                    "Disable" if mode["enabled"] else "Enable",
                    type="secondary" if mode["enabled"] else "primary",
                    key=_widget_key(f"dimension_state:{key}"),
                    width="stretch",
                    help=(
                        f"Disable {mode['label']} for labeling"
                        if mode["enabled"]
                        else f"Enable {mode['label']} for labeling"
                    ),
                ):
                    mode["enabled"] = not mode["enabled"]
                    _commit_modes(modes)
        if not expanded:
            return

        with st.container(key=f"taxonomy_concept_body_{_slug(key)}"):
            if mode["value_type"] == "categorical":
                definitions = mode.get("value_definitions", {})
                value_rows = []
                for code in mode["allowed_values"]:
                    label = html.escape(humanize_value(mode, code))
                    definition = html.escape(
                        str(definitions.get(code, "") or "No definition yet.")
                    )
                    value_rows.append(
                        '<div class="taxonomy-value-row">'
                        f'<span class="taxonomy-value-name">{label}</span>'
                        f'<span class="taxonomy-value-definition">{definition}</span>'
                        "</div>"
                    )
                st.markdown(
                    '<div class="taxonomy-values-heading">Values</div>'
                    + "".join(value_rows),
                    unsafe_allow_html=True,
                )
            elif mode["value_type"] == "boolean":
                st.markdown(
                    '<div class="taxonomy-values-heading">Response</div>'
                    '<div class="taxonomy-value-row">'
                    '<span class="taxonomy-value-name">Yes / No</span>'
                    '<span class="taxonomy-value-definition">A binary response.</span>'
                    "</div>",
                    unsafe_allow_html=True,
                )
            elif mode["value_type"] == "number":
                st.caption("Reviewers enter a number.")
            else:
                st.caption("Reviewers enter a short text response.")

            edit_key = taxonomy_edit_mode_key(f"edit_concept:{key}")
            st.button(
                "Done editing" if st.session_state.get(edit_key) else "Edit dimension",
                key=_widget_key(f"toggle_concept_edit:{key}"),
                on_click=_toggle_state, args=(edit_key,),
            )
            if st.session_state.get(edit_key):
                mode["label"] = st.text_input(
                    "Dimension name", value=mode["label"],
                    key=_widget_key(f"concept_label:{key}"),
                ).strip() or mode["label"]
                mode["description"] = st.text_input(
                    "One-sentence description", value=mode["description"],
                    key=_widget_key(f"concept_description:{key}"),
                ).strip()
                mode["operational_definition"] = st.text_area(
                    "Reviewer guidance", value=mode["operational_definition"],
                    key=_widget_key(f"concept_definition:{key}"),
                ).strip()
                if mode["value_type"] == "categorical":
                    st.markdown("**Edit values**")
                    _render_value_editor(
                        mode, modes, has_observations=has_observations
                    )
            _render_schema_details(mode, has_observations=has_observations)
            if st.session_state.get(edit_key):
                if st.button(
                    "Delete dimension",
                    key=_widget_key(f"delete_dimension:{key}"),
                    disabled=has_observations or len(modes) <= 1,
                    help=(
                        "This dimension has observations. Disable it or migrate the data before deletion."
                        if has_observations
                        else (
                            "A project must keep at least one coding dimension."
                            if len(modes) <= 1
                            else f"Delete {mode['label']}"
                        )
                    ),
                ):
                    modes[:] = [item for item in modes if item["key"] != key]
                    _commit_modes(modes)


def _render_reference_terms(methodology: dict[str, Any], search: str) -> None:
    terms = normalize_terminology(methodology.get("terminology"))
    visible = [
        (index, term)
        for index, term in enumerate(terms)
        if not search or search in f"{term['term']} {term['operational_definition']}".casefold()
    ]
    if not visible and search:
        st.info("No terminology matches your search.")
        return
    st.caption("Shared language used in reviewer guidance and published documentation.")
    for index, term in visible:
        stable = _slug(term["term"], fallback=f"term_{index}")
        expanded_key = taxonomy_edit_mode_key(f"term:{index}:{stable}")
        expanded = bool(st.session_state.get(expanded_key, False))
        with st.container(key=f"taxonomy_reference_{index}_{stable}"):
            trigger_col, status_col = st.columns([0.76, 0.24], vertical_alignment="center")
            trigger_col.button(
                f"{'▾' if expanded else '▸'}  **{term['term']}**  \n{_one_sentence(term['operational_definition'], fallback='No definition yet.')}",
                key=_widget_key(f"term_trigger:{index}:{stable}"),
                on_click=_toggle_state, args=(expanded_key,), width="stretch",
                help=f"{'Collapse' if expanded else 'Expand'} {term['term']}",
            )
            status_col.markdown('<span class="taxonomy-reference-status">Reference</span>', unsafe_allow_html=True)
            if expanded:
                term["term"] = st.text_input(
                    "Term", value=term["term"], key=_widget_key(f"term_name:{index}:{stable}")
                ).strip() or term["term"]
                term["operational_definition"] = st.text_area(
                    "Definition", value=term["operational_definition"],
                    key=_widget_key(f"term_definition:{index}:{stable}"),
                ).strip()
                if st.button("Remove term", key=_widget_key(f"remove_term:{index}:{stable}")):
                    terms.pop(index)
                    methodology["terminology"] = normalize_terminology(terms)
                    bump_taxonomy_workspace_revision()
                    st.rerun()
    methodology["terminology"] = normalize_terminology(terms)
    with st.expander("Add reference term", expanded=False):
        with st.form(_widget_key("add_reference_term"), border=False):
            term_name = st.text_input("Term name")
            term_definition = st.text_area("Definition")
            if st.form_submit_button("Add term") and term_name.strip():
                methodology["terminology"] = normalize_terminology(
                    [*terms, {"term": term_name, "operational_definition": term_definition}]
                )
                bump_taxonomy_workspace_revision()
                st.rerun()


def _render_group_header(group: str, count: int, *, search_active: bool) -> bool:
    state_key = taxonomy_edit_mode_key(f"group:{group}")
    if state_key not in st.session_state:
        st.session_state[state_key] = group in {"Shade", "Comfort", "Other"}
    expanded = True if search_active else bool(st.session_state[state_key])
    label = f"{'▼' if expanded else '▶'}  {group.upper()} · {count} dimension"
    if count != 1:
        label += "s"
    with st.container(key=f"taxonomy_group_{_slug(group)}"):
        st.button(
            label,
            key=_widget_key(f"group_trigger:{group}"),
            on_click=_toggle_state,
            args=(state_key,),
            width="stretch",
            help=f"{'Collapse' if expanded else 'Expand'} {group}",
        )
    return expanded


def render_taxonomy_editor(
    modes: Iterable[Mapping[str, Any]],
    methodology: dict[str, Any],
    taxonomy: list[dict[str, Any]],
    observed_values: Mapping[str, set[str]] | None = None,
) -> list[dict[str, Any]]:
    """Render the unified, searchable coding-dimension workspace."""
    normalized = hydrate_legacy_shade_metadata(modes, methodology, taxonomy)
    observed_values = observed_values or {}

    with st.container(key="taxonomy_view_switcher"):
        view = st.segmented_control(
            "Taxonomy view",
            ["Dimensions", "Terminology"],
            default="Dimensions",
            key=_widget_key("taxonomy_view"),
            label_visibility="collapsed",
        )

    if view == "Terminology":
        search = st.text_input(
            "Search terminology",
            placeholder="Search terminology…",
            key=_widget_key("terminology_search"),
            label_visibility="collapsed",
        ).strip().casefold()
        _render_reference_terms(methodology, search)
        normalized = normalize_modes(normalized)
        sync_legacy_shade_metadata(normalized, methodology, taxonomy)
        return normalized

    search_col, action_col = st.columns([0.78, 0.22], vertical_alignment="bottom")
    search = search_col.text_input(
        "Search dimensions", placeholder="Search dimensions…",
        key=_widget_key("concept_search"), label_visibility="collapsed",
    ).strip().casefold()
    add_key = taxonomy_edit_mode_key("add_concept")
    with action_col.container(key="taxonomy_add_dimension_action"):
        st.button(
            "+ Add custom measure", type="primary", width="stretch",
            key=_widget_key("add_concept_button"),
            on_click=_toggle_state, args=(add_key,),
        )
    _render_add_concept(normalized)

    grouped: dict[str, list[dict[str, Any]]] = {name: [] for name in CONCEPT_GROUPS}
    for mode in normalized:
        haystack = " ".join(
            [mode["label"], mode["description"], mode["key"],
             *(humanize_value(mode, value) for value in mode["allowed_values"])]
        ).casefold()
        if search and search not in haystack:
            continue
        grouped[concept_group(mode)].append(mode)

    result_count = sum(len(items) for items in grouped.values())
    if search and not result_count:
        st.info("No dimensions match your search.")
    for group in CONCEPT_GROUPS:
        if not grouped[group]:
            continue
        if _render_group_header(group, len(grouped[group]), search_active=bool(search)):
            for mode in grouped[group]:
                _render_concept(mode, normalized, observed_values)

    normalized = normalize_modes(normalized)
    sync_legacy_shade_metadata(normalized, methodology, taxonomy)
    return normalized
