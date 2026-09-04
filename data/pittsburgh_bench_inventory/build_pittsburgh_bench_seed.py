#!/usr/bin/env python3
"""Build the City of Pittsburgh Stop-GIS bench-inventory starter dataset.

The output is deliberately a source-evidence frame. PRT and OpenStreetMap
amenity fields are not converted into reviewed Stop-GIS assessments.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PRT_STOPS_URL = (
    "https://services3.arcgis.com/544gNI3xxlFIWuTc/arcgis/rest/services/"
    "Transit_Stops_%28system%29/FeatureServer/0"
)
PRT_AMENITIES_URL = (
    "https://services3.arcgis.com/544gNI3xxlFIWuTc/arcgis/rest/services/"
    "PRT_Current_Shelter_Locations/FeatureServer/0"
)
PRT_STOPS_ITEM_URL = "https://www.arcgis.com/home/item.html?id=a29f37608eb34c3895332ff99eea9b17"
PRT_AMENITIES_ITEM_URL = "https://www.arcgis.com/home/item.html?id=73d1faab8d3441babcd3463ef6987559"
PRT_TERMS_URL = (
    "https://www.rideprt.org/business-center/developer-resources/"
    "developer-license-agreement/"
)
OSM_COPYRIGHT_URL = "https://www.openstreetmap.org/copyright"
ODBL_URL = "https://opendatacommons.org/licenses/odbl/1-0/"
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
STOP_WHERE = "mode = 'BUS' AND muni = 'Pittsburgh city (Allegheny, PA)'"
AMENITY_WHERE = "amenity_type = 'shelter' AND mode IN ('BUS', 'BUS, RAIL')"
STOP_FIELDS = (
    "OBJECTID",
    "stop_id",
    "stop_code",
    "stop_name",
    "stop_lat",
    "stop_lon",
    "route_code",
    "stop_route",
    "direction",
    "trips_wd",
    "hood",
    "muni",
    "feed_version",
    "mode",
)
AMENITY_FIELDS = (
    "OBJECTID",
    "stop_id",
    "stop_code",
    "amenity_type",
    "amenity_subtype_1",
    "amenity_subtype_2",
    "upload_date",
    "mode",
)
OVERPASS_QUERY = """[out:json][timeout:60];
area["name"="Pittsburgh"]["boundary"="administrative"]["admin_level"="8"]->.a;
(
  nwr["highway"="bus_stop"](area.a);
  nwr["public_transport"="platform"]["bus"="yes"](area.a);
);
out meta center tags;
"""
OUTPUT_COLUMNS = (
    "stop_id",
    "stop_name",
    "stop_lat",
    "stop_lon",
    "stop_code",
    "routes",
    "route_display",
    "direction",
    "bench",
    "weekday_trips",
    "neighborhood",
    "municipality",
    "prt_feed_version",
    "prt_shelter_listed",
    "prt_shelter_owner",
    "prt_shelter_mobility",
    "prt_amenity_upload_date",
    "osm_match_status",
    "osm_element_type",
    "osm_element_id",
    "osm_feature_url",
    "osm_ref",
    "osm_bench_tag",
    "osm_shelter_tag",
    "osm_seats_tag",
    "osm_feature_timestamp",
    "osm_match_distance_m",
    "source_accessed_utc",
    "bench_review_status",
)
CORE_AND_PROVENANCE_FIELDS = {
    "stop_id",
    "stop_name",
    "stop_lat",
    "stop_lon",
    "stop_code",
    "routes",
    "route_display",
    "direction",
    "bench",
    "weekday_trips",
    "neighborhood",
    "municipality",
    "source_accessed_utc",
    "bench_review_status",
}
PRT_EVIDENCE_FIELDS = {
    "prt_feed_version",
    "prt_shelter_listed",
    "prt_shelter_owner",
    "prt_shelter_mobility",
    "prt_amenity_upload_date",
}
OSM_EVIDENCE_FIELDS = {
    "osm_match_status",
    "osm_element_type",
    "osm_element_id",
    "osm_feature_url",
    "osm_ref",
    "osm_bench_tag",
    "osm_shelter_tag",
    "osm_seats_tag",
    "osm_feature_timestamp",
    "osm_match_distance_m",
}
ZIP_MEMBERS = (
    "pittsburgh_bus_stops_stop_gis_import.csv",
    "build_pittsburgh_bench_seed.py",
    "build_summary.json",
    "README.md",
    "DATA_LICENSE.md",
    "CITATION.md",
    "CITATION.cff",
)
USER_AGENT = "Stop-GIS Pittsburgh bench starter builder/1.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _request_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    post: bool = False,
    timeout: int = 120,
    attempts: int = 4,
) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(params or {}).encode("utf-8")
    request_url = url if post or not encoded else f"{url}?{encoded.decode('ascii')}"
    data = encoded if post else None
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            request_url,
            data=data,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise RuntimeError(f"Expected a JSON object from {url}")
            if payload.get("error"):
                raise RuntimeError(f"Source returned an error: {payload['error']}")
            return payload
        except (OSError, ValueError, urllib.error.URLError, RuntimeError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"Unable to retrieve {url}: {last_error}") from last_error


def arcgis_item_metadata(layer_url: str) -> dict[str, Any]:
    layer = _request_json(layer_url, params={"f": "json"})
    item_id = str(layer.get("serviceItemId") or "")
    item = (
        _request_json(
            f"https://www.arcgis.com/sharing/rest/content/items/{item_id}",
            params={"f": "json"},
        )
        if item_id
        else {}
    )
    return {
        "name": layer.get("name", ""),
        "service_item_id": item_id,
        "title": item.get("title", ""),
        "license_info": re.sub(r"<[^>]+>", "", str(item.get("licenseInfo") or "")).strip(),
        "modified_epoch_ms": item.get("modified"),
        "max_record_count": int(layer.get("maxRecordCount") or 2000),
    }


def arcgis_count(layer_url: str, where: str) -> int:
    payload = _request_json(
        f"{layer_url}/query",
        params={"where": where, "returnCountOnly": "true", "f": "json"},
    )
    return int(payload["count"])


def fetch_arcgis_features(
    layer_url: str,
    *,
    where: str,
    fields: Iterable[str],
    page_size: int,
) -> tuple[list[dict[str, Any]], int]:
    """Retrieve every record using stable OBJECTID ordering and result offsets."""
    expected_before = arcgis_count(layer_url, where)
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        payload = _request_json(
            f"{layer_url}/query",
            params={
                "where": where,
                "outFields": ",".join(fields),
                "returnGeometry": "false",
                "orderByFields": "OBJECTID ASC",
                "resultOffset": offset,
                "resultRecordCount": page_size,
                "f": "json",
            },
        )
        page = [feature.get("attributes", {}) for feature in payload.get("features", [])]
        rows.extend(page)
        offset += len(page)
        if not page or not payload.get("exceededTransferLimit"):
            break
    expected_after = arcgis_count(layer_url, where)
    if expected_before != expected_after:
        raise RuntimeError(
            "The ArcGIS source changed during pagination "
            f"({expected_before} records before, {expected_after} after); rerun the builder."
        )
    if len(rows) != expected_after:
        raise RuntimeError(f"ArcGIS pagination returned {len(rows)} of {expected_after} records")
    object_ids = [row.get("OBJECTID") for row in rows]
    if None in object_ids or len(object_ids) != len(set(object_ids)):
        raise RuntimeError("ArcGIS pagination returned missing or duplicate OBJECTID values")
    return rows, expected_after


def fetch_osm() -> tuple[list[dict[str, Any]], str, int]:
    errors: list[str] = []
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            payload = _request_json(
                endpoint,
                params={"data": OVERPASS_QUERY},
                post=True,
                timeout=180,
                attempts=2,
            )
            if payload.get("remark") and not payload.get("elements"):
                raise RuntimeError(str(payload["remark"]))
            raw = payload.get("elements", [])
            deduplicated: dict[tuple[str, int], dict[str, Any]] = {}
            for element in raw:
                element_type = str(element.get("type") or "")
                element_id = element.get("id")
                if element_type in {"node", "way", "relation"} and isinstance(element_id, int):
                    deduplicated[(element_type, element_id)] = element
            return list(deduplicated.values()), endpoint, len(raw)
        except RuntimeError as exc:
            errors.append(f"{endpoint}: {exc}")
    raise RuntimeError("All Overpass endpoints failed: " + " | ".join(errors))


def clean_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return str(value).strip()


def normalize_code(value: Any) -> str:
    text = clean_scalar(value).casefold()
    if not text:
        return ""
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        return str(int(float(text)))
    return re.sub(r"\s+", "", text)


def ref_codes(value: Any) -> set[str]:
    return {code for part in re.split(r"[;,]", clean_scalar(value)) if (code := normalize_code(part))}


def route_values(value: Any) -> tuple[str, str]:
    display = clean_scalar(value)
    values = [part.strip() for part in re.split(r"[;,]", display) if part.strip()]
    return ";".join(dict.fromkeys(values)), display


def arcgis_date(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        instant = datetime.fromtimestamp(float(value) / 1000, timezone.utc)
    except (TypeError, ValueError, OSError):
        return clean_scalar(value)
    return instant.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def join_values(rows: Iterable[dict[str, Any]], field: str) -> str:
    values = sorted({clean_scalar(row.get(field)) for row in rows if clean_scalar(row.get(field))})
    return "; ".join(values)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6_371_008.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius_m * math.asin(math.sqrt(a))


def osm_coordinates(element: dict[str, Any]) -> tuple[float, float] | None:
    location = element if element.get("type") == "node" else element.get("center", {})
    try:
        lat, lon = float(location["lat"]), float(location["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    return lat, lon


def index_shelters(
    amenities: list[dict[str, Any]],
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
]:
    by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in amenities:
        stop_id = clean_scalar(row.get("stop_id"))
        code = normalize_code(row.get("stop_code"))
        if stop_id:
            by_id[stop_id].append(row)
        if code:
            by_code[code].append(row)
    return by_id, by_code


def index_osm(elements: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for element in elements:
        tags = element.get("tags") or {}
        if osm_coordinates(element) is None:
            continue
        for code in ref_codes(tags.get("ref")):
            by_code[code].append(element)
    return by_code


def match_osm(
    stop: dict[str, Any], osm_by_code: dict[str, list[dict[str, Any]]]
) -> tuple[dict[str, Any] | None, float | None, bool]:
    code = normalize_code(stop.get("stop_code"))
    if not code or not osm_by_code.get(code):
        return None, None, False
    stop_lat, stop_lon = float(stop["stop_lat"]), float(stop["stop_lon"])
    ranked = []
    for element in osm_by_code[code]:
        coordinates = osm_coordinates(element)
        if coordinates is not None:
            ranked.append((haversine_m(stop_lat, stop_lon, *coordinates), element))
    if not ranked:
        return None, None, False
    distance, element = min(ranked, key=lambda pair: pair[0])
    return (element, distance, False) if distance <= 250 else (None, distance, True)


def build_rows(
    stops: list[dict[str, Any]],
    amenities: list[dict[str, Any]],
    osm_elements: list[dict[str, Any]],
    accessed_utc: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    shelters_by_id, shelters_by_code = index_shelters(amenities)
    osm_by_code = index_osm(osm_elements)
    results: list[dict[str, Any]] = []
    stats = defaultdict(int)
    for stop in sorted(stops, key=lambda row: clean_scalar(row.get("stop_id"))):
        stop_id = clean_scalar(stop.get("stop_id"))
        code = normalize_code(stop.get("stop_code"))
        shelter_rows = shelters_by_id.get(stop_id, [])
        if shelter_rows:
            stats["shelter_matches_by_stop_id"] += 1
        elif code:
            shelter_rows = shelters_by_code.get(code, [])
            if shelter_rows:
                stats["shelter_matches_by_stop_code_fallback"] += 1
        element, distance, rejected = match_osm(stop, osm_by_code)
        if rejected:
            stats["osm_exact_code_rejected_over_250m"] += 1
        routes, route_display = route_values(stop.get("route_code"))
        row = {
            "stop_id": stop_id,
            "stop_name": clean_scalar(stop.get("stop_name")),
            "stop_lat": clean_scalar(stop.get("stop_lat")),
            "stop_lon": clean_scalar(stop.get("stop_lon")),
            "stop_code": clean_scalar(stop.get("stop_code")),
            "routes": routes,
            "route_display": route_display,
            "direction": clean_scalar(stop.get("direction")),
            "bench": "",
            "weekday_trips": clean_scalar(stop.get("trips_wd")),
            "neighborhood": clean_scalar(stop.get("hood")),
            "municipality": clean_scalar(stop.get("muni")),
            "prt_feed_version": clean_scalar(stop.get("feed_version")),
            "prt_shelter_listed": "yes" if shelter_rows else "no",
            "prt_shelter_owner": join_values(shelter_rows, "amenity_subtype_1"),
            "prt_shelter_mobility": join_values(shelter_rows, "amenity_subtype_2"),
            "prt_amenity_upload_date": max(
                (arcgis_date(item.get("upload_date")) for item in shelter_rows), default=""
            ),
            "osm_match_status": "unmatched",
            "osm_element_type": "",
            "osm_element_id": "",
            "osm_feature_url": "",
            "osm_ref": "",
            "osm_bench_tag": "",
            "osm_shelter_tag": "",
            "osm_seats_tag": "",
            "osm_feature_timestamp": "",
            "osm_match_distance_m": "",
            "source_accessed_utc": accessed_utc,
            "bench_review_status": "unreviewed",
        }
        if element is not None and distance is not None:
            tags = element.get("tags") or {}
            status = "exact_stop_code_large_offset" if distance > 50 else "exact_stop_code"
            row.update(
                {
                    "osm_match_status": status,
                    "osm_element_type": element["type"],
                    "osm_element_id": str(element["id"]),
                    "osm_feature_url": f"https://www.openstreetmap.org/{element['type']}/{element['id']}",
                    "osm_ref": clean_scalar(tags.get("ref")),
                    "osm_bench_tag": clean_scalar(tags.get("bench")),
                    "bench": {
                        "yes": "present",
                        "no": "absent",
                    }.get(clean_scalar(tags.get("bench")).casefold(), ""),
                    "osm_shelter_tag": clean_scalar(tags.get("shelter")),
                    "osm_seats_tag": clean_scalar(tags.get("seats")),
                    "osm_feature_timestamp": clean_scalar(element.get("timestamp")),
                    "osm_match_distance_m": f"{distance:.1f}",
                }
            )
            stats[status] += 1
        else:
            stats["unmatched"] += 1
        results.append(row)
    return results, dict(stats)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_rows(rows: list[dict[str, Any]], stops: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    required = ("stop_id", "stop_name", "stop_lat", "stop_lon")
    stop_ids = [row["stop_id"] for row in rows]
    if any(not row[field] for row in rows for field in required):
        errors.append("one or more Stop-GIS required fields are blank")
    if len(stop_ids) != len(set(stop_ids)):
        errors.append("stop_id values are not unique")
    coordinates_ok = True
    for row in rows:
        try:
            lat, lon = float(row["stop_lat"]), float(row["stop_lon"])
            coordinates_ok &= math.isfinite(lat) and math.isfinite(lon)
            coordinates_ok &= 40.2 <= lat <= 40.7 and -80.3 <= lon <= -79.7
        except (TypeError, ValueError):
            coordinates_ok = False
    if not coordinates_ok:
        errors.append("coordinates are nonfinite or outside the Pittsburgh-area validation bounds")
    if any(source.get("mode") != "BUS" or source.get("muni") != "Pittsburgh city (Allegheny, PA)" for source in stops):
        errors.append("one or more source rows are outside the requested mode/municipality scope")
    if any(row["bench_review_status"] != "unreviewed" for row in rows):
        errors.append("one or more records has a non-unreviewed assessment status")
    prohibited = {
        "bench_presence",
        "reviewed_label",
        "shading",
        "shade_coverage",
        "shade_sources",
    }.intersection(OUTPUT_COLUMNS)
    if prohibited:
        errors.append(f"prohibited final-assessment columns found: {sorted(prohibited)}")
    if any(
        clean_scalar(row.get("bench")) not in {"", "present", "absent"}
        for row in rows
    ):
        errors.append("bench source values do not map to bench_presence inputs")
    classified_columns = (
        CORE_AND_PROVENANCE_FIELDS | PRT_EVIDENCE_FIELDS | OSM_EVIDENCE_FIELDS
    )
    evidence_prefixes_ok = (
        set(OUTPUT_COLUMNS) == classified_columns
        and all(field.startswith("prt_") for field in PRT_EVIDENCE_FIELDS)
        and all(field.startswith("osm_") for field in OSM_EVIDENCE_FIELDS)
    )
    if not evidence_prefixes_ok:
        unclassified = sorted(set(OUTPUT_COLUMNS) - classified_columns)
        missing = sorted(classified_columns - set(OUTPUT_COLUMNS))
        errors.append(
            "source-evidence schema classification or prefixes are invalid "
            f"(unclassified={unclassified}, missing={missing})"
        )
    if errors:
        raise RuntimeError("Dataset validation failed: " + "; ".join(errors))
    return {
        "required_fields_populated": True,
        "unique_stop_ids": True,
        "coordinates_finite_and_in_pittsburgh_area": True,
        "source_scope_mode_bus_and_pittsburgh_city": True,
        "source_evidence_prefixes_preserved": evidence_prefixes_ok,
        "no_shade_study_columns": True,
        "all_assessments_unreviewed": True,
        "bench_column_maps_to_bench_presence": True,
    }


def validate_stop_gis_import(csv_path: Path, expected_rows: int) -> dict[str, Any]:
    try:
        import pandas as pd

        repository_root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(repository_root))
        from stop_gis.builder.imports import prepare_stop_dataset

        raw = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
        prepared = prepare_stop_dataset(
            raw,
            {"agency": "Pittsburgh Regional Transit"},
            [
                {"name": "No Shade"},
                {"name": "Limited Shade"},
                {"name": "Significant Shade"},
                {"name": "Needs Review"},
            ],
        )
        required_preserved = all(field in prepared.columns for field in OUTPUT_COLUMNS[:4])
        row_count_preserved = len(prepared) == expected_rows
        if not required_preserved or not row_count_preserved:
            raise RuntimeError("Stop-GIS import preparation dropped rows or required fields")
        return {
            "passed": True,
            "prepared_rows": len(prepared),
            "required_fields_preserved": required_preserved,
        }
    except ImportError as exc:
        raise RuntimeError(f"Stop-GIS import validation dependencies are unavailable: {exc}") from exc


def create_zip(output_dir: Path) -> tuple[Path, list[str]]:
    zip_path = output_dir / "Pittsburgh_Stop_GIS_Bench_Starter.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ZIP_MEMBERS:
            archive.write(output_dir / name, arcname=name)
    with zipfile.ZipFile(zip_path) as archive:
        bad_member = archive.testzip()
        names = archive.namelist()
    if bad_member or names != list(ZIP_MEMBERS):
        raise RuntimeError(f"ZIP validation failed (bad member: {bad_member!r}; members: {names!r})")
    return zip_path, names


def copy_static_artifacts(output_dir: Path) -> None:
    """Make alternate output directories independently reproducible."""
    source_dir = Path(__file__).resolve().parent
    for name in (
        "build_pittsburgh_bench_seed.py",
        "README.md",
        "DATA_LICENSE.md",
        "CITATION.md",
        "CITATION.cff",
    ):
        source = source_dir / name
        destination = output_dir / name
        if not source.is_file():
            raise RuntimeError(f"Missing required source artifact: {source}")
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Directory for the CSV, summary, and ZIP (default: script directory).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    copy_static_artifacts(output_dir)
    accessed_utc = utc_now()

    stops_metadata = arcgis_item_metadata(PRT_STOPS_URL)
    amenities_metadata = arcgis_item_metadata(PRT_AMENITIES_URL)
    stops, stop_count = fetch_arcgis_features(
        PRT_STOPS_URL,
        where=STOP_WHERE,
        fields=STOP_FIELDS,
        page_size=min(2000, stops_metadata["max_record_count"]),
    )
    amenities, amenity_count = fetch_arcgis_features(
        PRT_AMENITIES_URL,
        where=AMENITY_WHERE,
        fields=AMENITY_FIELDS,
        page_size=min(2000, amenities_metadata["max_record_count"]),
    )
    osm_elements, overpass_endpoint, osm_raw_count = fetch_osm()
    rows, match_stats = build_rows(stops, amenities, osm_elements, accessed_utc)
    validation = validate_rows(rows, stops)

    csv_path = output_dir / ZIP_MEMBERS[0]
    write_csv(csv_path, rows)
    import_validation = validate_stop_gis_import(csv_path, len(rows))
    validation["stop_gis_csv_import_preparation"] = import_validation

    bench_yes = sum(row["osm_bench_tag"].casefold() == "yes" for row in rows)
    bench_no = sum(row["osm_bench_tag"].casefold() == "no" for row in rows)
    mapped_bench_present = sum(row["bench"] == "present" for row in rows)
    mapped_bench_absent = sum(row["bench"] == "absent" for row in rows)
    shelter_stops = sum(row["prt_shelter_listed"] == "yes" for row in rows)
    summary = {
        "built_at_utc": accessed_utc,
        "scope": {"mode": "BUS", "municipality": "Pittsburgh city (Allegheny, PA)"},
        "counts": {
            "prt_scoped_bus_stops": stop_count,
            "prt_filtered_shelter_records_systemwide": amenity_count,
            "pittsburgh_stops_with_prt_shelter_listing": shelter_stops,
            "osm_elements_returned_before_deduplication": osm_raw_count,
            "osm_elements_after_type_id_deduplication": len(osm_elements),
            "osm_exact_stop_code_matches_within_50m": match_stats.get("exact_stop_code", 0),
            "osm_exact_stop_code_matches_over_50m_through_250m": match_stats.get(
                "exact_stop_code_large_offset", 0
            ),
            "osm_exact_code_candidates_rejected_over_250m": match_stats.get(
                "osm_exact_code_rejected_over_250m", 0
            ),
            "osm_unmatched_prt_stops": match_stats.get("unmatched", 0),
            "matched_records_with_osm_bench_yes": bench_yes,
            "matched_records_with_osm_bench_no": bench_no,
            "bench_mode_prefill_present": mapped_bench_present,
            "bench_mode_prefill_absent": mapped_bench_absent,
            "prt_shelter_matches_by_stop_id": match_stats.get("shelter_matches_by_stop_id", 0),
            "prt_shelter_matches_by_stop_code_fallback": match_stats.get(
                "shelter_matches_by_stop_code_fallback", 0
            ),
        },
        "sources": {
            "prt_stops": {
                "url": PRT_STOPS_URL,
                "item_url": PRT_STOPS_ITEM_URL,
                "accessed_utc": accessed_utc,
                "governing_terms_url": PRT_TERMS_URL,
                "citation": (
                    "Pittsburgh Regional Transit. (2026). PRT Stops - Current "
                    "(full system) [Feature layer]."
                ),
                "where": STOP_WHERE,
                **stops_metadata,
            },
            "prt_amenities": {
                "url": PRT_AMENITIES_URL,
                "item_url": PRT_AMENITIES_ITEM_URL,
                "accessed_utc": accessed_utc,
                "governing_terms_url": PRT_TERMS_URL,
                "citation": (
                    "Pittsburgh Regional Transit. (2026). PRT Stop Amenities - "
                    "Current [Feature layer]."
                ),
                "where": AMENITY_WHERE,
                **amenities_metadata,
            },
            "openstreetmap": {
                "overpass_endpoint_used": overpass_endpoint,
                "query": OVERPASS_QUERY,
                "copyright": "OpenStreetMap contributors",
                "license": "ODbL 1.0",
                "license_url": ODBL_URL,
                "attribution_url": OSM_COPYRIGHT_URL,
                "accessed_utc": accessed_utc,
                "citation": (
                    "OpenStreetMap contributors. (2026). OpenStreetMap "
                    "[Database]. OpenStreetMap Foundation."
                ),
            },
        },
        "citation": {
            "preferred": (
                "Stop-GIS contributors. (2026). Pittsburgh bus-stop bench-inventory "
                "starter dataset (Version 0.1.0) [Data set]. Stop-GIS."
            ),
            "human_readable_file": "CITATION.md",
            "machine_readable_file": "CITATION.cff",
            "terms_and_attribution_file": "DATA_LICENSE.md",
        },
        "matching": {
            "public_stop_code_normalization": "trim/casefold; remove whitespace; normalize numeric leading zeros",
            "osm_ref_separators": ["semicolon", "comma"],
            "candidate_selection": "closest exact public-stop-code match",
            "large_offset_threshold_m": 50,
            "rejection_threshold_m": 250,
            "nearest_neighbor_without_code_agreement": False,
        },
        "validation": validation,
        "artifacts": {"csv_sha256": sha256(csv_path)},
        "limitations": [
            "This is an unreviewed starter dataset, not a verified bench census.",
            "prt_shelter_listed=no means no matching record appeared in the public layer.",
            "OSM tags are provisional, contributor-maintained evidence and may be incomplete or stale.",
            "A shelter listing does not imply that a bench is present.",
        ],
    }
    summary_path = output_dir / "build_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    for required_document in ("README.md", "DATA_LICENSE.md", "CITATION.md", "CITATION.cff"):
        if not (output_dir / required_document).is_file():
            raise RuntimeError(f"Missing required documentation file: {output_dir / required_document}")
    zip_path, zip_members = create_zip(output_dir)
    summary["validation"]["zip_opens_and_contains_documented_files"] = True
    summary["artifacts"]["zip_members"] = zip_members
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # Refresh the archive after adding final ZIP validation metadata to the summary.
    zip_path, _ = create_zip(output_dir)

    counts = summary["counts"]
    accepted = counts["osm_exact_stop_code_matches_within_50m"] + counts[
        "osm_exact_stop_code_matches_over_50m_through_250m"
    ]
    print(f"Built {stop_count:,} scoped PRT bus stops at {accessed_utc}.")
    print(f"PRT shelter-listed City stops: {shelter_stops:,}.")
    print(
        f"OSM elements: {len(osm_elements):,}; accepted code matches: {accepted:,}; "
        f">50 m flags: {counts['osm_exact_stop_code_matches_over_50m_through_250m']:,}; "
        f">250 m rejected: {counts['osm_exact_code_candidates_rejected_over_250m']:,}."
    )
    print(f"OSM bench tag leads: yes={bench_yes:,}, no={bench_no:,}.")
    print("Validation passed: required fields, uniqueness, coordinates, scope, unreviewed status, import, ZIP.")
    print(f"Wrote {csv_path} and {zip_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
