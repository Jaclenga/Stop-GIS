"""Streamlit controls for configuring and completing assessment modes."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from stop_gis.assessment_modes import (
    AssessmentValidationError,
    STUDY_TEMPLATES,
    enabled_modes,
    normalize_modes,
    validate_assessment_values,
)


def _mode_table(modes: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "enabled": mode["enabled"],
                "key": mode["key"],
                "label": mode["label"],
                "operational_definition": mode["operational_definition"],
                "value_type": mode["value_type"],
                "allowed_values": ", ".join(mode["allowed_values"]),
                "measurement_level": mode["measurement_level"],
                "multiple": mode["multiple"],
                "allow_comment": mode["allow_comment"],
                "collect_confidence": mode["collect_confidence"],
                "required": mode["required"],
                "sort_order": mode["sort_order"],
                "show_on_map": mode["display"]["map"],
                "filter": mode["display"]["filter"],
                "summary": mode["display"]["summary"],
                "export": mode["display"]["export"],
                "weight_mapping": json.dumps(mode["scoring"], sort_keys=True),
            }
            for mode in normalize_modes(modes)
        ]
    )


def _update_modes_from_table(
    original: list[dict[str, Any]], edited: pd.DataFrame
) -> list[dict[str, Any]]:
    by_key = {mode["key"]: mode for mode in normalize_modes(original)}
    result = []
    for _, row in edited.iterrows():
        key = str(row.get("key", "")).strip()
        if key not in by_key:
            continue
        mode = dict(by_key[key])
        mode.update(
            {
                "enabled": bool(row.get("enabled", False)),
                "label": str(row.get("label", "")).strip(),
                "operational_definition": str(row.get("operational_definition", "")).strip(),
                "value_type": str(row.get("value_type", "categorical")),
                "allowed_values": [piece.strip() for piece in str(row.get("allowed_values", "")).split(",") if piece.strip()],
                "measurement_level": str(row.get("measurement_level", "nominal")),
                "multiple": bool(row.get("multiple", False)),
                "allow_comment": bool(row.get("allow_comment", True)),
                "collect_confidence": bool(row.get("collect_confidence", True)),
                "required": bool(row.get("required", False)),
                "sort_order": int(row.get("sort_order") or 1),
            }
        )
        if mode["measurement_level"] == "ordinal":
            mode["ordering"] = list(mode["allowed_values"])
        mode["display"] = {
            "map": bool(row.get("show_on_map", False)),
            "filter": bool(row.get("filter", False)),
            "summary": bool(row.get("summary", False)),
            "export": bool(row.get("export", False)),
        }
        try:
            scoring = json.loads(str(row.get("weight_mapping", "{}")) or "{}")
            if isinstance(scoring, dict):
                mode["scoring"] = scoring
        except json.JSONDecodeError:
            pass
        result.append(mode)
    return normalize_modes(result)


def render_assessment_mode_builder(modes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    st.subheader("Assessment modes")
    st.caption(
        "Enable only the fields reviewers need. Stable keys are locked; labels, definitions, values, order, visibility, and scoring mappings are project-specific."
    )
    template_labels = {config["label"]: key for key, config in STUDY_TEMPLATES.items()}
    template_label = st.selectbox("Starter template", list(template_labels), key="assessment_template")
    if st.button("Apply template", key="apply_assessment_template"):
        from stop_gis.assessment_modes import modes_for_template

        modes = modes_for_template(template_labels[template_label])
        st.session_state["assessment_modes"] = modes
        st.rerun()

    with st.expander("Add a custom mode", expanded=False):
        custom_key = st.text_input("Stable key", placeholder="snow_clearance", key="custom_mode_key")
        custom_label = st.text_input("Label", placeholder="Snow clearance", key="custom_mode_label")
        custom_definition = st.text_area("Operational definition", key="custom_mode_definition")
        custom_type = st.selectbox("Value type", ["categorical", "boolean", "number", "text"], key="custom_mode_type")
        custom_values = st.text_input(
            "Allowed values (comma-separated)", disabled=custom_type != "categorical", key="custom_mode_values"
        )
        if st.button("Add mode", key="add_custom_assessment_mode"):
            candidate = {
                "key": custom_key,
                "label": custom_label,
                "operational_definition": custom_definition,
                "value_type": custom_type,
                "allowed_values": [piece.strip() for piece in custom_values.split(",") if piece.strip()],
                "enabled": True,
                "sort_order": len(modes) + 1,
            }
            try:
                updated_modes = normalize_modes([*modes, candidate])
            except AssessmentValidationError as error:
                st.error(str(error))
            else:
                st.session_state["assessment_modes"] = updated_modes
                st.rerun()

    edited = st.data_editor(
        _mode_table(modes),
        disabled=["key"],
        hide_index=True,
        width="stretch",
        key="assessment_modes_editor",
        column_config={
            "enabled": st.column_config.CheckboxColumn("Enabled"),
            "key": st.column_config.TextColumn("Stable key"),
            "label": st.column_config.TextColumn("Reviewer label"),
            "operational_definition": st.column_config.TextColumn("Operational definition", width="large"),
            "allowed_values": st.column_config.TextColumn("Allowed values (comma-separated)", width="large"),
            "value_type": st.column_config.SelectboxColumn("Value type", options=["categorical", "boolean", "number", "text"]),
            "measurement_level": st.column_config.SelectboxColumn("Measurement", options=["nominal", "ordinal", "interval", "ratio"]),
            "multiple": st.column_config.CheckboxColumn("Multi-select"),
            "allow_comment": st.column_config.CheckboxColumn("Comment"),
            "collect_confidence": st.column_config.CheckboxColumn("Confidence"),
            "required": st.column_config.CheckboxColumn("Required"),
            "sort_order": st.column_config.NumberColumn("Order", min_value=1, step=1),
            "show_on_map": st.column_config.CheckboxColumn("Map"),
            "filter": st.column_config.CheckboxColumn("Filter"),
            "summary": st.column_config.CheckboxColumn("Summary"),
            "export": st.column_config.CheckboxColumn("Export"),
            "weight_mapping": st.column_config.TextColumn("Value scores (JSON)", width="large"),
        },
    )
    try:
        return _update_modes_from_table(modes, edited)
    except AssessmentValidationError as error:
        st.error(str(error))
        return normalize_modes(modes)


def render_scoring_builder(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    st.subheader("Optional composite scores")
    st.caption(
        "Scores are study-specific analytical constructs, not universal measures of stop quality. Weights and missing-value handling are exported with every score."
    )
    frame = pd.DataFrame(
        [
            {
                "enabled": bool(profile.get("enabled", False)),
                "key": str(profile.get("key", "")),
                "label": str(profile.get("label", "")),
                "missing": str(profile.get("missing", "exclude")),
                "weights": json.dumps(profile.get("weights", {}), sort_keys=True),
            }
            for profile in profiles
        ]
    )
    edited = st.data_editor(
        frame, hide_index=True, width="stretch", key="assessment_scoring_editor",
        disabled=["key"],
        column_config={
            "enabled": st.column_config.CheckboxColumn("Enabled"),
            "key": st.column_config.TextColumn("Stable key"),
            "label": st.column_config.TextColumn("Display label"),
            "missing": st.column_config.SelectboxColumn("Missing/unclear", options=["exclude", "zero"]),
            "weights": st.column_config.TextColumn("Mode weights (JSON)", width="large"),
        },
    )
    result = []
    for _, row in edited.iterrows():
        try:
            weights = json.loads(str(row.get("weights", "{}")) or "{}")
        except json.JSONDecodeError:
            st.error(f"Score {row.get('key', '')} has invalid weights JSON.")
            return profiles
        if not isinstance(weights, dict):
            st.error(f"Score {row.get('key', '')} weights must be a JSON object.")
            return profiles
        result.append(
            {
                "enabled": bool(row.get("enabled", False)),
                "key": str(row.get("key", "")).strip(),
                "label": str(row.get("label", "")).strip(),
                "missing": str(row.get("missing", "exclude")),
                "weights": {str(key): float(value) for key, value in weights.items()},
            }
        )
    return result


def render_assessment_form(
    modes: list[dict[str, Any]],
    *,
    key_prefix: str,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Render enabled modes and return a validated payload on submission."""
    active = enabled_modes(modes)
    if not active:
        st.warning("This project has no enabled assessment modes.")
        return None
    defaults = defaults or {}
    values: dict[str, Any] = {}
    comments: dict[str, str] = {}
    confidence: dict[str, float] = {}
    for mode in active:
        key = mode["key"]
        label = mode["label"] + (" *" if mode["required"] else "")
        help_text = mode["operational_definition"] or mode["description"] or None
        default = defaults.get(key)
        if mode["multiple"]:
            default_values = default if isinstance(default, list) else []
            values[key] = st.multiselect(
                label, mode["allowed_values"], default=[value for value in default_values if value in mode["allowed_values"]],
                help=help_text, key=f"{key_prefix}:{key}",
            )
        elif mode["value_type"] == "categorical":
            # Keep an explicit blank choice even for required modes so validation
            # can distinguish a reviewer decision from the first preselected item.
            options = [""] + mode["allowed_values"]
            default_index = options.index(default) if default in options else 0
            values[key] = st.selectbox(label, options, index=default_index, help=help_text, key=f"{key_prefix}:{key}")
        elif mode["value_type"] == "boolean":
            boolean_options = [True, False] if mode["required"] else ["", True, False]
            default_index = boolean_options.index(default) if isinstance(default, bool) else 0
            values[key] = st.selectbox(
                label,
                boolean_options,
                index=default_index,
                format_func=lambda value: "Not assessed" if value == "" else ("Yes" if value else "No"),
                help=help_text,
                key=f"{key_prefix}:{key}",
            )
        elif mode["value_type"] == "number":
            default_text = ""
            try:
                if default is not None and not bool(pd.isna(default)):
                    default_text = str(default)
            except (TypeError, ValueError):
                default_text = str(default or "")
            values[key] = st.text_input(
                label,
                value=default_text,
                placeholder="Enter a number" if mode["required"] else "Not assessed",
                help=help_text,
                key=f"{key_prefix}:{key}",
            )
        else:
            values[key] = st.text_input(label, value=str(default or ""), help=help_text, key=f"{key_prefix}:{key}")
        if mode["allow_comment"]:
            comment = st.text_input(f"{mode['label']} comment (optional)", key=f"{key_prefix}:{key}:comment")
            if comment.strip():
                comments[key] = comment.strip()
        if mode["collect_confidence"]:
            confidence[key] = st.slider(
                f"{mode['label']} confidence", 0.0, 1.0, 0.8, 0.05,
                key=f"{key_prefix}:{key}:confidence",
            )
    if not st.button("Submit assessment", type="primary", key=f"{key_prefix}:submit"):
        return None
    try:
        clean_values = validate_assessment_values(modes, values)
    except AssessmentValidationError as error:
        st.error(str(error))
        return None
    return {
        "assessment_values": clean_values,
        "comments": {key: value for key, value in comments.items() if key in clean_values},
        "confidence": {key: value for key, value in confidence.items() if key in clean_values},
    }

