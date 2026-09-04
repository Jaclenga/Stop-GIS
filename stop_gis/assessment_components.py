"""Streamlit controls for completing researcher-defined coding dimensions."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from stop_gis.assessment_modes import (
    AssessmentValidationError,
    enabled_modes,
    mode_value_label,
    validate_assessment_values,
)


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


def categorical_control_kind(mode: dict[str, Any]) -> str:
    """Choose a compact generic control from the configured value count."""
    value_count = len(mode.get("input_values", mode.get("allowed_values", [])))
    if mode.get("multiple"):
        return "pills" if value_count <= 8 else "multiselect"
    if value_count <= 4:
        return "segmented"
    if value_count <= 8:
        return "pills"
    return "selectbox"


def render_dimension_value_control(
    mode: dict[str, Any], *, label: str, default: Any, key: str, help_text: str | None
) -> Any:
    """Render one researcher-defined dimension without field-specific UI code."""
    options = list(mode.get("input_values", mode["allowed_values"]))

    def format_value(value: Any) -> str:
        return mode_value_label(mode, value)

    kind = categorical_control_kind(mode)
    if mode["multiple"]:
        default_values = default if isinstance(default, list) else []
        selected = [value for value in default_values if value in options]
        if kind == "pills":
            return st.pills(
                label, options, selection_mode="multi", default=selected,
                format_func=format_value, help=help_text, key=key, width="stretch",
            )
        return st.multiselect(
            label, options, default=selected, format_func=format_value,
            help=help_text, key=key,
        )
    selected_default = default if default in options else None
    if kind == "segmented":
        return st.segmented_control(
            label, options, default=selected_default, format_func=format_value,
            help=help_text, key=key, width="stretch",
        )
    if kind == "pills":
        return st.pills(
            label, options, default=selected_default, format_func=format_value,
            help=help_text, key=key, width="stretch",
        )
    select_options = [""] + options
    default_index = select_options.index(default) if default in select_options else 0
    return st.selectbox(
        label, select_options, index=default_index,
        format_func=lambda value: "Not assessed" if value == "" else format_value(value),
        help=help_text, key=key,
    )


def _render_selected_value_definitions(mode: dict[str, Any], value: Any) -> None:
    definitions = mode.get("value_definitions", {})
    selected = value if isinstance(value, list) else [value]
    guidance = [
        f"**{mode_value_label(mode, code)}:** {definitions[code]}"
        for code in selected
        if code in definitions and str(definitions[code]).strip()
    ]
    if guidance:
        st.caption("  \n".join(guidance))


def render_assessment_form(
    modes: list[dict[str, Any]],
    *,
    key_prefix: str,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Render enabled modes and return a validated payload on submission."""
    active = enabled_modes(modes)
    if not active:
        st.warning("This project has no included coding dimensions.")
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
        if mode["value_type"] == "categorical":
            values[key] = render_dimension_value_control(
                mode, label=label, default=default,
                key=f"{key_prefix}:{key}", help_text=help_text,
            )
            _render_selected_value_definitions(mode, values[key])
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

