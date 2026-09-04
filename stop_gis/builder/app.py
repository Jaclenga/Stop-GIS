import html
import json
import math
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pydeck as pdk
import streamlit as st

# Keep ordinary strings as Python objects. Streamlit serializes every dataframe
# through PyArrow; pandas extension-string arrays have caused process-fatal
# native crashes at that boundary in CI.
pd.options.future.infer_string = False

from stop_gis import public_app as published_app
from stop_gis.features import AGENT_WORKFLOWS_ENABLED, PHOTO_WORKFLOWS_ENABLED
from stop_gis.public_voting import normalize_voting_config
from stop_gis.persistence.store import (
    DuplicateProjectNameError,
    ProjectConflictError,
    add_shade_label,
    create_project,
    database_status,
    delete_project,
    init_database,
    list_images,
    list_assessments,
    list_review_history,
    list_shade_labels,
    list_projects,
    load_project_bundle,
    mark_project_store_initialized,
    project_store_initialized,
    save_project_bundle,
    update_project_details,
)
from stop_gis.builder.imports import (
    REQUIRED_STOP_FIELDS,
    OPTIONAL_FIELDS,
    apply_field_mapping,
    calculate_priority_scores,
    clean_import_key,
    detect_zip_import_format,
    fetch_api_bytes,
    format_bytes,
    hex_to_rgb,
    import_stop_dataset,
    max_api_bytes,
    max_upload_bytes,
    max_zip_members,
    max_zip_uncompressed_bytes,
    normalize_category,
    normalize_hex_color,
    normalize_review_status,
    parse_api_response,
    parse_geojson_bytes,
    parse_geojson_overlay_bytes,
    parse_gtfs_zip,
    parse_shapefile_overlay_zip,
    parse_shapefile_zip,
    prepare_stop_dataset,
    read_csv_bytes,
    render_mapped_import_controls,
    suggest_source_column,
    timestamp_with_timezone,
    validate_api_url,
    validate_zip_bytes,
)
from stop_gis.builder.labels import (
    agreement_overview_metrics,
    agreement_metric_summary,
    average_pairwise_cohen_kappa,
    category_count_matrix,
    clean_label_values,
    cohen_kappa_for_pair,
    disagreement_queue_table,
    fleiss_kappa,
    format_metric_value,
    krippendorff_alpha_nominal,
    label_rater_key,
    label_source_code,
    latest_labels_by_rater,
    majority_label_table,
    raw_label_summary,
    render_agreement_metrics,
    review_queue_label,
    review_queue_table,
    split_list_field,
    stop_picker_label,
    stop_review_snapshot,
    taxonomy_names,
)
from stop_gis.builder.visuals import (
    CATEGORICAL_MAP_FILTERS,
    CHART_AGGREGATIONS,
    CHART_TYPES,
    COLOR_MODE_FIELDS,
    COLOR_PALETTE,
    DEFAULT_CUSTOM_CHART,
    DEFAULT_DISPLAY_COLUMNS,
    DEFAULT_VISUALIZATION as BASE_DEFAULT_VISUALIZATION,
    DESTINATION_FILTER_COLUMNS,
    FIELD_LABELS,
    GIS_OVERLAY_CATEGORIES,
    MAP_STYLES,
    MARKER_SHAPES,
    MAX_CUSTOM_CHARTS,
    METRIC_REQUIREMENTS,
    NUMERIC_MAP_FILTERS,
    OVERLAY_REQUIREMENTS,
    RECORD_COUNT_FIELD,
    SHADE_PALETTES,
    add_marker_icons,
    build_custom_chart_data,
    build_deck_chart,
    build_gis_overlay_layers,
    build_tooltip_text,
    calculate_view_state,
    clean_gis_overlays,
    clean_selected_options,
    color_dataset,
    color_for_priority,
    display_label,
    ensure_custom_chart_defaults,
    ensure_field_color_map,
    field_values_for_colors,
    get_active_data_columns,
    get_available_metric_cards,
    get_available_overlays,
    get_chart_column_options,
    get_color_options,
    get_custom_charts,
    get_display_column_options,
    get_selected_display_columns,
    get_taxonomy_color_map,
    has_all_column_data,
    has_any_column_data,
    has_column_data,
    marker_icon_data_uri,
    migrate_legacy_analytics_config,
    render_custom_chart,
    render_custom_charts,
    rgba_from_hex,
    selected_dashboard_sections,
)
from stop_gis.deploy import (
    DeploymentBundleSpec,
    build_deployment_bundle,
    deploy_launcher_script,
    deploy_readme,
    deploy_script,
    github_new_repo_url,
    powershell_literal,
    public_voting_source,
    published_app_source,
    slugify_repo_name,
    streamlit_entrypoint_path,
)
from stop_gis.deploy.service import (
    DEFAULT_DEPLOY_COMMIT_MESSAGE,
)
from stop_gis.domain.data_quality import evaluate_data_quality
from stop_gis.domain.shade_dimensions import (
    DEFAULT_TERMINOLOGY as CORE_DEFAULT_TERMINOLOGY,
    DEFAULT_COVERAGE_TAXONOMY,
    SHADE_COVERAGE_OPTIONS as CORE_SHADE_COVERAGE_OPTIONS,
    SHADE_COVERAGE_TAXONOMY as CORE_SHADE_COVERAGE_TAXONOMY,
    SHADE_SOURCE_OPTIONS as CORE_SHADE_SOURCE_OPTIONS,
    SHADE_SOURCE_TAXONOMY as CORE_SHADE_SOURCE_TAXONOMY,
    normalize_coverage_taxonomy,
    normalize_coverage_display_taxonomy,
    normalize_source_taxonomy,
    normalize_terminology,
)
from stop_gis.assessment_modes import (
    assessment_codebook,
    normalize_modes,
)


APP_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = APP_DIR / "data" / "pittsburgh_bench_inventory"
DATA_PATH = DEFAULT_DATA_DIR / "pittsburgh_bus_stops_stop_gis_import.csv"
SEED_SUMMARY_PATH = DEFAULT_DATA_DIR / "build_summary.json"
APP_TITLE = "Stop-GIS Builder"
VISUAL_MAP_HEIGHT = 500
AUTOSAVE_STATUS_KEY = "workspace_autosave_status"
DEFAULT_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_API_BYTES = 15 * 1024 * 1024
DEFAULT_MAX_ZIP_MEMBERS = 256
DEFAULT_MAX_ZIP_MEMBER_BYTES = 80 * 1024 * 1024
DEFAULT_MAX_ZIP_UNCOMPRESSED_BYTES = 150 * 1024 * 1024
API_FETCH_TIMEOUT_SECONDS = 30


DEFAULT_PROJECT = {
    "name": "Pittsburgh Bus Stop Bench Inventory",
    "agency": "Pittsburgh Regional Transit (PRT)",
    "region": "Pittsburgh, Pennsylvania",
    "description": (
        "An unreviewed starter inventory for assessing passenger benches and "
        "other seating at City of Pittsburgh bus stops."
    ),
    "owners": "Stop-GIS contributors and Pittsburgh bench-study reviewers",
    "visibility": "Public",
    "dataset_version": "0.1.0",
    "methodology_version": "0.1.0",
    "source_name": "PRT current stops with provisional OpenStreetMap evidence",
    "source_license": "PRT Developer License Agreement; OpenStreetMap ODbL 1.0",
    "source_url": (
        "https://services3.arcgis.com/544gNI3xxlFIWuTc/arcgis/rest/services/"
        "Transit_Stops_%28system%29/FeatureServer/0"
    ),
}

DEFAULT_TAXONOMY = [dict(item) for item in DEFAULT_COVERAGE_TAXONOMY]
DEFAULT_TERMINOLOGY = [dict(item) for item in CORE_DEFAULT_TERMINOLOGY]
SHADE_SOURCE_TAXONOMY = [dict(item) for item in CORE_SHADE_SOURCE_TAXONOMY]
SHADE_COVERAGE_TAXONOMY = [dict(item) for item in CORE_SHADE_COVERAGE_TAXONOMY]

