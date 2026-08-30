#!/usr/bin/env python3
"""Verify local bus-stop heat data, optionally against the county source layer.

The local checks need no network access or third-party packages. Pass ``--live``
to download the cited Hillsborough County ArcGIS layer and independently match
each GTFS stop to the polygon that contains it.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


APP_DIR = Path(__file__).resolve().parents[1]
DEMO_DATA_DIR = APP_DIR / "data" / "demo"
DEFAULT_DATA = DEMO_DATA_DIR / "shading_data.csv"
DEFAULT_STOPS = DEMO_DATA_DIR / "stops.txt"
DEFAULT_URL = (
    "https://services1.arcgis.com/IbNXlmt2RVVRCZ6M/arcgis/rest/services/"
    "HeatVulnerabilityIndex/FeatureServer/0/query?where=1%3D1&outFields=*"
    "&returnGeometry=true&outSR=4326&f=geojson"
)
HEAT_FIELDS = (
    "heat_vulnerability_index",
    "heat_vulnerability_label",
    "tree_canopy_pct",
    "lst_median",
)
REQUIRED_DATA_FIELDS = ("stop_id", "shading", *HEAT_FIELDS)
UNKNOWN_VALUE = "Unknown"
FILLER_VALUES = {
    "",
    "-",
    "--",
    "n/a",
    "na",
    "nan",
    "none",
    "null",
    "missing",
    "not available",
    "unknown",
}
VALID_LABELS = {
    UNKNOWN_VALUE,
    "Least Vulnerable",
    "Low Vulnerability",
    "Moderate Vulnerability",
    "Elevated Vulnerability",
    "Most Vulnerable",
}
SOURCE_FIELD_NAMES = {
    "heat_vulnerability_index": ("HVI_index_weighted", "HVI_Weighted"),
    "heat_vulnerability_label": ("Code", "Label"),
    "tree_canopy_pct": ("TreeCanopy_PCT",),
    "lst_median": ("LST_Median", "LST"),
}


@dataclass
class Report:
    errors: list[str]
    warnings: list[str]

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def is_filler(value: Any) -> bool:
    return clean(value).casefold() in FILLER_VALUES


def number(value: Any) -> float | None:
    text = clean(value)
    if not text:
        return None
    try:
        result = float(text)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    repeated: set[str] = set()
    for value in values:
        if value in seen:
            repeated.add(value)
        seen.add(value)
    return sorted(repeated)


def examples(values: Iterable[str], limit: int = 8) -> str:
    items = list(values)
    shown = ", ".join(items[:limit])
    return shown + (f" (+{len(items) - limit} more)" if len(items) > limit else "")


def fill_unknown_heat_values(rows: list[dict[str, str]], fields: list[str]) -> tuple[int, int]:
    replacements = 0
    changed_stops = 0
    fillable_fields = [field for field in HEAT_FIELDS if field in fields]
    for row in rows:
        row_changed = False
        for field in fillable_fields:
            if is_filler(row.get(field)) and clean(row.get(field)) != UNKNOWN_VALUE:
                row[field] = UNKNOWN_VALUE
                replacements += 1
                row_changed = True
        changed_stops += row_changed
    return replacements, changed_stops


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    # OneDrive can deny atomic replacement while still allowing a normal file update.
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def validate_local(
    data_fields: list[str],
    data_rows: list[dict[str, str]],
    stop_fields: list[str],
    stop_rows: list[dict[str, str]],
    report: Report,
) -> None:
    missing_fields = [field for field in REQUIRED_DATA_FIELDS if field not in data_fields]
    if missing_fields:
        report.error(f"heat CSV is missing columns: {', '.join(missing_fields)}")
        return

    missing_stop_fields = [field for field in ("stop_id", "stop_lat", "stop_lon") if field not in stop_fields]
    if missing_stop_fields:
        report.error(f"stops CSV is missing columns: {', '.join(missing_stop_fields)}")
        return

    data_ids = [clean(row.get("stop_id")) for row in data_rows]
    stop_ids = [clean(row.get("stop_id")) for row in stop_rows]
    blank_ids = sum(not stop_id for stop_id in data_ids)
    if blank_ids:
        report.error(f"heat CSV has {blank_ids} blank stop_id value(s)")
    repeated = duplicates(stop_id for stop_id in data_ids if stop_id)
    if repeated:
        report.error(f"heat CSV has duplicate stop_id values: {examples(repeated)}")

    data_id_set = set(data_ids) - {""}
    stop_id_set = set(stop_ids) - {""}
    missing_ids = sorted(stop_id_set - data_id_set)
    extra_ids = sorted(data_id_set - stop_id_set)
    if missing_ids:
        report.error(f"{len(missing_ids)} GTFS stops are absent from the heat CSV: {examples(missing_ids)}")
    if extra_ids:
        report.error(f"{len(extra_ids)} heat rows are not in the GTFS stops file: {examples(extra_ids)}")

    numeric_limits = {
        "heat_vulnerability_index": (0.0, 10.0),
        "tree_canopy_pct": (0.0, 1.0),
        # Broad corruption guard; the source values are Fahrenheit-like surface temperatures.
        "lst_median": (-100.0, 200.0),
    }
    for field, (minimum, maximum) in numeric_limits.items():
        invalid: list[str] = []
        out_of_range: list[str] = []
        for row in data_rows:
            raw = clean(row.get(field))
            if is_filler(raw):
                continue
            parsed = number(raw)
            if parsed is None:
                invalid.append(clean(row.get("stop_id")))
            elif not minimum <= parsed <= maximum:
                out_of_range.append(clean(row.get("stop_id")))
        if invalid:
            report.error(f"{field} is non-numeric for stop(s): {examples(invalid)}")
        if out_of_range:
            report.error(
                f"{field} is outside [{minimum:g}, {maximum:g}] for stop(s): {examples(out_of_range)}"
            )

    bad_labels = sorted(
        {
            clean(row.get("heat_vulnerability_label"))
            for row in data_rows
            if not is_filler(row.get("heat_vulnerability_label"))
            and clean(row.get("heat_vulnerability_label")) not in VALID_LABELS
        }
    )
    if bad_labels:
        report.error(f"unrecognized heat vulnerability labels: {examples(bad_labels)}")

    no_heat = [
        clean(row.get("stop_id"))
        for row in data_rows
        if all(is_filler(row.get(field)) for field in HEAT_FIELDS)
    ]
    if no_heat:
        report.warning(
            f"{len(no_heat)} stop(s) have all heat fields marked {UNKNOWN_VALUE}: {examples(no_heat)}"
        )


def fetch_geojson(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "tampa-shade-heat-verifier/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def first_property(properties: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in properties and properties[name] is not None:
            return properties[name]
    return None


def iter_rings(geometry: dict[str, Any]) -> Iterable[list[list[list[float]]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "Polygon":
        yield coordinates
    elif geometry_type == "MultiPolygon":
        yield from coordinates


def point_on_segment(x: float, y: float, a: list[float], b: list[float]) -> bool:
    cross = (x - a[0]) * (b[1] - a[1]) - (y - a[1]) * (b[0] - a[0])
    if abs(cross) > 1e-10:
        return False
    return min(a[0], b[0]) - 1e-10 <= x <= max(a[0], b[0]) + 1e-10 and min(
        a[1], b[1]
    ) - 1e-10 <= y <= max(a[1], b[1]) + 1e-10


def point_in_ring(x: float, y: float, ring: list[list[float]]) -> bool:
    inside = False
    for index, current in enumerate(ring):
        previous = ring[index - 1]
        if point_on_segment(x, y, previous, current):
            return True
        if (current[1] > y) != (previous[1] > y):
            crossing_x = (previous[0] - current[0]) * (y - current[1]) / (
                previous[1] - current[1]
            ) + current[0]
            if x < crossing_x:
                inside = not inside
    return inside


def point_in_geometry(x: float, y: float, geometry: dict[str, Any]) -> bool:
    for polygon in iter_rings(geometry):
        if polygon and point_in_ring(x, y, polygon[0]):
            if not any(point_in_ring(x, y, hole) for hole in polygon[1:]):
                return True
    return False


def feature_bbox(feature: dict[str, Any]) -> tuple[float, float, float, float] | None:
    points = [point for polygon in iter_rings(feature.get("geometry") or {}) for ring in polygon for point in ring]
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def source_value(feature: dict[str, Any], field: str) -> str | float | None:
    raw = first_property(feature.get("properties") or {}, SOURCE_FIELD_NAMES[field])
    if field == "heat_vulnerability_label":
        return clean(raw) or None
    return number(raw)


def values_match(local: str, source: str | float | None, tolerance: float) -> bool:
    if source is None:
        return is_filler(local)
    if isinstance(source, str):
        return clean(local) == source
    local_number = number(local)
    return local_number is not None and math.isclose(local_number, source, abs_tol=tolerance, rel_tol=0.0)


def validate_live(
    data_rows: list[dict[str, str]],
    stop_rows: list[dict[str, str]],
    geojson: dict[str, Any],
    tolerance: float,
    report: Report,
) -> None:
    features = geojson.get("features") or []
    indexed = [(feature_bbox(feature), feature) for feature in features]
    indexed = [(bbox, feature) for bbox, feature in indexed if bbox is not None]
    if not indexed:
        report.error("source response contained no usable Polygon or MultiPolygon features")
        return

    local_by_id = {clean(row.get("stop_id")): row for row in data_rows}
    unmatched: list[str] = []
    mismatches: dict[str, list[str]] = {field: [] for field in HEAT_FIELDS}
    ambiguous: list[str] = []

    for stop in stop_rows:
        stop_id = clean(stop.get("stop_id"))
        longitude = number(stop.get("stop_lon"))
        latitude = number(stop.get("stop_lat"))
        if not stop_id or longitude is None or latitude is None or stop_id not in local_by_id:
            continue
        matches = [
            feature
            for (min_x, min_y, max_x, max_y), feature in indexed
            if min_x <= longitude <= max_x
            and min_y <= latitude <= max_y
            and point_in_geometry(longitude, latitude, feature.get("geometry") or {})
        ]
        if not matches:
            unmatched.append(stop_id)
            if any(not is_filler(local_by_id[stop_id].get(field)) for field in HEAT_FIELDS):
                for field in HEAT_FIELDS:
                    if not is_filler(local_by_id[stop_id].get(field)):
                        mismatches[field].append(stop_id)
            continue
        if len(matches) > 1:
            ambiguous.append(stop_id)
        feature = matches[0]
        for field in HEAT_FIELDS:
            if not values_match(local_by_id[stop_id].get(field, ""), source_value(feature, field), tolerance):
                mismatches[field].append(stop_id)

    if unmatched:
        report.warning(
            f"{len(unmatched)} GTFS stop(s) fall outside every source polygon: {examples(unmatched)}"
        )
    if ambiguous:
        report.warning(f"{len(ambiguous)} stop(s) intersect multiple source polygons: {examples(ambiguous)}")
    for field, stop_ids in mismatches.items():
        if stop_ids:
            report.error(
                f"{field} differs from the source for {len(stop_ids)} stop(s): {examples(stop_ids)}"
            )


def print_report(report: Report, checked_rows: int, live: bool) -> None:
    mode = "local and live source" if live else "local integrity"
    print(f"Checked {checked_rows} heat rows ({mode}).")
    for warning in report.warnings:
        print(f"WARNING: {warning}")
    for error in report.errors:
        print(f"ERROR: {error}")
    if report.errors:
        print(f"FAILED: {len(report.errors)} error(s), {len(report.warnings)} warning(s).")
    else:
        print(f"PASSED: 0 errors, {len(report.warnings)} warning(s).")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="heat-enriched CSV to verify")
    parser.add_argument("--stops", type=Path, default=DEFAULT_STOPS, help="GTFS stops CSV")
    parser.add_argument("--live", action="store_true", help="compare values with the county ArcGIS layer")
    parser.add_argument("--url", default=DEFAULT_URL, help="override the live ArcGIS GeoJSON query URL")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="report filler values without replacing them with Unknown",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.000005,
        help="absolute tolerance for numeric source comparisons (default: 0.000005)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = Report(errors=[], warnings=[])
    try:
        data_fields, data_rows = read_csv(args.data)
        stop_fields, stop_rows = read_csv(args.stops)
    except (OSError, csv.Error) as exc:
        print(f"ERROR: could not read input CSV: {exc}", file=sys.stderr)
        return 2

    filler_count = sum(
        is_filler(row.get(field)) and clean(row.get(field)) != UNKNOWN_VALUE
        for row in data_rows
        for field in HEAT_FIELDS
        if field in data_fields
    )
    if filler_count and args.check_only:
        report.error(
            f"heat CSV contains {filler_count} blank or filler heat value(s); "
            "rerun without --check-only"
        )
    elif filler_count:
        _, changed_stops = fill_unknown_heat_values(data_rows, data_fields)
        try:
            write_csv(args.data, data_fields, data_rows)
        except OSError as exc:
            print(f"ERROR: could not write normalized heat CSV: {exc}", file=sys.stderr)
            return 2
        print(
            f"Filled {filler_count} blank or filler heat value(s) with {UNKNOWN_VALUE} "
            f"across {changed_stops} stop(s) in {args.data}."
        )

    validate_local(data_fields, data_rows, stop_fields, stop_rows, report)
    if args.live and not report.errors:
        try:
            geojson = fetch_geojson(args.url)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"ERROR: could not fetch or parse the source layer: {exc}", file=sys.stderr)
            return 2
        validate_live(data_rows, stop_rows, geojson, args.tolerance, report)

    print_report(report, len(data_rows), args.live)
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
