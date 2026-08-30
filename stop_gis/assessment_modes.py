"""Configurable assessment modes, validation, summaries, and transparent scores.

Mode definitions are plain JSON-compatible dictionaries so they can travel in
project bundles and deployment artifacts without coupling the domain model to a
particular database or UI framework.
"""

from __future__ import annotations

import copy
import ast
import json
import math
import re
from collections import Counter
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd


VALUE_TYPES = {"categorical", "boolean", "number", "text"}
MEASUREMENT_LEVELS = {"nominal", "ordinal", "interval", "ratio"}
MODE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def _mode(
    key: str,
    label: str,
    description: str,
    operational_definition: str,
    values: Sequence[str],
    *,
    ordinal: bool = False,
    multiple: bool = False,
    enabled: bool = False,
    scoring: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    allowed = list(values)
    return {
        "key": key,
        "label": label,
        "description": description,
        "operational_definition": operational_definition,
        "value_type": "categorical",
        "allowed_values": allowed,
        "ordering": allowed if ordinal else [],
        "multiple": multiple,
        "allow_comment": True,
        "collect_confidence": True,
        "enabled": enabled,
        "required": False,
        "sort_order": 0,
        "measurement_level": "ordinal" if ordinal else "nominal",
        "scoring": dict(scoring or {}),
        "display": {"map": True, "filter": True, "summary": True, "export": True},
    }


BUILTIN_ASSESSMENT_MODES: list[dict[str, Any]] = [
    _mode(
        "shade_coverage",
        "Shade coverage",
        "Visible shade reaching the passenger waiting area.",
        "Classify shade at the place passengers reasonably wait, rather than shade that is merely nearby.",
        ["none", "limited", "significant", "unclear"],
        ordinal=True,
        scoring={"none": 0, "limited": 0.5, "significant": 1},
    ),
    _mode(
        "shade_source",
        "Shade source",
        "The features that visibly provide shade to the waiting area.",
        "Select every visible source: natural vegetation, purpose-built shelter, or incidental built form.",
        ["natural", "purpose_built", "incidental", "unclear"],
        multiple=True,
    ),
    _mode(
        "bench", "Bench", "Presence and usability of designated passenger seating.",
        "Capture whether designated passenger seating exists at the stop and appears usable.",
        ["none", "present", "damaged", "unclear"], scoring={"none": 0, "damaged": 0.25, "present": 1},
    ),
    _mode(
        "shelter", "Shelter", "Presence and condition of passenger weather protection.",
        "Classify a structure intended to protect waiting passengers from sun or weather.",
        ["none", "partial", "full", "damaged", "unclear"], ordinal=True,
        scoring={"none": 0, "damaged": 0.25, "partial": 0.5, "full": 1},
    ),
    _mode(
        "trash_can", "Trash can", "Presence of a public waste receptacle at the stop.",
        "Record a waste receptacle within the immediate stop waiting area.",
        ["yes", "no", "unclear"], scoring={"no": 0, "yes": 1},
    ),
    _mode(
        "lighting", "Lighting", "Lighting available for the stop waiting area.",
        "Classify visible lighting by whether it is absent, nearby, or dedicated to the stop.",
        ["none", "nearby", "dedicated", "unclear"], ordinal=True,
        scoring={"none": 0, "nearby": 0.5, "dedicated": 1},
    ),
    _mode(
        "passenger_information", "Passenger information", "Passenger-facing route and service information.",
        "Record the most informative passenger information visibly available at the stop.",
        ["none", "stop_sign_only", "route_information", "schedule_information", "real_time_information", "unclear"],
        ordinal=True,
        scoring={"none": 0, "stop_sign_only": 0.25, "route_information": 0.5, "schedule_information": 0.75, "real_time_information": 1},
    ),
    _mode(
        "sidewalk_connection", "Sidewalk connection", "Quality of the pedestrian connection to the stop.",
        "Assess whether a continuous and reasonably usable pedestrian path reaches the waiting area.",
        ["none", "poor", "adequate", "unclear"], ordinal=True,
        scoring={"none": 0, "poor": 0.25, "adequate": 1},
    ),
    _mode(
        "boarding_pad", "Boarding pad", "Suitability of the boarding and alighting surface.",
        "Assess whether a stable, sufficiently sized surface supports boarding and alighting.",
        ["none", "inadequate", "adequate", "unclear"], ordinal=True,
        scoring={"none": 0, "inadequate": 0.25, "adequate": 1},
    ),
    _mode(
        "wheelchair_accessibility", "Wheelchair accessibility", "Observed wheelchair accessibility of the stop area.",
        "Record whether the waiting and boarding area appears reachable and usable by a wheelchair user.",
        ["yes", "no", "unclear"], scoring={"no": 0, "yes": 1},
    ),
    _mode(
        "curb_ramp", "Curb ramp", "Presence of an applicable curb ramp serving the stop approach.",
        "Record a curb ramp where a curb crossing is needed to reach or use the stop.",
        ["yes", "no", "not_applicable", "unclear"], scoring={"no": 0, "yes": 1},
    ),
    _mode(
        "crosswalk", "Crosswalk", "Presence of a marked crossing relevant to stop access.",
        "Record a marked crosswalk serving a likely pedestrian approach to the stop.",
        ["yes", "no", "unclear"], scoring={"no": 0, "yes": 1},
    ),
    _mode(
        "bike_rack", "Bike rack", "Presence of bicycle parking at or immediately near the stop.",
        "Record a fixed bicycle rack intended for public use in the stop area.",
        ["yes", "no", "unclear"], scoring={"no": 0, "yes": 1},
    ),
    _mode(
        "cleanliness", "Cleanliness", "Observed cleanliness of the waiting area.",
        "Rate visible litter, debris, and general upkeep within the immediate waiting area.",
        ["poor", "fair", "good", "unclear"], ordinal=True,
        scoring={"poor": 0, "fair": 0.5, "good": 1},
    ),
    _mode(
        "traffic_exposure", "Traffic exposure", "Passenger exposure to nearby moving traffic.",
        "Rate the apparent intensity and proximity of traffic beside the waiting area.",
        ["low", "medium", "high", "unclear"], ordinal=True,
        scoring={"high": 0, "medium": 0.5, "low": 1},
    ),
]

for _index, _definition in enumerate(BUILTIN_ASSESSMENT_MODES, start=1):
    _definition["sort_order"] = _index

BUILTIN_MODE_KEYS = tuple(item["key"] for item in BUILTIN_ASSESSMENT_MODES)

STUDY_TEMPLATES: dict[str, dict[str, Any]] = {
    "basic_stop_amenities": {
        "label": "Basic Stop Amenities",
        "description": "A compact inventory of common passenger amenities.",
        "modes": ["bench", "shelter", "trash_can", "lighting", "passenger_information"],
    },
    "accessibility_audit": {
        "label": "Accessibility Audit",
        "description": "Pedestrian connection and boarding accessibility fields.",
        "modes": ["sidewalk_connection", "boarding_pad", "wheelchair_accessibility", "curb_ramp", "crosswalk"],
    },
    "passenger_comfort": {
        "label": "Passenger Comfort",
        "description": "Amenities and environmental conditions that shape the waiting experience.",
        "modes": ["bench", "shelter", "shade_coverage", "shade_source", "lighting", "cleanliness", "traffic_exposure"],
    },
    "custom": {
        "label": "Custom",
        "description": "Choose and configure assessment modes manually.",
        "modes": [],
    },
}

DEFAULT_SCORING_PROFILES: list[dict[str, Any]] = [
    {
        "key": "comfort",
        "label": "Comfort score",
        "enabled": False,
        "missing": "exclude",
        "weights": {"bench": 1, "shelter": 1, "shade_coverage": 1, "lighting": 1, "cleanliness": 1, "passenger_information": 1},
    },
    {
        "key": "accessibility",
        "label": "Accessibility score",
        "enabled": False,
        "missing": "exclude",
        "weights": {"sidewalk_connection": 1, "boarding_pad": 1, "wheelchair_accessibility": 1, "curb_ramp": 1, "crosswalk": 1},
    },
]


class AssessmentValidationError(ValueError):
    """Raised when a mode definition or submitted value violates its schema."""


def builtin_modes() -> list[dict[str, Any]]:
    return copy.deepcopy(BUILTIN_ASSESSMENT_MODES)


def mode_map(modes: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("key", "")): dict(item) for item in modes if item.get("key")}


