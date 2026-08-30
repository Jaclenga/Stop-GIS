"""Central data-quality checks for active Stop-GIS project datasets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


REQUIRED_STOP_FIELDS = ("stop_id", "stop_name", "stop_lat", "stop_lon")
ISSUE_RECORD_COLUMNS = [
    "issue_key",
    "issue",
    "record_type",
    "record_id",
    "row_number",
    "details",
    "_position",
]


@dataclass(frozen=True)
class DataQualityIssue:
    key: str
    label: str
    description: str
    record_type: str


DATA_QUALITY_ISSUES = (
    DataQualityIssue(
        "duplicate_stop_ids",
        "Duplicate stop IDs",
        "Stop IDs must uniquely identify one stop record.",
        "Stop",
    ),
    DataQualityIssue(
        "missing_coordinates",
        "Missing coordinates",
        "Stops need both latitude and longitude before they can be mapped.",
        "Stop",
    ),
    DataQualityIssue(
        "missing_required_fields",
        "Missing required fields",
        "Stop ID, stop name, latitude, and longitude are required.",
        "Stop",
    ),
    DataQualityIssue(
        "invalid_geometries",
        "Invalid geometries",
        "Coordinates must be numeric and within valid latitude/longitude bounds.",
        "Stop",
    ),
    DataQualityIssue(
        "orphaned_images",
        "Orphaned images",
        "Every image must reference a stop in the active dataset.",
        "Image",
    ),
)
ISSUE_BY_KEY = {issue.key: issue for issue in DATA_QUALITY_ISSUES}


def _empty_issue_records() -> pd.DataFrame:
    return pd.DataFrame(columns=pd.Index(ISSUE_RECORD_COLUMNS, dtype=object))


def _column(frame: pd.DataFrame, name: str) -> pd.Series:
    if name in frame.columns:
        return frame[name]
    return pd.Series(pd.NA, index=frame.index, dtype=object)


def _blank_mask(values: pd.Series) -> pd.Series:
    return values.isna() | values.astype(str).str.strip().eq("")


def _normalized_text(values: pd.Series) -> pd.Series:
    return values.fillna("").astype(str).str.strip()


def _record_id(frame: pd.DataFrame, position: int, record_type: str) -> str:
    field = "stop_id" if record_type == "Stop" else "id"
    if field in frame.columns:
        value = frame.iloc[position][field]
        if pd.notna(value) and str(value).strip():
            return str(value).strip()
    return f"{record_type} row {position + 1}"


def _issue_rows(
    frame: pd.DataFrame,
    issue: DataQualityIssue,
    mask: pd.Series,
    details: dict[int, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for position, affected in enumerate(mask.fillna(False).astype(bool).tolist()):
        if not affected:
            continue
        rows.append(
            {
                "issue_key": issue.key,
                "issue": issue.label,
                "record_type": issue.record_type,
                "record_id": _record_id(frame, position, issue.record_type),
                "row_number": position + 1,
                "details": details.get(position, issue.description),
                "_position": position,
            }
        )
    return rows


@dataclass
class DataQualityReport:
    """Validation findings and publication-readiness state for one project."""

    stops: pd.DataFrame
    images: pd.DataFrame
    records: pd.DataFrame

    def count(self, issue_key: str) -> int:
        return int(self.records["issue_key"].eq(issue_key).sum()) if not self.records.empty else 0

    @property
    def total_issues(self) -> int:
        return len(self.records)

    @property
    def publication_ready(self) -> bool:
        return bool(len(self.stops)) and self.total_issues == 0

    def summary_table(self) -> pd.DataFrame:
        rows = [
            {
                "Validation issue": issue.label,
                "Affected records": self.count(issue.key),
                "Status": "Pass" if self.count(issue.key) == 0 else "Needs attention",
            }
            for issue in DATA_QUALITY_ISSUES
        ]
        return pd.DataFrame(rows)

    def issue_records(self, issue_key: str | None = None) -> pd.DataFrame:
        records = self.records
        if issue_key and issue_key in ISSUE_BY_KEY:
            records = records[records["issue_key"].eq(issue_key)]
        return records.drop(columns=["_position"], errors="ignore").reset_index(drop=True)

    def affected_records(self, issue_key: str) -> pd.DataFrame:
        """Return source rows for one selected issue, annotated with its details."""
        issue = ISSUE_BY_KEY[issue_key]
        findings = self.records[self.records["issue_key"].eq(issue_key)]
        source = self.stops if issue.record_type == "Stop" else self.images
        if findings.empty:
            return source.iloc[0:0].copy()
        rows = []
        for finding in findings.to_dict("records"):
            position = int(finding["_position"])
            if position >= len(source):
                continue
            row = source.iloc[position].to_dict()
            row = {
                "Validation issue": issue.label,
                "Quality details": finding["details"],
                "Source row": position + 1,
                **row,
            }
            rows.append(row)
        return pd.DataFrame(rows)


def evaluate_data_quality(
    stops: pd.DataFrame | None,
    images: pd.DataFrame | None = None,
) -> DataQualityReport:
    """Evaluate all publication-blocking dataset checks in one pass."""
    stops = stops.copy() if isinstance(stops, pd.DataFrame) else pd.DataFrame()
    images = images.copy() if isinstance(images, pd.DataFrame) else pd.DataFrame()
    findings: list[dict[str, Any]] = []

    stop_ids = _normalized_text(_column(stops, "stop_id"))
    duplicate_mask = stop_ids.ne("") & stop_ids.duplicated(keep=False)
    duplicate_details = {
        position: f"Stop ID {stop_ids.iloc[position]!r} appears more than once."
        for position in range(len(stops))
        if bool(duplicate_mask.iloc[position])
    }
    findings.extend(
        _issue_rows(stops, ISSUE_BY_KEY["duplicate_stop_ids"], duplicate_mask, duplicate_details)
    )

    latitude_values = _column(stops, "stop_lat")
    longitude_values = _column(stops, "stop_lon")
    latitude_blank = _blank_mask(latitude_values)
    longitude_blank = _blank_mask(longitude_values)
    missing_coordinate_mask = latitude_blank | longitude_blank
    missing_coordinate_details = {}
    for position in range(len(stops)):
        missing = []
        if bool(latitude_blank.iloc[position]):
            missing.append("latitude")
        if bool(longitude_blank.iloc[position]):
            missing.append("longitude")
        if missing:
            missing_coordinate_details[position] = f"Missing {' and '.join(missing)}."
    findings.extend(
        _issue_rows(
            stops,
            ISSUE_BY_KEY["missing_coordinates"],
            missing_coordinate_mask,
            missing_coordinate_details,
        )
    )

    required_blank = {field: _blank_mask(_column(stops, field)) for field in REQUIRED_STOP_FIELDS}
    missing_required_mask = pd.Series(False, index=stops.index)
    for mask in required_blank.values():
        missing_required_mask |= mask
    missing_required_details = {}
    for position in range(len(stops)):
        missing = [field for field, mask in required_blank.items() if bool(mask.iloc[position])]
        if missing:
            missing_required_details[position] = f"Missing required field(s): {', '.join(missing)}."
    findings.extend(
        _issue_rows(
            stops,
            ISSUE_BY_KEY["missing_required_fields"],
            missing_required_mask,
            missing_required_details,
        )
    )

    latitude = pd.to_numeric(latitude_values, errors="coerce")
    longitude = pd.to_numeric(longitude_values, errors="coerce")
    invalid_geometry_mask = ~missing_coordinate_mask & (
        latitude.isna()
        | longitude.isna()
        | latitude.lt(-90)
        | latitude.gt(90)
        | longitude.lt(-180)
        | longitude.gt(180)
    )
    invalid_geometry_details = {}
    for position in range(len(stops)):
        if not bool(invalid_geometry_mask.iloc[position]):
            continue
        invalid_geometry_details[position] = (
            f"Invalid point coordinates: latitude={latitude_values.iloc[position]!r}, "
            f"longitude={longitude_values.iloc[position]!r}."
        )
    findings.extend(
        _issue_rows(
            stops,
            ISSUE_BY_KEY["invalid_geometries"],
            invalid_geometry_mask,
            invalid_geometry_details,
        )
    )

    valid_stop_ids = set(stop_ids[stop_ids.ne("")])
    image_stop_ids = _normalized_text(_column(images, "stop_id"))
    orphaned_mask = image_stop_ids.eq("") | ~image_stop_ids.isin(valid_stop_ids)
    orphaned_details = {}
    for position in range(len(images)):
        if not bool(orphaned_mask.iloc[position]):
            continue
        stop_id = image_stop_ids.iloc[position]
        orphaned_details[position] = (
            "Image is not associated with a stop."
            if not stop_id
            else f"Referenced stop ID {stop_id!r} is not in the active dataset."
        )
    findings.extend(
        _issue_rows(images, ISSUE_BY_KEY["orphaned_images"], orphaned_mask, orphaned_details)
    )

    records = pd.DataFrame(findings) if findings else _empty_issue_records()
    return DataQualityReport(stops=stops, images=images, records=records)