DEFAULT_METHODOLOGY = {
    "title": "Pittsburgh Bus Stop Bench Inventory",
    "summary": "Auditing passenger bench and seating availability at City of Pittsburgh bus stops.",
    "purpose": (
        "This starter project supports a reproducible inventory of passenger benches and seating at current "
        "Pittsburgh Regional Transit bus stops within the City of Pittsburgh. Official PRT stop and shelter "
        "records establish the stop frame, while OpenStreetMap tags provide provisional review leads. None of "
        "those source fields is treated as a verified bench assessment."
    ),
    "assessment_method": (
        "Reviewers assess one stop at a time and record bench presence, bench condition, seating form, and "
        "informal seating. Count only furniture clearly intended to serve the passenger waiting area. Use "
        "unclear when imagery or field evidence is missing, obstructed, stale, or ambiguous. Independent "
        "submissions remain immutable when an administrator later adjudicates a current value."
    ),
    "shade_method": "",
    "data_sources": (
        "- Pittsburgh Regional Transit current stops: authoritative stop frame and service attributes\n"
        "- Pittsburgh Regional Transit current shelter locations: provisional shelter evidence\n"
        "- OpenStreetMap bus-stop tags: provisional, contributor-maintained bench and seating evidence\n"
        "- Independent field, imagery, or expert assessments collected in Stop-GIS"
    ),
    "contributors": "Project team, reviewers, and community contributors",
    "citation": (
        "Dataset release:\n"
        "    Stop-GIS contributors. (2026). Pittsburgh bus-stop bench-inventory starter dataset "
        "(Version 0.1.0) [Data set]. Stop-GIS.\n\n"
        "Preserve the packaged DATA_LICENSE.md notice when redistributing the source data."
    ),
    "bibliography": (
        "Works referenced:\n"
        "    Pittsburgh Regional Transit. (2026). PRT Stops - Current (full system) "
        "[Feature layer; item a29f37608eb34c3895332ff99eea9b17]. Accessed 2026-09-04. "
        "https://www.arcgis.com/home/item.html?id=a29f37608eb34c3895332ff99eea9b17\n"
        "    Pittsburgh Regional Transit. (2026). PRT Stop Amenities - Current "
        "[Feature layer; item 73d1faab8d3441babcd3463ef6987559]. Accessed 2026-09-04. "
        "https://www.arcgis.com/home/item.html?id=73d1faab8d3441babcd3463ef6987559\n"
        "    OpenStreetMap contributors. (2026). OpenStreetMap [Database]. "
        "OpenStreetMap Foundation. ODbL 1.0. Accessed 2026-09-04. "
        "https://www.openstreetmap.org/copyright\n\n"
        "Terms: https://www.rideprt.org/business-center/developer-resources/"
        "developer-license-agreement/"
    ),
    "limitations": (
        "This is an unreviewed starter dataset, not a verified bench census. PRT has no bench field, a shelter "
        "listing does not imply bench presence, and OpenStreetMap tags may be incomplete or stale. Every stop "
        "requires human review before its bench or seating status is treated as an observation."
    ),
    "release_history": "- 0.1.0: Unreviewed Pittsburgh bench-inventory starter project",
    "terminology": [
        {
            "term": "Passenger waiting area",
            "operational_definition": "The immediate area at the stop where passengers reasonably wait or sit before boarding.",
        },
        {
            "term": "Bench",
            "operational_definition": "Fixed or purpose-placed seating clearly intended to serve waiting transit passengers.",
        },
    ],
    "shade_source_taxonomy": [dict(item) for item in SHADE_SOURCE_TAXONOMY],
    "shade_coverage_taxonomy": [dict(item) for item in SHADE_COVERAGE_TAXONOMY],
}

DEFAULT_ASSESSMENT_MODES = normalize_modes(
    [
        {
            "key": "bench_presence",
            "label": "Bench presence",
            "description": "Whether a passenger bench is present at the stop.",
            "operational_definition": "Count only a bench clearly intended to serve the passenger waiting area.",
            "value_type": "categorical",
            "allowed_values": ["present", "absent", "unclear"],
            "ordering": [],
            "measurement_level": "nominal",
            "enabled": True,
            "sort_order": 1,
        },
        {
            "key": "bench_condition",
            "label": "Bench condition",
            "description": "Observed usability of a passenger bench.",
            "operational_definition": "Assess condition only when a qualifying passenger bench is present.",
            "value_type": "categorical",
            "allowed_values": ["usable", "damaged", "unusable", "unclear", "not_applicable"],
            "ordering": [],
            "measurement_level": "nominal",
            "enabled": True,
            "sort_order": 2,
        },
        {
            "key": "seating_form",
            "label": "Seating form",
            "description": "The primary form of passenger seating at the stop.",
            "operational_definition": "Distinguish traditional or shelter-integrated benches from other formal seating types.",
            "value_type": "categorical",
            "allowed_values": ["traditional_bench", "shelter_integrated_bench", "simme_seat", "individual_seat", "lean_rail", "other", "unclear"],
            "ordering": [],
            "measurement_level": "nominal",
            "enabled": True,
            "sort_order": 3,
        },
        {
            "key": "informal_seating",
            "label": "Informal/DIY seating",
            "description": "Whether loose or improvised passenger seating is present.",
            "operational_definition": "Record chairs, crates, or other improvised objects apparently used by waiting passengers.",
            "value_type": "categorical",
            "allowed_values": ["present", "absent", "unclear"],
            "ordering": [],
            "measurement_level": "nominal",
            "enabled": True,
            "sort_order": 4,
        },
    ]
)
DEFAULT_VISUALIZATION = json.loads(json.dumps(BASE_DEFAULT_VISUALIZATION))
DEFAULT_VISUALIZATION.update(
    {
        "color_by": "Column: Bench presence",
        "display_columns": [
            "stop_id",
            "stop_name",
            "routes",
            "bench_presence",
            "bench_condition",
            "seating_form",
            "informal_seating",
            "review_status",
        ],
        "metric_cards": ["Review status"],
        "priority_weights": {"ridership": 0.0, "low_shade": 0.0},
        "show_legend": True,
        "cluster_dense_stops": False,
        "pittsburgh_bench_map_version": 1,
        "field_color_maps": {
            "bench_presence": {
                "present": "#16803c",
                "absent": "#dc2626",
                "Unknown": "#94a3b8",
                "unclear": "#f59e0b",
            }
        },
        "custom_charts": [
            {
                "title": "Bench Presence",
                "x": "bench_presence",
                "y": RECORD_COUNT_FIELD,
                "aggregation": "Count",
                "chart_type": "Bar",
            },
            {
                "title": "Bench Condition",
                "x": "bench_condition",
                "y": RECORD_COUNT_FIELD,
                "aggregation": "Count",
                "chart_type": "Bar",
            },
            {
                "title": "Seating Form",
                "x": "seating_form",
                "y": RECORD_COUNT_FIELD,
                "aggregation": "Count",
                "chart_type": "Bar",
            },
        ],
    }
)
DEFAULT_VISUALIZATION["voting"]["enabled"] = False
DEFAULT_SCORING: list[dict[str, Any]] = []

REVIEW_STATUS_COLORS = {
    "Unlabeled": [148, 163, 184],
    "Needs Review": [234, 179, 8],
    "Crowd Reviewed": [45, 212, 191],
    "Expert Reviewed": [59, 130, 246],
    "Accepted": [34, 197, 94],
    "Disputed": [239, 68, 68],
    "Archived": [107, 114, 128],
}

REVIEW_QUEUE_DEFAULT_STATUSES = ["Needs Review", "Disputed", "Unlabeled"]
REVIEW_ACTION_OPTIONS = [
    "Accept current label",
    "Expert override",
    "Mark disputed",
    "Resolve dispute",
    "Archive",
]
REVIEW_ACTION_STATUS_DEFAULTS = {
    "Accept current label": "Accepted",
    "Expert override": "Expert Reviewed",
    "Mark disputed": "Disputed",
    "Resolve dispute": "Accepted",
    "Archive": "Archived",
}

LABEL_SOURCE_OPTIONS = [
    "Expert review",
    "Crowdsourcing",
    "Field audit",
    "Imported dataset",
    "Manual review",
]
if AGENT_WORKFLOWS_ENABLED:
    LABEL_SOURCE_OPTIONS.insert(-1, "LLM-assisted suggestion")