def normalize_mode_definition(mode: Mapping[str, Any], *, sort_order: int = 1) -> dict[str, Any]:
    key = str(mode.get("key", "")).strip().lower()
    if not MODE_KEY_PATTERN.fullmatch(key):
        raise AssessmentValidationError(
            f"Mode key {key!r} must start with a letter and contain only lowercase letters, numbers, and underscores."
        )
    value_type = str(mode.get("value_type", "categorical")).strip().lower()
    if value_type not in VALUE_TYPES:
        raise AssessmentValidationError(f"Unsupported value type for {key}: {value_type}")
    allowed_values = [str(value).strip() for value in mode.get("allowed_values", []) if str(value).strip()]
    if len(set(allowed_values)) != len(allowed_values):
        raise AssessmentValidationError(f"Mode {key} contains duplicate allowed values.")
    if value_type == "categorical" and not allowed_values:
        raise AssessmentValidationError(f"Categorical mode {key} needs at least one allowed value.")
    ordering = [str(value).strip() for value in mode.get("ordering", []) if str(value).strip()]
    if any(value not in allowed_values for value in ordering):
        raise AssessmentValidationError(f"Mode {key} ordering contains a value outside allowed_values.")
    measurement = str(mode.get("measurement_level") or ("ordinal" if ordering else "nominal")).lower()
    if measurement not in MEASUREMENT_LEVELS:
        raise AssessmentValidationError(f"Unsupported measurement level for {key}: {measurement}")
    display = {"map": True, "filter": True, "summary": True, "export": True}
    if isinstance(mode.get("display"), Mapping):
        display.update({name: bool(value) for name, value in mode["display"].items() if name in display})
    scoring = mode.get("scoring") if isinstance(mode.get("scoring"), Mapping) else {}
    return {
        "key": key,
        "label": str(mode.get("label") or key.replace("_", " ").title()).strip(),
        "description": str(mode.get("description") or "").strip(),
        "operational_definition": str(mode.get("operational_definition") or "").strip(),
        "value_type": value_type,
        "allowed_values": allowed_values,
        "ordering": ordering,
        "multiple": bool(mode.get("multiple", False)),
        "allow_comment": bool(mode.get("allow_comment", True)),
        "collect_confidence": bool(mode.get("collect_confidence", True)),
        "enabled": bool(mode.get("enabled", False)),
        "required": bool(mode.get("required", False)),
        "sort_order": int(mode.get("sort_order") or sort_order),
        "measurement_level": measurement,
        "scoring": {str(name): float(value) for name, value in scoring.items() if _finite_number(value)},
        "display": display,
    }


