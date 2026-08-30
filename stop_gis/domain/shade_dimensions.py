from __future__ import annotations

import copy
import re
from typing import Any


SHADE_COVERAGE_OPTIONS = ["No Shade", "Limited Shade", "Significant Shade"]
SHADE_SOURCE_OPTIONS = ["Natural", "Purpose-built", "Incidental"]

DEFAULT_TERMINOLOGY = [
    {
        "term": "Waiting Area",
        "operational_definition": (
            "The designated location where passengers would reasonably stand or sit while waiting to board the "
            "bus, including any bus stop pad, sidewalk immediately adjacent to the bus stop sign, or seating "
            "within a bus shelter. Grass, landscaping, roadway, bicycle lanes, and areas not reasonably intended "
            "for waiting are excluded."
        ),
    },
]


def normalize_terminology(terminology: Any) -> list[dict[str, str]]:
    source = DEFAULT_TERMINOLOGY if not isinstance(terminology, list) else terminology
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in source:
        if not isinstance(item, dict):
            continue
        raw_term = item.get("term", "")
        raw_definition = item.get("operational_definition", "")
        term = "" if raw_term is None or raw_term != raw_term else str(raw_term).strip()
        definition = (
            ""
            if raw_definition is None or raw_definition != raw_definition
            else str(raw_definition).strip()
        )
        if not term or term.casefold() in seen:
            continue
        seen.add(term.casefold())
        normalized.append({"term": term, "operational_definition": definition})
    return normalized


def normalize_source_taxonomy(taxonomy: Any) -> list[dict[str, str]]:
    configured: dict[str, tuple[str, str]] = {}
    if isinstance(taxonomy, list):
        for item in taxonomy:
            if not isinstance(item, dict):
                continue
            source = normalize_shade_source(item.get("code") or item.get("shade_source", ""))
            display_label = str(item.get("shade_source", "") or "").strip()
            raw_definition = item.get("operational_definition", "")
            definition = (
                ""
                if raw_definition is None or raw_definition != raw_definition
                else str(raw_definition).strip()
            )
            if source:
                configured[source] = (display_label or source, definition)
    normalized = []
    used_labels: set[str] = set()
    for default in SHADE_SOURCE_TAXONOMY:
        code = default["shade_source"]
        display_label, definition = configured.get(
            code,
            (code, default["operational_definition"]),
        )
        if display_label.casefold() in used_labels:
            display_label = code
        used_labels.add(display_label.casefold())
        normalized.append(
            {
                "code": code,
                "shade_source": display_label,
                "operational_definition": definition or default["operational_definition"],
            }
        )
    return normalized