LABELER_ROLE_OPTIONS = [
    "Reviewer",
    "Contributor",
    "Project Admin",
    "Expert",
    "Public",
]
if AGENT_WORKFLOWS_ENABLED:
    LABELER_ROLE_OPTIONS.append("Model")

SHADE_SOURCE_OPTIONS = list(CORE_SHADE_SOURCE_OPTIONS)

SHADE_COVERAGE_OPTIONS = list(CORE_SHADE_COVERAGE_OPTIONS)


def rgb_to_hex(value: list[int]) -> str:
    rgb = [max(0, min(255, int(channel))) for channel in value[:3]]
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def load_seed_dataset(
    taxonomy: list[dict[str, Any]], project: dict[str, Any]
) -> pd.DataFrame:
    if not DATA_PATH.exists():
        return pd.DataFrame(columns=REQUIRED_STOP_FIELDS)
    stops = pd.read_csv(DATA_PATH, dtype=str, keep_default_na=False)
    stops = prepare_stop_dataset(stops, project, taxonomy)
    stops = add_pittsburgh_bench_prefills(stops)
    stops["assessment_values"] = [{} for _ in range(len(stops))]
    stops["source_evidence_notice"] = (
        "Unreviewed source evidence only; verify bench and seating conditions before use."
    )
    return stops