def normalize_modes(modes: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    source = list(modes or [])
    if not source:
        source = builtin_modes()
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, mode in enumerate(source, start=1):
        item = normalize_mode_definition(mode, sort_order=index)
        if item["key"] in seen:
            raise AssessmentValidationError(f"Duplicate assessment mode key: {item['key']}")
        seen.add(item["key"])
        normalized.append(item)
    return sorted(normalized, key=lambda item: (item["sort_order"], item["label"].casefold()))


def modes_for_template(template_key: str) -> list[dict[str, Any]]:
    if template_key not in STUDY_TEMPLATES:
        raise KeyError(f"Unknown study template: {template_key}")
    enabled = set(STUDY_TEMPLATES[template_key]["modes"])
    modes = builtin_modes()
    for item in modes:
        item["enabled"] = item["key"] in enabled
    return modes


def legacy_shade_modes(taxonomy: Iterable[Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return enabled shade definitions for a project created before assessment modes."""
    modes = builtin_modes()
    by_key = {item["key"]: item for item in modes}
    by_key["shade_coverage"]["enabled"] = True
    by_key["shade_source"]["enabled"] = True
    legacy = list(taxonomy or [])
    labels = [str(item.get("name", "")).strip() for item in legacy if str(item.get("name", "")).strip()]
    descriptions = {
        str(item.get("name", "")).strip(): str(item.get("description", "")).strip()
        for item in legacy
    }
    canonical = [("No Shade", "none"), ("Limited Shade", "limited"), ("Significant Shade", "significant")]
    if labels:
        by_key["shade_coverage"]["allowed_values"] = [code for label, code in canonical if label in labels] + ["unclear"]
        by_key["shade_coverage"]["ordering"] = list(by_key["shade_coverage"]["allowed_values"])
        by_key["shade_coverage"]["legacy_labels"] = {code: label for label, code in canonical}
        by_key["shade_coverage"]["legacy_definitions"] = descriptions
    return modes


def enabled_modes(modes: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(mode) for mode in normalize_modes(modes) if mode.get("enabled")]


def _finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _is_missing(value: Any) -> bool:
    if value is None or value is pd.NA:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def validate_mode_value(mode: Mapping[str, Any], value: Any) -> Any:
    definition = normalize_mode_definition(mode)
    key = definition["key"]
    if _is_missing(value):
        if definition["required"]:
            raise AssessmentValidationError(f"A value is required for {key}.")
        return None
    if definition["multiple"]:
        if isinstance(value, str):
            values = [piece.strip() for piece in re.split(r"[;,|]", value) if piece.strip()]
        elif isinstance(value, Sequence):
            values = list(value)
        else:
            raise AssessmentValidationError(f"Mode {key} requires a list of values.")
        clean = []
        for item in values:
            item = str(item).strip()
            if definition["allowed_values"] and item not in definition["allowed_values"]:
                raise AssessmentValidationError(f"Invalid value for {key}: {item}")
            if item not in clean:
                clean.append(item)
        return clean
    value_type = definition["value_type"]
    if value_type == "categorical":
        clean_value = str(value).strip()
        if clean_value not in definition["allowed_values"]:
            raise AssessmentValidationError(f"Invalid value for {key}: {clean_value}")
        return clean_value
    if value_type == "boolean":
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
        raise AssessmentValidationError(f"Mode {key} requires a boolean value.")
    if value_type == "number":
        if not _finite_number(value):
            raise AssessmentValidationError(f"Mode {key} requires a finite number.")
        return float(value)
    return str(value).strip()


def validate_assessment_values(
    modes: Iterable[Mapping[str, Any]],
    values: Mapping[str, Any],
    *,
    enabled_only: bool = True,
) -> dict[str, Any]:
    definitions = mode_map(normalize_modes(modes))
    unknown = sorted(set(values) - set(definitions))
    if unknown:
        raise AssessmentValidationError(f"Unknown assessment mode(s): {', '.join(unknown)}")
    normalized: dict[str, Any] = {}
    for key, definition in definitions.items():
        if enabled_only and not definition["enabled"]:
            if key in values:
                raise AssessmentValidationError(f"Assessment mode {key} is disabled for this project.")
            continue
        clean = validate_mode_value(definition, values.get(key))
        if clean is not None:
            normalized[key] = clean
    return normalized


LEGACY_SHADE_COVERAGE_TO_MODE = {
    "no shade": "none",
    "limited": "limited",
    "limited shade": "limited",
    "significant": "significant",
    "significant shade": "significant",
    "needs review": "unclear",
    "unknown": "unclear",
}
LEGACY_SHADE_SOURCE_TO_MODE = {
    "natural": "natural",
    "purpose-built": "purpose_built",
    "purpose built": "purpose_built",
    "constructed": "purpose_built",
    "incidental": "incidental",
    "manmade": "incidental",
}


def assessment_values_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    raw = record.get("assessment_values")
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            try:
                raw = ast.literal_eval(raw)
            except (SyntaxError, ValueError):
                raw = {}
    values = dict(raw) if isinstance(raw, Mapping) else {}
    coverage = record.get("shade_coverage")
    if _is_missing(coverage):
        coverage = record.get("shading")
    if not _is_missing(coverage) and "shade_coverage" not in values:
        values["shade_coverage"] = LEGACY_SHADE_COVERAGE_TO_MODE.get(str(coverage).strip().lower(), str(coverage).strip())
    sources = record.get("shade_sources")
    if not _is_missing(sources) and "shade_source" not in values:
        pieces = sources if isinstance(sources, list) else re.split(r"[;,|]", str(sources))
        values["shade_source"] = [
            LEGACY_SHADE_SOURCE_TO_MODE.get(str(item).strip().lower(), str(item).strip())
            for item in pieces if str(item).strip()
        ]
    return values


def materialize_assessment_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Expose nested assessment values as ordinary columns for maps and exports."""
    if df is None:
        return pd.DataFrame()
    result = df.copy()
    records = result.to_dict("records")
    extracted = [assessment_values_from_record(record) for record in records]
    keys = sorted({key for values in extracted for key in values})
    for key in keys:
        existing = result[key].tolist() if key in result.columns else [None] * len(result)
        projected = [
            values[key] if key in values else existing[index]
            for index, values in enumerate(extracted)
        ]
        result[key] = pd.Series(projected, index=result.index, dtype=object)
    return result


def apply_assessment_values(record: Mapping[str, Any], values: Mapping[str, Any]) -> dict[str, Any]:
    updated = dict(record)
    merged = assessment_values_from_record(updated)
    merged.update(values)
    updated["assessment_values"] = merged
    for key, value in merged.items():
        updated[key] = "; ".join(map(str, value)) if isinstance(value, list) else value
    # Compatibility projections for the established shade maps and exports.
    coverage_labels = {"none": "No Shade", "limited": "Limited Shade", "significant": "Significant Shade", "unclear": "Needs Review"}
    source_labels = {"natural": "Natural", "purpose_built": "Purpose-built", "incidental": "Incidental", "unclear": ""}
    if "shade_coverage" in merged:
        updated["shade_coverage"] = coverage_labels.get(str(merged["shade_coverage"]), str(merged["shade_coverage"]))
        updated["shading"] = updated["shade_coverage"]
    if "shade_source" in merged:
        raw_sources = merged["shade_source"] if isinstance(merged["shade_source"], list) else [merged["shade_source"]]
        updated["shade_sources"] = "; ".join(filter(None, (source_labels.get(str(value), str(value)) for value in raw_sources)))
    return updated


def categorical_summary(df: pd.DataFrame, mode: Mapping[str, Any]) -> pd.DataFrame:
    definition = normalize_mode_definition(mode)
    materialized = materialize_assessment_columns(df)
    key = definition["key"]
    if key not in materialized.columns:
        return pd.DataFrame(columns=["value", "count", "percentage"])
    values: list[Any] = []
    for value in materialized[key].tolist():
        if definition["multiple"] and isinstance(value, (list, tuple, set)):
            values.extend(item for item in value if not _is_missing(item))
        elif not _is_missing(value):
            values.append(value)
    counts = Counter(map(str, values))
    denominator = len(materialized) if not definition["multiple"] else sum(counts.values())
    order = definition["ordering"] or definition["allowed_values"] or sorted(counts)
    rows = []
    for value in [*order, *sorted(set(counts) - set(order))]:
        count = counts.get(value, 0)
        rows.append({"value": value, "count": count, "percentage": round(count / denominator * 100, 1) if denominator else 0.0})
    return pd.DataFrame(rows)


def grouped_categorical_summary(
    df: pd.DataFrame,
    mode: Mapping[str, Any],
    group_by: str,
) -> pd.DataFrame:
    """Return mode counts/percentages by any preserved project attribute."""
    materialized = materialize_assessment_columns(df)
    if group_by not in materialized.columns:
        raise KeyError(f"Grouping field is not available: {group_by}")
    rows = []
    for group_value, group in materialized.groupby(group_by, dropna=False, sort=True):
        summary = categorical_summary(group, mode)
        for record in summary.to_dict("records"):
            rows.append({group_by: group_value, **record})
    return pd.DataFrame(rows, columns=[group_by, "value", "count", "percentage"])


def _normalized_mode_score(mode: Mapping[str, Any], value: Any) -> float | None:
    if _is_missing(value) or str(value).strip().lower() in {"unclear", "unknown", "needs review"}:
        return None
    scoring = mode.get("scoring") if isinstance(mode.get("scoring"), Mapping) else {}
    if isinstance(value, (list, tuple, set)):
        scores = [_normalized_mode_score(mode, item) for item in value]
        available = [score for score in scores if score is not None]
        return max(available) if available else None
    if str(value) in scoring and _finite_number(scoring[str(value)]):
        return float(scoring[str(value)])
    if mode.get("value_type") == "boolean":
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        normalized = str(value).strip().lower()
        if normalized in {"true", "yes", "1"}:
            return 1.0
        if normalized in {"false", "no", "0"}:
            return 0.0
        return None
    if _finite_number(value):
        return float(value)
    return None


def calculate_composite_score(
    assessment_values: Mapping[str, Any],
    modes: Iterable[Mapping[str, Any]],
    profile: Mapping[str, Any],
) -> float | None:
    """Calculate a reproducible 0–100 score from explicit mode mappings.

    Missing and unclear values are excluded by default.  They become zero only
    when the profile explicitly sets ``missing`` to ``zero``.
    """
    definitions = mode_map(normalize_modes(modes))
    weights = profile.get("weights") if isinstance(profile.get("weights"), Mapping) else {}
    missing_policy = str(profile.get("missing", "exclude")).lower()
    numerator = 0.0
    denominator = 0.0
    for key, raw_weight in weights.items():
        if key not in definitions or not _finite_number(raw_weight) or float(raw_weight) <= 0:
            continue
        weight = float(raw_weight)
        score = _normalized_mode_score(definitions[key], assessment_values.get(key))
        if score is None:
            if missing_policy == "zero":
                denominator += weight
            continue
        numerator += max(0.0, min(1.0, score)) * weight
        denominator += weight
    return round(numerator / denominator * 100, 1) if denominator else None


def add_composite_scores(
    df: pd.DataFrame,
    modes: Iterable[Mapping[str, Any]],
    profiles: Iterable[Mapping[str, Any]],
) -> pd.DataFrame:
    result = materialize_assessment_columns(df)
    for profile in profiles:
        if not profile.get("enabled"):
            continue
        key = str(profile.get("key", "score")).strip() or "score"
        result[f"{key}_score"] = [
            calculate_composite_score(assessment_values_from_record(row), modes, profile)
            for row in result.to_dict("records")
        ]
    return result


def filter_by_assessment_values(df: pd.DataFrame, filters: Mapping[str, Any]) -> pd.DataFrame:
    result = materialize_assessment_columns(df)
    for key, expected in filters.items():
        if key not in result.columns or expected in (None, "", []):
            continue
        selected = set(expected if isinstance(expected, (list, tuple, set)) else [expected])
        result = result[result[key].map(
            lambda value: bool(selected.intersection(value)) if isinstance(value, (list, tuple, set)) else value in selected
        )]
    return result.copy()


def reliability_for_mode(
    assessments: pd.DataFrame,
    mode: Mapping[str, Any],
) -> dict[str, Any]:
    """Compute percent agreement and Krippendorff alpha for one configured mode."""
    definition = normalize_mode_definition(mode)
    key = definition["key"]
    empty_result = {
        "mode": key,
        "measurement_level": definition["measurement_level"],
        "items": 0,
        "agreement": None,
        "krippendorff_alpha": None,
    }
    if assessments.empty:
        return empty_result

    rows: list[dict[str, Any]] = []
    for row_order, (_, row) in enumerate(assessments.iterrows()):
        if "submission_type" in assessments.columns and str(
            row.get("submission_type", "independent")
        ).strip().lower() != "independent":
            continue
        values = row.get("assessment_values", {})
        if isinstance(values, str):
            try:
                values = json.loads(values)
            except (TypeError, ValueError):
                values = {}
        value = values.get(key) if isinstance(values, Mapping) else None
        if _is_missing(value):
            continue
        if isinstance(value, (list, tuple, set)):
            value = json.dumps(sorted(map(str, value)), separators=(",", ":"))
        elif definition["measurement_level"] in {"interval", "ratio"}:
            if not _finite_number(value):
                continue
            value = float(value)
        else:
            value = str(value)
        reviewer_id = str(row.get("reviewer_id", "") or "").strip()
        if not reviewer_id:
            record_id = str(row.get("id", "") or "").strip()
            reviewer_id = f"anonymous:{record_id or row_order}"
        rows.append(
            {
                "stop_id": str(row.get("stop_id", "")),
                "reviewer_id": reviewer_id,
                "value": value,
                "created_at": pd.to_datetime(row.get("created_at"), errors="coerce", utc=True),
                "row_order": row_order,
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return empty_result
    frame = frame.sort_values(
        ["created_at", "row_order"], na_position="first", kind="stable"
    ).drop_duplicates(["stop_id", "reviewer_id"], keep="last")
    groups = [group["value"].tolist() for _, group in frame.groupby("stop_id") if len(group) >= 2]
    if not groups:
        return empty_result

    # Krippendorff's coincidence matrix gives every usable rating unit weight,
    # even when different stops have different numbers of reviewers.
    coincidences: Counter[tuple[Any, Any]] = Counter()
    for values in groups:
        for left_index, left in enumerate(values):
            for right_index, right in enumerate(values):
                if left_index != right_index:
                    coincidences[(left, right)] += 1.0 / (len(values) - 1)

    marginals: Counter[Any] = Counter()
    for (left, _), count in coincidences.items():
        marginals[left] += count
    total = float(sum(marginals.values()))
    categories = list(marginals)
    configured_order = [value for value in definition["ordering"] if value in marginals]
    ordinal_order = [*configured_order, *sorted(set(categories) - set(configured_order), key=str)]

    def distance(left: Any, right: Any) -> float:
        if left == right:
            return 0.0
        measurement = definition["measurement_level"]
        if measurement == "nominal":
            return 1.0
        if measurement == "ordinal":
            left_index = ordinal_order.index(left)
            right_index = ordinal_order.index(right)
            low, high = sorted((left_index, right_index))
            between = ordinal_order[low : high + 1]
            span = sum(marginals[value] for value in between)
            span -= (marginals[left] + marginals[right]) / 2.0
            return float(span**2)
        left_number, right_number = float(left), float(right)
        if measurement == "interval":
            return (left_number - right_number) ** 2
        denominator = left_number + right_number
        return ((left_number - right_number) / denominator) ** 2 if denominator else 1.0

    observed = (
        sum(count * distance(left, right) for (left, right), count in coincidences.items())
        / total
    )
    expected = 0.0
    if total > 1:
        for left in categories:
            for right in categories:
                expected_count = marginals[left] * (
                    marginals[right] - (1.0 if left == right else 0.0)
                ) / (total - 1.0)
                expected += expected_count * distance(left, right)
        expected /= total
    alpha = 1.0 - observed / expected if expected > 0 else (1.0 if observed == 0 else None)
    agreement = sum(
        count for (left, right), count in coincidences.items() if left == right
    ) / total
    return {
        "mode": key,
        "measurement_level": definition["measurement_level"],
        "items": len(groups),
        "agreement": round(agreement, 4),
        "krippendorff_alpha": round(alpha, 4) if alpha is not None else None,
    }