def normalize_coverage_display_taxonomy(
    taxonomy: Any,
    canonical_taxonomy: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    configured: dict[str, tuple[str, str]] = {}
    if isinstance(taxonomy, list):
        for item in taxonomy:
            if not isinstance(item, dict):
                continue
            code = normalize_shade_coverage(item.get("code") or item.get("shade_coverage", ""), "")
            display_label = str(item.get("shade_coverage", "") or "").strip()
            definition = str(item.get("operational_definition", "") or "").strip()
            if code in SHADE_COVERAGE_OPTIONS:
                configured[code] = (display_label or code, definition)
    canonical_definitions = {
        item.get("name"): str(item.get("description", "") or "").strip()
        for item in normalize_coverage_taxonomy(canonical_taxonomy)
        if item.get("name") in SHADE_COVERAGE_OPTIONS
    }
    normalized = []
    used_labels: set[str] = set()
    for code in SHADE_COVERAGE_OPTIONS:
        default_definition = canonical_definitions.get(code, "")
        display_label, definition = configured.get(code, (code, default_definition))
        if display_label.casefold() in used_labels:
            display_label = code
        used_labels.add(display_label.casefold())
        normalized.append(
            {
                "code": code,
                "shade_coverage": display_label,
                "operational_definition": definition or default_definition,
            }
        )
    return normalized

SHADE_COVERAGE_TAXONOMY = [
    {
        "shade_coverage": "No Shade",
        "operational_definition": "No shade visibly reaches the waiting area.",
    },
    {
        "shade_coverage": "Limited Shade",
        "operational_definition": "Shade visibly covers part of the waiting area, but not most of it.",
    },
    {
        "shade_coverage": "Significant Shade",
        "operational_definition": "Shade visibly covers most of the waiting area or seating area.",
    },
]

SHADE_SOURCE_TAXONOMY = [
    {
        "shade_source": "Natural",
        "operational_definition": "Trees, palms, hedges, or other vegetation visibly shade the waiting area.",
    },
    {
        "shade_source": "Purpose-built",
        "operational_definition": (
            "A designated, purpose-built bus shelter, awning, canopy, overhang, or similar passenger shelter "
            "visibly shades the waiting area."
        ),
    },
    {
        "shade_source": "Incidental",
        "operational_definition": "A nearby building or other non-shelter built feature visibly shades the waiting area.",
    },
]

DEFAULT_COVERAGE_TAXONOMY = [
    {
        "name": "No Shade",
        "description": "No shade visibly reaches the waiting area.",
        "color": "#dc143c",
        "sort_order": 1,
    },
    {
        "name": "Limited Shade",
        "description": "Shade visibly covers part of the waiting area, but not most of it.",
        "color": "#d69e2e",
        "sort_order": 2,
    },
    {
        "name": "Significant Shade",
        "description": "Shade visibly covers most of the waiting area or seating area.",
        "color": "#228b22",
        "sort_order": 3,
    },
    {
        "name": "Needs Review",
        "description": "The stop needs imagery, review, or disagreement resolution.",
        "color": "#808080",
        "sort_order": 4,
    },
]

_COVERAGE_ALIASES = {
    "no shade": "No Shade",
    "limited": "Limited Shade",
    "limited shade": "Limited Shade",
    "limited natural shade": "Limited Shade",
    "significant": "Significant Shade",
    "significant shade": "Significant Shade",
    "significant natural shade": "Significant Shade",
    "needs review": "Needs Review",
    "unknown": "Needs Review",
}

_SOURCE_ALIASES = {
    "natural": "Natural",
    "natural shade": "Natural",
    "tree": "Natural",
    "trees": "Natural",
    "vegetation": "Natural",
    "purpose-built": "Purpose-built",
    "purpose built": "Purpose-built",
    "purpose-built shade": "Purpose-built",
    "constructed": "Purpose-built",
    "constructed shade": "Purpose-built",
    "intentional built": "Purpose-built",
    "intentional built shade": "Purpose-built",
    "intentional constructed": "Purpose-built",
    "shelter": "Purpose-built",
    "canopy": "Purpose-built",
    "incidental": "Incidental",
    "incidental shade": "Incidental",
    "manmade": "Incidental",
    "manmade shade": "Incidental",
    "incidental built": "Incidental",
    "incidental built shade": "Incidental",
    "building": "Incidental",
}

_LEGACY_COVERAGE_DESCRIPTIONS = {
    "Limited Shade": {
        "Vegetation shades part of the waiting area, but not most of it.",
        "Shade visibly reaches part of the waiting area, but not most of it.",
        "Shade visibly reaches part of the waiting area, but does not cover most of it.",
    },
    "Significant Shade": {
        "Vegetation visibly covers most of the waiting area or seating area.",
    },
}


def normalize_shade_coverage(value: Any, fallback: str = "Needs Review") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return _COVERAGE_ALIASES.get(text.lower(), fallback)


def normalize_shade_source(value: Any) -> str:
    return _SOURCE_ALIASES.get(str(value or "").strip().lower(), "")


def split_shade_sources(value: Any) -> list[str]:
    sources: list[str] = []
    for part in re.split(r"[;,|]", str(value or "")):
        normalized = normalize_shade_source(part)
        if normalized and normalized not in sources:
            sources.append(normalized)
    return sources


def infer_sources_from_legacy_category(value: Any) -> list[str]:
    text = str(value or "").strip().lower()
    sources: list[str] = []
    for candidate in SHADE_SOURCE_OPTIONS:
        normalized = normalize_shade_source(candidate)
        aliases = [alias for alias, target in _SOURCE_ALIASES.items() if target == normalized]
        if any(alias in text for alias in aliases) and normalized not in sources:
            sources.append(normalized)
    return sources


def normalize_coverage_taxonomy(taxonomy: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    configured: dict[str, tuple[int, dict[str, Any]]] = {}
    for item in taxonomy or []:
        original_name = str(item.get("name", "")).strip()
        normalized_name = normalize_shade_coverage(original_name, "")
        if not normalized_name:
            continue
        priority = 0 if original_name == normalized_name else 1
        existing = configured.get(normalized_name)
        if existing is None or priority < existing[0]:
            configured[normalized_name] = (priority, dict(item))

    normalized_taxonomy = copy.deepcopy(DEFAULT_COVERAGE_TAXONOMY)
    for default in normalized_taxonomy:
        existing = configured.get(default["name"])
        if not existing:
            continue
        configured_item = existing[1]
        for key in ["description", "color"]:
            configured_value = str(configured_item.get(key, "")).strip()
            if configured_value and not (
                key == "description"
                and configured_value in _LEGACY_COVERAGE_DESCRIPTIONS.get(default["name"], set())
            ):
                default[key] = configured_item[key]
    return normalized_taxonomy