def add_pittsburgh_bench_prefills(stops: pd.DataFrame) -> pd.DataFrame:
    """Populate editable bench-mode inputs without changing review status."""
    prepared = stops.copy()
    derived = pd.Series("", index=prepared.index, dtype=object)
    if "osm_bench_tag" in prepared.columns:
        osm_tags = (
            prepared["osm_bench_tag"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.lower()
        )
        derived = osm_tags.map({"yes": "present", "no": "absent"}).fillna("")
    source_bench = (
        prepared["bench"].fillna("").astype(str).str.strip().str.lower()
        if "bench" in prepared.columns
        else derived.copy()
    )
    source_bench = source_bench.where(
        source_bench.isin({"present", "absent", "unclear"}), ""
    )
    prepared["bench"] = source_bench.where(source_bench != "", derived)
    existing_presence = (
        prepared["bench_presence"].fillna("").astype(str).str.strip().str.lower()
        if "bench_presence" in prepared.columns
        else pd.Series("", index=prepared.index, dtype=object)
    )
    valid_presence = existing_presence.where(
        existing_presence.isin({"present", "absent", "unclear"}), ""
    )
    prepared["bench_presence"] = valid_presence.where(
        valid_presence != "", prepared["bench"]
    )
    return prepared


def empty_stop_dataset() -> pd.DataFrame:
    return pd.DataFrame(
        columns=REQUIRED_STOP_FIELDS + OPTIONAL_FIELDS + ["priority_score"]
    )


def with_default_project_values(project: dict[str, Any]) -> dict[str, Any]:
    merged = DEFAULT_PROJECT.copy()
    merged.update(project or {})
    return merged


def with_default_methodology_values(methodology: dict[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(DEFAULT_METHODOLOGY))
    merged.update(methodology or {})
    merged["terminology"] = normalize_terminology(merged.get("terminology"))
    merged["shade_source_taxonomy"] = normalize_source_taxonomy(
        merged.get("shade_source_taxonomy")
    )
    merged["shade_coverage_taxonomy"] = normalize_coverage_display_taxonomy(
        merged.get("shade_coverage_taxonomy")
    )
    return merged


def with_default_visualization_values(visualization: dict[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(DEFAULT_VISUALIZATION))
    merged.update(visualization or {})
    return merged


def normalized_visualization_values(
    visualization: dict[str, Any], taxonomy: list[dict[str, Any]]
) -> dict[str, Any]:
    visualization = migrate_legacy_analytics_config(visualization)
    visualization = with_default_visualization_values(
        json.loads(json.dumps(visualization or {}, default=str))
    )
    if "custom_charts" not in visualization and isinstance(
        visualization.get("custom_chart"), dict
    ):
        visualization["custom_charts"] = [visualization["custom_chart"]]
    for key, value in DEFAULT_VISUALIZATION.items():
        visualization.setdefault(key, json.loads(json.dumps(value)))
    metric_cards = visualization.setdefault("metric_cards", [])
    for label in DEFAULT_VISUALIZATION["metric_cards"]:
        if label not in metric_cards:
            metric_cards.append(label)

    review_colors = visualization.setdefault("review_status_colors", {})
    for status, color in REVIEW_STATUS_COLORS.items():
        review_colors.setdefault(status, rgb_to_hex(color))

    priority_colors = visualization.setdefault("priority_colors", {})
    for key, color in DEFAULT_VISUALIZATION["priority_colors"].items():
        priority_colors.setdefault(key, color)

    clean_gis_overlays(visualization)
    visualization["voting"] = normalize_voting_config(
        visualization.get("voting"),
        taxonomy,
    )
    return visualization


def apply_pittsburgh_bench_map_defaults(
    visualization: dict[str, Any], project: dict[str, Any]
) -> dict[str, Any]:
    if (
        project.get("name") != "Pittsburgh Bus Stop Bench Inventory"
        or "pittsburgh_bench_map_version" in visualization
    ):
        return visualization
    visualization.update(
        {
            "color_by": "Column: Bench presence",
            "show_legend": True,
            "cluster_dense_stops": False,
            "pittsburgh_bench_map_version": 1,
        }
    )
    visualization.setdefault("field_color_maps", {})["bench_presence"] = {
        "present": "#16803c",
        "absent": "#dc2626",
        "Unknown": "#94a3b8",
        "unclear": "#f59e0b",
    }
    return visualization


def ensure_visualization_defaults() -> None:
    current = st.session_state["visualization"]
    current = apply_pittsburgh_bench_map_defaults(
        current, st.session_state.get("project", {})
    )
    st.session_state["visualization"] = normalized_visualization_values(
        current,
        st.session_state.get("taxonomy", []),
    )


def create_seed_project() -> str:
    project = DEFAULT_PROJECT.copy()
    taxonomy = [item.copy() for item in DEFAULT_TAXONOMY]
    methodology = DEFAULT_METHODOLOGY.copy()
    methodology["assessment_modes"] = json.loads(json.dumps(DEFAULT_ASSESSMENT_MODES))
    visualization = json.loads(json.dumps(DEFAULT_VISUALIZATION))
    stops = load_seed_dataset(taxonomy, project)
    test_seed_limit = (
        os.environ.get("STOP_GIS_TEST_MAX_SEED_ROWS")
        or os.environ.get("SHADE_GIS_TEST_MAX_SEED_ROWS", "")
    ).strip()
    if test_seed_limit:
        try:
            limit = max(int(test_seed_limit), 1)
        except ValueError:
            limit = len(stops)
        stops = stops.head(limit).copy()
    import_log = [
        {
            "source": "Stop-GIS Pittsburgh bench-inventory starter dataset",
            "format": "CSV",
            "rows": len(stops),
            "imported_at": timestamp_with_timezone(),
            "source_url": DEFAULT_PROJECT["source_url"],
            "source_license": DEFAULT_PROJECT["source_license"],
        }
    ]
    return create_project(
        project,
        taxonomy,
        methodology,
        visualization,
        stops,
        import_log,
        assessment_modes=DEFAULT_ASSESSMENT_MODES,
        scoring=DEFAULT_SCORING,
    )


def load_project_into_session(project_id: str) -> None:
    bundle = load_project_bundle(project_id)
    project = with_default_project_values(bundle["project"])
    taxonomy = normalize_coverage_taxonomy(bundle["taxonomy"] or DEFAULT_TAXONOMY)
    methodology = with_default_methodology_values(bundle["methodology"])
    stored_visualization = apply_pittsburgh_bench_map_defaults(
        dict(bundle["visualization"] or {}), project
    )
    visualization = normalized_visualization_values(stored_visualization, taxonomy)
    stops = bundle["stops"]
    if stops.empty:
        stops = empty_stop_dataset()
    else:
        stops = prepare_stop_dataset(stops, project, taxonomy)
        if project.get("name") == "Pittsburgh Bus Stop Bench Inventory":
            stops = add_pittsburgh_bench_prefills(stops)

    for key in list(st.session_state):
        if key.startswith("api_import_") or key.startswith("manual_entry_"):
            st.session_state.pop(key, None)
    for key in ("manual_import_entries", "manual_import_source"):
        st.session_state.pop(key, None)

    st.session_state["active_project_id"] = project_id
    st.session_state["loaded_project_id"] = project_id
    st.session_state["project"] = project
    st.session_state["taxonomy"] = taxonomy
    st.session_state["methodology"] = methodology
    st.session_state["visualization"] = visualization
    st.session_state["assessment_modes"] = normalize_modes(
        bundle.get("assessment_modes")
    )
    st.session_state["scoring"] = json.loads(
        json.dumps(bundle.get("scoring") or DEFAULT_SCORING)
    )
    st.session_state["stops"] = stops
    st.session_state["import_log"] = bundle["import_log"]
    st.session_state.pop("deploy_page_settings_project_id", None)
    ensure_visualization_defaults()


def save_active_project_to_store(review_event: dict[str, Any] | None = None) -> bool:
    if (
        os.environ.get("STOP_GIS_TEST_DISABLE_AUTO_SAVE")
        or os.environ.get("SHADE_GIS_TEST_DISABLE_AUTO_SAVE", "")
    ).strip() == "1":
        return True
    project_id = st.session_state.get("active_project_id")
    if not project_id:
        return True
    st.session_state[AUTOSAVE_STATUS_KEY] = {
        "state": "saving",
        "message": "Saving changes…",
    }
    try:
        event_id = save_project_bundle(
            project_id,
            st.session_state.get("project", DEFAULT_PROJECT.copy()),
            st.session_state.get(
                "taxonomy", [item.copy() for item in DEFAULT_TAXONOMY]
            ),
            st.session_state.get("methodology", DEFAULT_METHODOLOGY.copy()),
            st.session_state.get(
                "visualization", json.loads(json.dumps(DEFAULT_VISUALIZATION))
            ),
            st.session_state.get("stops", empty_stop_dataset()),
            st.session_state.get("import_log", []),
            review_event=review_event,
            assessment_modes=st.session_state.get(
                "assessment_modes", DEFAULT_ASSESSMENT_MODES
            ),
            scoring=st.session_state.get("scoring", DEFAULT_SCORING),
        )
        if review_event is not None:
            review_event["_saved_event_id"] = event_id
        saved_at = datetime.now().astimezone().strftime("%I:%M %p").lstrip("0")
        st.session_state[AUTOSAVE_STATUS_KEY] = {
            "state": "saved",
            "message": f"All changes saved · {saved_at}",
        }
        return True
    except DuplicateProjectNameError as error:
        st.session_state[AUTOSAVE_STATUS_KEY] = {
            "state": "failed",
            "message": "Autosave paused — choose a unique project name.",
        }
        st.error(str(error))
        return False
    except ProjectConflictError as error:
        st.session_state[AUTOSAVE_STATUS_KEY] = {
            "state": "failed",
            "message": "Autosave paused — reload the latest project before editing.",
        }
        st.error(str(error))
        return False
    except (OSError, sqlite3.Error):
        st.session_state[AUTOSAVE_STATUS_KEY] = {
            "state": "failed",
            "message": "Autosave failed — your latest changes may not be stored.",
        }
        st.error(
            "Autosave failed. Your latest changes may not be stored. Check the project database and try again."
        )
        return False


def render_autosave_status() -> None:
    status = st.session_state.get(AUTOSAVE_STATUS_KEY)
    if not status or not st.session_state.get("active_project_id"):
        return
    state = str(status.get("state") or "saved")
    message = html.escape(str(status.get("message") or "Autosave is on."))
    st.markdown(
        f'<div class="autosave-status {state}" role="status" aria-live="polite">{message}</div>',
        unsafe_allow_html=True,
    )


def create_blank_project(name: str) -> str:
    project = DEFAULT_PROJECT.copy()
    project.update(
        {
            "name": name.strip() or "Untitled Stop Audit",
            "agency": "",
            "region": "",
            "description": "A reproducible audit of bus-stop infrastructure and passenger experience.",
            "dataset_version": "draft",
            "methodology_version": "draft",
            "source_name": "",
            "source_license": "",
            "source_url": "",
        }
    )
    methodology = DEFAULT_METHODOLOGY.copy()
    methodology["assessment_modes"] = json.loads(json.dumps(DEFAULT_ASSESSMENT_MODES))
    return create_project(
        project,
        [item.copy() for item in DEFAULT_TAXONOMY],
        methodology,
        json.loads(json.dumps(DEFAULT_VISUALIZATION)),
        empty_stop_dataset(),
        [],
        assessment_modes=DEFAULT_ASSESSMENT_MODES,
        scoring=DEFAULT_SCORING,
    )


def ensure_state() -> None:
    init_database()
    projects = list_projects()
    if projects:
        mark_project_store_initialized()
    elif not project_store_initialized():
        try:
            create_seed_project()
        except DuplicateProjectNameError:
            # Another app session may have created the seed after our initial read.
            pass
        mark_project_store_initialized()
        projects = list_projects()

    if not projects:
        clear_loaded_project_session()
        st.session_state["page"] = "Home"
        return

    active_project_id = st.session_state.get("active_project_id") or projects[0]["id"]
    known_ids = {project["id"] for project in projects}
    if active_project_id not in known_ids:
        active_project_id = projects[0]["id"]

    if st.session_state.get("loaded_project_id") != active_project_id:
        load_project_into_session(active_project_id)
        return

    current_taxonomy = st.session_state.get("taxonomy", DEFAULT_TAXONOMY)
    normalized_taxonomy = normalize_coverage_taxonomy(current_taxonomy)
    if current_taxonomy != normalized_taxonomy:
        st.session_state["taxonomy"] = normalized_taxonomy
        stops = st.session_state.get("stops", empty_stop_dataset())
        if not stops.empty:
            st.session_state["stops"] = prepare_stop_dataset(
                stops,
                st.session_state.get("project", DEFAULT_PROJECT),
                normalized_taxonomy,
            )
    ensure_visualization_defaults()


def dataframe_to_geojson(df: pd.DataFrame) -> str:
    return published_app.dataframe_to_geojson(df)


def study_config_json() -> str:
    return json.dumps(study_config_payload(), indent=2, default=str)


def study_config_payload() -> dict[str, Any]:
    taxonomy = normalize_coverage_taxonomy(st.session_state["taxonomy"])
    public_project = with_default_project_values(st.session_state["project"])
    public_project.pop("deployment", None)
    public_project.pop("_store_updated_at", None)
    public_project.pop("_store_loaded_at", None)
    methodology = with_default_methodology_values(st.session_state["methodology"])
    terminology = normalize_terminology(methodology.pop("terminology", None))
    source_taxonomy = normalize_source_taxonomy(
        methodology.pop("shade_source_taxonomy", None)
    )
    coverage_taxonomy = normalize_coverage_display_taxonomy(
        methodology.pop("shade_coverage_taxonomy", None),
        taxonomy,
    )
    visualization = normalized_visualization_values(
        st.session_state["visualization"], taxonomy
    )
    visualization["voting"]["shade_source_taxonomy"] = source_taxonomy
    visualization["voting"]["shade_coverage_taxonomy"] = coverage_taxonomy
    assessment_modes = normalize_modes(
        st.session_state.get("assessment_modes", DEFAULT_ASSESSMENT_MODES)
    )
    return {
        "study_id": st.session_state.get("active_project_id")
        or slugify_repo_name(st.session_state["project"].get("name", "stop-audit")),
        "project": public_project,
        "assessment_modes": assessment_modes,
        "codebook": assessment_codebook(assessment_modes),
        "scoring": st.session_state.get("scoring", DEFAULT_SCORING),
        "taxonomy": taxonomy,
        "terminology": terminology,
        "shade_source_taxonomy": source_taxonomy,
        "shade_coverage_taxonomy": coverage_taxonomy,
        "methodology": methodology,
        "visualization": visualization,
        "import_log": st.session_state["import_log"],
    }


def _canonical_deployment_state(
    project_id: str,
    project: dict[str, Any],
    taxonomy: list[dict[str, Any]],
    methodology: dict[str, Any],
    visualization: dict[str, Any],
    stops: pd.DataFrame,
    import_log: list[dict[str, Any]],
    assessment_modes: list[dict[str, Any]] | None = None,
    scoring: list[dict[str, Any]] | None = None,
) -> str:
    normalized_project = with_default_project_values(project)
    normalized_project.pop("deployment", None)
    normalized_project.pop("_store_updated_at", None)
    normalized_project.pop("_store_loaded_at", None)
    normalized_taxonomy = normalize_coverage_taxonomy(taxonomy)
    normalized_methodology = with_default_methodology_values(methodology)
    normalized_visualization = normalized_visualization_values(
        visualization, normalized_taxonomy
    )
    normalized_stops = stops.copy()
    if not normalized_stops.empty:
        normalized_stops = prepare_stop_dataset(
            normalized_stops, normalized_project, normalized_taxonomy
        )
    if not normalized_stops.empty:
        normalized_stops = normalized_stops.reindex(
            sorted(normalized_stops.columns), axis=1
        )
        if "stop_id" in normalized_stops.columns:
            normalized_stops = normalized_stops.sort_values("stop_id", kind="stable")
    payload = {
        "study_id": project_id,
        "project": normalized_project,
        "taxonomy": normalized_taxonomy,
        "methodology": normalized_methodology,
        "visualization": normalized_visualization,
        "import_log": import_log,
        "assessment_modes": normalize_modes(
            assessment_modes or DEFAULT_ASSESSMENT_MODES
        ),
        "scoring": scoring or DEFAULT_SCORING,
        "stops": normalized_stops.to_dict(orient="records"),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def deployment_session_freshness_issue() -> str:
    project_id = str(st.session_state.get("active_project_id") or "").strip()
    if not project_id:
        return (
            "The active project is not saved yet. Save or reopen it before publishing."
        )
    try:
        persisted = load_project_bundle(project_id)
    except KeyError:
        return "The saved project could not be found. Return to the project list and reopen it before publishing."

    current_state = _canonical_deployment_state(
        project_id,
        st.session_state.get("project", {}),
        st.session_state.get("taxonomy", []),
        st.session_state.get("methodology", {}),
        st.session_state.get("visualization", {}),
        st.session_state.get("stops", empty_stop_dataset()),
        st.session_state.get("import_log", []),
        st.session_state.get("assessment_modes", DEFAULT_ASSESSMENT_MODES),
        st.session_state.get("scoring", DEFAULT_SCORING),
    )
    persisted_state = _canonical_deployment_state(
        project_id,
        persisted["project"],
        persisted["taxonomy"],
        persisted["methodology"],
        persisted["visualization"],
        persisted["stops"],
        persisted["import_log"],
        persisted.get("assessment_modes", DEFAULT_ASSESSMENT_MODES),
        persisted.get("scoring", DEFAULT_SCORING),
    )
    if current_state != persisted_state:
        return (
            "This browser tab is not using the latest saved project state. Another tab or session changed the "
            "project. Reload the saved project before creating a deployment package."
        )
    return ""


def active_raw_labels() -> pd.DataFrame:
    project_id = st.session_state.get("active_project_id")
    if not project_id:
        return pd.DataFrame()
    return list_shade_labels(project_id)


def build_github_deploy_bundle(
    repo_name: str,
    deploy_mode: str = "existing",
    commit_message: str = DEFAULT_DEPLOY_COMMIT_MESSAGE,
) -> bytes:
    stops = st.session_state["stops"]
    if not stops.empty:
        project_id = str(st.session_state.get("active_project_id") or "")
        images = (
            list_images(project_id)
            if PHOTO_WORKFLOWS_ENABLED and project_id
            else pd.DataFrame()
        )
        quality_report = evaluate_data_quality(stops, images)
        if not quality_report.publication_ready:
            raise ValueError(
                f"Data Quality found {quality_report.total_issues:,} publication-blocking "
                "issue occurrence(s). Open Dataset > Quality, review the affected records, "
                "and resolve them before publishing."
            )
    return build_deployment_bundle(
        DeploymentBundleSpec(
            repository=repo_name,
            project=st.session_state["project"],
            study_id=str(st.session_state.get("active_project_id") or ""),
            stops=stops,
            raw_labels=active_raw_labels(),
            config_json=study_config_json(),
            priority_weights=st.session_state["visualization"]["priority_weights"],
            deploy_mode=deploy_mode,
            commit_message=commit_message,
            assessments=list_assessments(
                str(st.session_state.get("active_project_id") or "")
            ),
        )
    )


def set_page(page: str) -> None:
    if st.session_state.get("page") == page:
        return
    st.session_state["page"] = page


def open_project(project_id: str) -> bool:
    current_project_id = st.session_state.get("active_project_id")
    if current_project_id and current_project_id != project_id:
        if not save_active_project_to_store():
            return False
        load_project_into_session(project_id)
    elif st.session_state.get("loaded_project_id") != project_id:
        load_project_into_session(project_id)
    set_page("Data")
    return True


def request_open_project(project_id: str, _project_name: str = "") -> None:
    open_project(project_id)


def clear_pending_project_settings() -> None:
    st.session_state.pop("pending_project_settings", None)


def clear_pending_project_delete() -> None:
    st.session_state.pop("pending_project_delete", None)


def request_project_settings(project_id: str) -> None:
    clear_pending_project_delete()
    st.session_state["pending_project_settings"] = project_id


def request_project_delete(project_id: str, project_name: str) -> None:
    clear_pending_project_settings()
    st.session_state["pending_project_delete"] = {
        "id": project_id,
        "name": project_name,
    }


def clear_loaded_project_session() -> None:
    for key in (
        "active_project_id",
        "loaded_project_id",
        "project",
        "taxonomy",
        "methodology",
        "visualization",
        "assessment_modes",
        "scoring",
        "stops",
        "import_log",
        "deploy_page_settings_project_id",
    ):
        st.session_state.pop(key, None)


def request_main_menu() -> None:
    set_page("Home")


@st.dialog("Project settings", on_dismiss=clear_pending_project_settings)
def render_project_settings() -> None:
    project_id = str(st.session_state.get("pending_project_settings") or "")
    try:
        bundle = load_project_bundle(project_id)
    except KeyError:
        clear_pending_project_settings()
        st.error("This project no longer exists.")
        return

    project = bundle["project"]
    project_name = str(project.get("name") or "Untitled Stop Audit")
    st.markdown(
        "<span class='project-settings-dialog-marker'></span>", unsafe_allow_html=True
    )
    st.caption("Update the details shown on your project card and published study.")
    with st.form(f"project_settings_form_{project_id}"):
        name = st.text_input(
            "Project name",
            value=project_name,
            help="The main title shown on the project card, in previews, and in the published study.",
        )
        agency = st.text_input(
            "Agency or organization",
            value=str(project.get("agency") or ""),
            help=(
                "The transit agency, research team, or organization responsible for the study. "
                "It appears with the location in project and public-study captions."
            ),
        )
        region = st.text_input(
            "Location",
            value=str(project.get("region") or ""),
            help=(
                "A descriptive geographic label, such as 'Pittsburgh, Pennsylvania.' It appears in project "
                "and public-study labels; it does not move the map, filter data, or set a boundary."
            ),
        )
        description = st.text_area(
            "Description",
            value=str(project.get("description") or ""),
            help=(
                "A short explanation of the study's purpose. It is saved with the project and included "
                "in exported project metadata."
            ),
        )
        visibility_options = ["Private", "Public"]
        current_visibility = str(project.get("visibility") or "Private").title()
        visibility = st.selectbox(
            "Visibility",
            visibility_options,
            index=visibility_options.index(current_visibility)
            if current_visibility in visibility_options
            else 0,
            help=(
                "Controls the Private or Public badge on the project. Public marks the study as intended "
                "for a public audience, but publishing the website is still a separate step."
            ),
        )
        submitted = st.form_submit_button(
            "Save changes", type="primary", width="stretch"
        )

    if submitted:
        if not name.strip():
            st.error("Project name is required.")
        else:
            try:
                update_project_details(
                    project_id,
                    name=name,
                    agency=agency,
                    region=region,
                    description=description,
                    visibility=visibility,
                    expected_revision=str(project.get("_store_updated_at") or ""),
                )
            except (DuplicateProjectNameError, ProjectConflictError) as error:
                st.error(str(error))
                return
            if st.session_state.get("active_project_id") == project_id:
                load_project_into_session(project_id)
            clear_pending_project_settings()
            st.session_state["project_settings_notice"] = (
                f"Saved settings for {name.strip()}."
            )
            st.rerun()

    st.divider()
    st.subheader("Danger zone")
    st.caption(
        "Deleting a project permanently removes its stops, stored evidence, labels, reviews, and releases from this device."
    )
    if st.button(
        "Delete project",
        key=f"request_delete_project_{project_id}",
        width="stretch",
    ):
        request_project_delete(project_id, project_name)
        st.rerun()


@st.dialog("Delete project?", on_dismiss=clear_pending_project_delete)
def render_project_delete_confirmation() -> None:
    pending = st.session_state.get("pending_project_delete") or {}
    project_id = str(pending.get("id") or "")
    project_name = str(pending.get("name") or "this project")
    st.markdown(
        "<span class='project-delete-dialog-marker'></span>", unsafe_allow_html=True
    )
    st.warning(f"This permanently deletes {project_name} and all of its project data.")
    confirmation = st.text_input(
        f'Type "{project_name}" to confirm',
        key=f"delete_project_confirmation_{project_id}",
    )
    cancel_column, delete_column = st.columns(2)
    with cancel_column:
        if st.button("Cancel", key="cancel_project_delete", width="stretch"):
            clear_pending_project_delete()
            st.session_state["pending_project_settings"] = project_id
            st.rerun()
    with delete_column:
        if st.button(
            "Delete permanently",
            key="confirm_project_delete",
            type="primary",
            disabled=confirmation != project_name,
            width="stretch",
        ):
            deleted = delete_project(project_id)
            if not deleted:
                st.error("This project no longer exists.")
                return
            if st.session_state.get("active_project_id") == project_id:
                clear_loaded_project_session()
            clear_pending_project_delete()
            clear_pending_project_settings()
            st.session_state["project_settings_notice"] = f"Deleted {project_name}."
            st.rerun()


def format_project_updated(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "Recently updated"
    try:
        updated = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return "Recently updated"
    now = datetime.now(updated.tzinfo) if updated.tzinfo else datetime.now()
    if updated.date() == now.date():
        return "Updated today"
    return f"Updated {updated.strftime('%b %d, %Y').replace(' 0', ' ')}"


def project_label_progress(
    labeled_count: int, location_count: int
) -> tuple[float, str]:
    """Return the exact bar width and a concise, non-misleading percentage label."""
    if location_count <= 0 or labeled_count <= 0:
        return 0.0, "0%"

    percent = min(labeled_count / location_count * 100, 100.0)
    if percent < 0.1:
        return percent, "<0.1%"
    if percent >= 100:
        return percent, "100%"

    displayed_percent = min(round(percent, 1), 99.9)
    label = f"{displayed_percent:.1f}".rstrip("0").rstrip(".")
    return percent, f"{label}%"


def render_home_page() -> None:
    projects = list_projects()
    notice = st.session_state.pop("project_settings_notice", None)
    if notice:
        st.toast(str(notice), icon="✅")
    st.markdown(
        """
        <style>
        .st-key-home_page {
            margin: 0 auto;
            max-width: 1080px;
            padding: 0.15rem 0 2.5rem;
        }
        div[data-testid="stAppViewContainer"]:has(.st-key-home_page) {
            background: #f8fbf8;
        }
        div[data-testid="stAppViewContainer"]:has(.st-key-home_page)
        div[data-testid="stHorizontalBlock"]:has(.st-key-nav_home) {
            margin-bottom: 0.35rem;
        }
        .st-key-home_page .home-subtitle {
            color: #52605a;
            font-size: 1.05rem;
            line-height: 1.65;
            margin: 0 0 1.2rem;
            max-width: 46rem;
        }
        .st-key-home_page .home-section-title {
            color: #17211c;
            font-size: 1.9rem;
            letter-spacing: -0.025em;
            line-height: 1.2;
            margin: 0;
        }
        .st-key-home_page .home-project-count {
            color: #66736c;
            font-size: 0.92rem;
            margin: 0.35rem 0 0;
        }
        div[data-testid="stHorizontalBlock"]:has(.home-section-title) [data-testid="stPopover"] button,
        .st-key-home_create_project button {
            background: #166534;
            border-color: #166534;
            color: white;
            font-weight: 650;
        }
        div[data-testid="stHorizontalBlock"]:has(.home-section-title) [data-testid="stPopover"] button:hover,
        .st-key-home_create_project button:hover {
            background: #14532d;
            border-color: #14532d;
            color: white;
        }
        div[data-testid="stDialog"]:has(.project-settings-dialog-marker) button[kind="primary"] {
            background: #166534;
            border-color: #166534;
            color: white;
        }
        div[data-testid="stDialog"]:has(.project-delete-dialog-marker) button[kind="primary"] {
            background: #b91c1c;
            border-color: #b91c1c;
            color: white;
        }
        div[data-testid="stDialog"]:has(.project-delete-dialog-marker) button[kind="primary"]:hover {
            background: #991b1b;
            border-color: #991b1b;
        }
        div[class*="st-key-project_card_"] {
            background: white;
            border: 1px solid #dce4df;
            border-radius: 0.85rem;
            box-shadow: 0 1px 2px rgba(15, 48, 30, 0.04);
            cursor: pointer;
            box-sizing: border-box;
            min-height: 24.5rem;
            padding: 1.25rem;
            position: relative;
            transition: border-color 150ms ease, box-shadow 150ms ease, transform 150ms ease;
        }
        div[class*="st-key-project_card_"]:hover {
            border-color: #4ade80;
            box-shadow: 0 0.75rem 1.8rem rgba(20, 83, 45, 0.13);
            transform: translateY(-3px);
        }
        div[class*="st-key-project_card_"]:active {
            box-shadow: 0 0.3rem 0.8rem rgba(20, 83, 45, 0.14);
            transform: translateY(-1px);
        }
        div[class*="st-key-project_card_"]:focus-within {
            border-color: #16a34a;
            box-shadow: 0 0 0 0.22rem rgba(34, 197, 94, 0.24);
        }
        div[class*="st-key-project_card_"] div[class*="st-key-home_open_"] {
            bottom: 0;
            left: 0;
            position: absolute;
            right: 0;
            top: 0;
            z-index: 5;
        }
        div[class*="st-key-project_card_"] div[class*="st-key-home_open_"] button {
            bottom: 0;
            cursor: pointer;
            height: 100%;
            left: 0;
            opacity: 0;
            position: absolute;
            top: 0;
            width: 100%;
        }
        div[class*="st-key-project_card_"] div[class*="st-key-project_settings_"] {
            position: absolute;
            right: 0.9rem;
            top: 0.9rem;
            z-index: 10;
        }
        div[class*="st-key-project_card_"] div[class*="st-key-project_settings_"] button {
            background: white;
            border: 1px solid #d7ddd9;
            border-radius: 0.55rem;
            color: #26342c;
            font-size: 1.25rem;
            height: 2.2rem;
            min-width: 2.4rem;
            padding: 0 0.45rem;
        }
        div[class*="st-key-project_card_"] div[class*="st-key-project_settings_"] button:hover {
            background: #f0fdf4;
            border-color: #86efac;
            color: #166534;
        }
        .project-card-content {
            display: flex;
            flex-direction: column;
            min-height: 15.5rem;
            pointer-events: none;
        }
        .project-card-topline {
            align-items: flex-start;
            display: flex;
            gap: 0.75rem;
            justify-content: space-between;
        }
        .project-card-title {
            color: #17211c;
            font-size: 1.3rem;
            font-weight: 720;
            letter-spacing: -0.012em;
            line-height: 1.35;
            margin: 0;
            overflow-wrap: anywhere;
            padding-right: 3rem;
        }
        .project-card-location {
            color: #4f5e56;
            font-size: 0.94rem;
            line-height: 1.5;
            margin: 0.8rem 0 0;
        }
        .project-location-label {
            color: #166534;
            font-size: 0.72rem;
            font-weight: 750;
            letter-spacing: 0.035em;
            text-transform: uppercase;
        }
        .project-card-meta {
            color: #6b7871;
            font-size: 0.84rem;
            line-height: 1.55;
            margin: 0.65rem 0 0.85rem;
        }
        .project-progress-label {
            align-items: center;
            color: #4f5e56;
            display: flex;
            font-size: 0.8rem;
            justify-content: space-between;
            margin-bottom: 0.38rem;
        }
        .project-progress-track {
            background: #e6ece8;
            border-radius: 999px;
            height: 0.48rem;
            overflow: hidden;
        }
        .project-progress-fill {
            background: #22a854;
            border-radius: inherit;
            height: 100%;
        }
        .project-progress-fill.has-progress {
            min-width: 2px;
        }
        .project-card-stats {
            display: grid;
            gap: 0.6rem;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            margin: 0.9rem 0 1rem;
        }
        .project-card-stat strong {
            color: #26342c;
            display: block;
            font-size: 0.9rem;
        }
        .project-card-stat span {
            color: #738078;
            display: block;
            font-size: 0.7rem;
            line-height: 1.25;
        }
        .project-card-action {
            color: #166534;
            font-size: 0.92rem;
            font-weight: 700;
            margin-top: auto;
        }
        @media (max-width: 760px) {
            .st-key-home_page { padding-left: 0.2rem; padding-right: 0.2rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="home_page"):
        title_column, action_column = st.columns([4, 1], vertical_alignment="center")
        with title_column:
            project_word = "project" if len(projects) == 1 else "projects"
            st.markdown(
                f'<h1 class="home-section-title">Your Projects</h1>'
                f'<p class="home-project-count">{len(projects)} {project_word}</p>',
                unsafe_allow_html=True,
            )
        with action_column:
            with st.popover("+ New Project", width="stretch"):
                st.markdown("**Create a stop audit**")
                st.caption(
                    "Start with an empty project and add your own transit or GIS data."
                )
                new_project_name = st.text_input(
                    "Project name",
                    key="home_new_project_name",
                    placeholder="e.g. Downtown transit stop audit",
                )
                if st.button(
                    "Create project", key="home_create_project", width="stretch"
                ):
                    try:
                        project_id = create_blank_project(new_project_name)
                    except DuplicateProjectNameError as error:
                        st.error(str(error))
                        return
                    load_project_into_session(project_id)
                    set_page("Data")
                    st.rerun()

        st.markdown("<div style='height: 0.7rem'></div>", unsafe_allow_html=True)
        if not projects:
            st.info("No saved projects yet. Create your first project to get started.")
            return

        card_columns = st.columns(2, gap="large")
        for index, project in enumerate(projects):
            project_id = str(project["id"])
            project_name = str(project.get("name") or "Untitled Stop Audit")
            name = html.escape(project_name)
            agency = html.escape(str(project.get("agency") or "No agency"))
            region = html.escape(str(project.get("region") or "No location set"))
            version = html.escape(str(project.get("dataset_version") or "draft"))
            updated = html.escape(format_project_updated(project.get("updated_at")))
            location_count = int(project.get("location_count") or 0)
            labeled_count = int(project.get("labeled_count") or 0)
            unlabeled_count = max(location_count - labeled_count, 0)
            label_percent, label_percent_text = project_label_progress(
                labeled_count, location_count
            )
            progress_fill_class = " has-progress" if label_percent > 0 else ""
            with card_columns[index % len(card_columns)]:
                with st.container(key=f"project_card_{index}"):
                    st.markdown(
                        f"""
                        <div class="project-card-content">
                            <div class="project-card-topline">
                                <h2 class="project-card-title">{name}</h2>
                            </div>
                            <p class="project-card-location"><span class="project-location-label">Location</span> · {region} · {agency}</p>
                            <p class="project-card-meta">Dataset v{version} · {updated}</p>
                            <div class="project-progress-label"><span>Label progress</span><strong>{label_percent_text}</strong></div>
                            <div class="project-progress-track"><div class="project-progress-fill{progress_fill_class}" style="width: {label_percent:.4f}%"></div></div>
                            <div class="project-card-stats">
                                <div class="project-card-stat"><strong>{location_count:,}</strong><span>Locations</span></div>
                                <div class="project-card-stat"><strong>{labeled_count:,}</strong><span>Labeled</span></div>
                                <div class="project-card-stat"><strong>{unlabeled_count:,}</strong><span>Unlabeled</span></div>
                            </div>
                            <span class="project-card-action">Open Project →</span>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    st.button(
                        "⋯",
                        key=f"project_settings_{project_id}",
                        help=f"Settings for {project_name}",
                        on_click=request_project_settings,
                        args=(project_id,),
                    )
                    st.button(
                        f"Open project: {name}",
                        key=f"home_open_{project_id}",
                        width="stretch",
                        on_click=request_open_project,
                        args=(project_id, project_name),
                    )
        if st.session_state.get("pending_project_delete"):
            render_project_delete_confirmation()
        elif st.session_state.get("pending_project_settings"):
            render_project_settings()


def render_header() -> str:
    configured_modes = st.session_state.get("assessment_modes", [])
    shade_enabled = (
        any(
            mode["enabled"]
            and mode["key"] in {"shade_coverage", "shade_source"}
            for mode in normalize_modes(configured_modes)
        )
        if configured_modes
        else True
    )
    primary_navigation = [
        ("Dataset", "Data"),
        ("Labeling", "Labels"),
        ("Publish", "Preview"),
    ]
    if shade_enabled:
        primary_navigation.insert(2, ("Public Voting", "Voting"))
    secondary_navigation = {
        "Dataset": [
            ("Overview", "Data"),
            ("Quality", "Data Quality"),
            ("Taxonomy", "Taxonomy"),
        ],
        "Labeling": [
            ("Dataset Review", "Labels"),
            ("Intercoder Review", "Blind Coding"),
        ],
        "Publish": [
            ("Preview & Exports", "Preview"),
            ("Release Notes", "Docs"),
            ("Dataset Release", "Deploy"),
        ],
    }
    page_sections = {
        "Data": "Dataset",
        "Data Quality": "Dataset",
        "Taxonomy": "Dataset",
        "Labels": "Labeling",
        "Blind Coding": "Labeling",
        "Docs": "Publish",
        "Preview": "Publish",
        "Deploy": "Publish",
    }
    if shade_enabled:
        page_sections["Voting"] = "Public Voting"
    pages = ["Home", *page_sections]
    if st.session_state.get("page") not in pages:
        st.session_state["page"] = "Home"
    st.markdown(
        """
        <style>
        :root {
            --shade-brand: #17603a;
            --shade-brand-hover: #124b2e;
            --shade-brand-soft: #eaf4ee;
            --shade-text: #17211b;
            --shade-muted: #66706a;
            --shade-border: #e4e8e5;
        }
        [data-testid="stMainBlockContainer"] {
            max-width: 1440px;
            padding-top: 0.75rem;
        }
        header[data-testid="stHeader"] {
            background: transparent;
            height: 0;
            min-height: 0;
        }
        [data-testid="stToolbar"],
        [data-testid="stDecoration"] {
            display: none;
        }
        .st-key-app_header {
            align-items: center;
            border-bottom: 1px solid var(--shade-border);
            min-height: 68px;
            padding: 0.45rem 0;
        }
        .st-key-app_header [data-testid="stHorizontalBlock"] {
            align-items: center;
        }
        .st-key-nav_home button {
            background: transparent;
            border: 0;
            border-radius: 8px;
            box-shadow: none;
            color: var(--shade-brand);
            font-size: 30px;
            font-weight: 750;
            justify-content: flex-start;
            letter-spacing: -0.03em;
            line-height: 1;
            min-height: 44px;
            padding: 0.3rem 0.45rem;
            white-space: nowrap;
        }
        .st-key-nav_home button p {
            font-size: 30px;
            font-weight: 750;
            line-height: 1;
        }
        .st-key-nav_home button:hover {
            background: var(--shade-brand-soft);
            color: var(--shade-brand-hover);
        }
        .st-key-nav_home button:active {
            background: #dceee3;
            color: var(--shade-brand-hover);
        }
        .st-key-nav_home button:focus-visible {
            box-shadow: 0 0 0 3px rgba(23, 96, 58, 0.16);
            outline: none;
        }
        div[class*="st-key-primary_nav_"] button,
        .st-key-header_subnav button {
            background: transparent;
            border: 0;
            border-radius: 8px;
            box-shadow: none;
            color: #4f5953;
            font-size: 15px;
            font-weight: 500;
            min-height: 40px;
            padding: 0.55rem 0.8rem;
        }
        div[class*="st-key-primary_nav_"] button:hover,
        .st-key-header_subnav button:hover {
            background: #f2f5f3;
            color: var(--shade-text);
        }
        div[class*="st-key-primary_nav_"] button[kind="primary"],
        .st-key-header_subnav button[kind="primary"] {
            background: var(--shade-brand-soft);
            color: var(--shade-brand);
            font-weight: 650;
        }
        .st-key-header_project [data-testid="stPopover"] > button {
            background: #ffffff;
            border: 1px solid #dce2de;
            border-radius: 8px;
            box-shadow: none;
            color: #303833;
            font-size: 14px;
            min-height: 40px;
            padding: 0.5rem 0.75rem;
            white-space: nowrap;
            width: 100%;
        }
        .st-key-header_project [data-testid="stPopover"] > button:hover {
            background: #f7f9f7;
            border-color: #c8d1cb;
        }
        .st-key-header_subnav {
            border-bottom: 1px solid var(--shade-border);
            margin-bottom: 1rem;
            min-height: 44px;
            padding: 0.25rem 0 0.45rem;
        }
        .st-key-header_subnav [data-testid="stHorizontalBlock"] {
            gap: 0.5rem !important;
            justify-content: flex-start !important;
            width: 100%;
        }
        .st-key-header_subnav [data-testid="stColumn"] {
            flex: 0 0 auto !important;
            min-width: 0 !important;
            width: auto !important;
        }
        .st-key-header_subnav [data-testid="stColumn"] button {
            width: auto !important;
        }
        .autosave-status {
            background: #f0fdf4;
            border: 1px solid #bbf7d0;
            border-radius: 999px;
            bottom: 1rem;
            box-shadow: 0 0.35rem 1rem rgba(15, 48, 30, 0.12);
            color: #166534;
            font-size: 0.8rem;
            font-weight: 650;
            padding: 0.45rem 0.75rem;
            position: fixed;
            right: 1rem;
            z-index: 999;
        }
        .autosave-status.failed {
            background: #fff7ed;
            border-color: #fdba74;
            color: #9a3412;
        }
        @media (max-width: 900px) {
            [data-testid="stMainBlockContainer"] { padding-left: 1rem; padding-right: 1rem; }
            .st-key-nav_home button, .st-key-nav_home button p { font-size: 25px; }
            div[class*="st-key-primary_nav_"] button { font-size: 14px; padding: 0.5rem; }
            .st-key-header_project [data-testid="stPopover"] > button { min-width: 44px; }
        }
        @media (max-width: 760px) {
            .autosave-status {
                bottom: 0.6rem;
                left: 0.75rem;
                max-width: calc(100vw - 1.5rem);
                right: auto;
            }
            .st-key-app_header { min-height: 0; }
            div[data-testid="stHorizontalBlock"]:has(.st-key-header_project) {
                flex-wrap: wrap;
            }
            div[data-testid="stHorizontalBlock"]:has(.st-key-header_project)
            > [data-testid="stColumn"] {
                flex: 1 1 100% !important;
                width: 100% !important;
            }
            div[data-testid="stHorizontalBlock"]:has(.st-key-header_project)
            div[class*="st-key-primary_nav_"] button {
                font-size: 13px;
                padding-left: 0.35rem;
                padding-right: 0.35rem;
            }
            .st-key-header_subnav {
                overflow-x: auto;
                scrollbar-width: thin;
            }
            .st-key-header_subnav [data-testid="stHorizontalBlock"] {
                flex-wrap: nowrap;
                min-width: max-content;
                padding-bottom: 0.2rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    on_home_page = st.session_state["page"] == "Home"
    if on_home_page:
        st.button("Stop-GIS", key="nav_home", on_click=request_main_menu)
    else:
        active_section = page_sections[st.session_state["page"]]
        with st.container(key="app_header"):
            brand, navigation, project_selector = st.columns(
                [1.55, 4.75, 1.7],
                gap="medium",
                vertical_alignment="center",
            )
            with brand:
                st.button("Stop-GIS", key="nav_home", on_click=request_main_menu)
            nav_columns = navigation.columns(len(primary_navigation), gap="small")
            for column, (label, destination) in zip(nav_columns, primary_navigation):
                with column:
                    st.button(
                        label,
                        key=f"primary_nav_{label.lower()}",
                        type="primary" if active_section == label else "secondary",
                        width="stretch",
                        on_click=set_page,
                        args=(destination,),
                    )
            with project_selector:
                project_name = str(
                    st.session_state.get("project", {}).get("name") or "Project"
                )
                selector_label = (
                    project_name
                    if len(project_name) <= 24
                    else f"{project_name[:21].rstrip()}..."
                )
                with st.popover(
                    selector_label,
                    key="header_project",
                    width="stretch",
                ):
                    st.caption("Projects")
                    active_project_id = st.session_state.get("active_project_id")
                    for project in list_projects():
                        project_id = project["id"]
                        st.button(
                            project.get("name") or "Untitled Stop Audit",
                            key=f"header_project_{project_id}",
                            type="primary"
                            if project_id == active_project_id
                            else "secondary",
                            disabled=project_id == active_project_id,
                            width="stretch",
                            on_click=request_open_project,
                            args=(
                                project_id,
                                project.get("name") or "Untitled Stop Audit",
                            ),
                        )
                    st.divider()
                    st.button(
                        "All projects",
                        key="header_all_projects",
                        width="stretch",
                        on_click=request_main_menu,
                    )

        section_pages = secondary_navigation.get(active_section, [])
        if section_pages:
            with st.container(key="header_subnav"):
                subnav_columns = st.columns(len(section_pages), gap="small")
                for column, (label, destination) in zip(subnav_columns, section_pages):
                    with column:
                        st.button(
                            label,
                            key=f"secondary_nav_{destination.lower()}",
                            type=(
                                "primary"
                                if st.session_state["page"] == destination
                                else "secondary"
                            ),
                            on_click=set_page,
                            args=(destination,),
                        )
    return st.session_state["page"]


def main() -> None:
    from stop_gis.pages.data_page import render_data_page
    from stop_gis.pages.blind_coding_page import render_blind_coding_page
    from stop_gis.pages.data_quality_page import render_data_quality_page
    from stop_gis.pages.deploy_page import render_deploy_page
    from stop_gis.pages.docs_page import render_methodology_page
    from stop_gis.pages.labels_page import render_labels_page
    from stop_gis.pages.preview_page import render_preview_page
    from stop_gis.pages.taxonomy_page import render_taxonomy_page
    from stop_gis.pages.voting_page import render_voting_page

    st.set_page_config(page_title=APP_TITLE, layout="wide")
    ensure_state()
    if st.session_state.get("page") in {"Methodology", "Methods"}:
        st.session_state["page"] = "Docs"
    page = render_header()
    if page == "Home":
        render_home_page()
    elif page == "Data Quality":
        render_data_quality_page()
    elif page == "Labels":
        render_labels_page()
    elif page == "Blind Coding":
        render_blind_coding_page()
    elif page == "Taxonomy":
        render_taxonomy_page()
    elif page == "Voting":
        render_voting_page()
    elif page == "Docs":
        render_methodology_page()
    elif page == "Preview":
        render_preview_page()
    elif page == "Deploy":
        render_deploy_page()
    else:
        render_data_page()
    save_active_project_to_store()
    render_autosave_status()
