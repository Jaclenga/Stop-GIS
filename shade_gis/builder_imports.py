import io
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import urllib.parse
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from shade_gis.identifiers import canonical_identifier
from shade_gis.shade_dimensions import (
    infer_sources_from_legacy_category,
    normalize_shade_coverage,
    split_shade_sources,
)


DEFAULT_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_API_BYTES = 15 * 1024 * 1024
DEFAULT_MAX_ZIP_MEMBERS = 256
DEFAULT_MAX_ZIP_MEMBER_BYTES = 80 * 1024 * 1024
DEFAULT_MAX_ZIP_UNCOMPRESSED_BYTES = 150 * 1024 * 1024
DEFAULT_PRIORITY_WEIGHTS = {"ridership": 0.5, "low_shade": 0.5}
API_FETCH_TIMEOUT_SECONDS = 30
API_MAX_REDIRECTS = 5

REVIEW_STATUS_NAMES = {
    "Unlabeled",
    "Needs Review",
    "Crowd Reviewed",
    "Expert Reviewed",
    "Accepted",
    "Disputed",
    "Archived",
}
REQUIRED_STOP_FIELDS = ["stop_id", "stop_name", "stop_lat", "stop_lon"]
OPTIONAL_FIELDS = [
    "agency",
    "routes",
    "municipality",
    "shading",
    "shade_coverage",
    "shade_sources",
    "review_status",
    "confidence",
    "ridership",
    "nearby_destinations",
]
FIELD_ALIASES = {
    "stop_id": ["stop_id", "stopid", "stop_code", "id", "objectid"],
    "stop_name": ["stop_name", "stopname", "name", "stop_desc", "description"],
    "stop_lat": ["stop_lat", "stoplat", "latitude", "lat", "y"],
    "stop_lon": ["stop_lon", "stoplon", "longitude", "lon", "lng", "long", "x"],
    "agency": ["agency", "agency_name", "operator"],
    "routes": ["routes", "route", "route_short_name", "route_ids"],
    "municipality": ["municipality", "city", "jurisdiction", "neighborhood"],
    "shading": ["shading", "shade", "shade_category", "shade_label"],
    "shade_coverage": ["shade_coverage", "coverage"],
    "shade_sources": ["shade_sources", "shade_source", "source"],
    "review_status": ["review_status", "status"],
    "confidence": ["confidence", "score"],
    "ridership": ["ridership", "boardings", "ons", "passengers"],
    "nearby_destinations": ["nearby_destinations", "destinations", "destination", "nearby_places", "places"],
}


def timestamp_with_timezone() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def hex_to_rgb(value: str) -> list[int]:
    text = str(value or "").strip().lstrip("#")
    if len(text) != 6:
        return [128, 128, 128]
    try:
        return [int(text[index : index + 2], 16) for index in (0, 2, 4)]
    except ValueError:
        return [128, 128, 128]


def normalize_hex_color(value: Any, fallback: str = "#808080") -> str:
    text = str(value or "").strip()
    if not text.startswith("#"):
        text = f"#{text}"
    if len(text) != 7:
        return fallback
    try:
        int(text[1:], 16)
    except ValueError:
        return fallback
    return text.lower()


def scalar_text(value: Any) -> str:
    """Return trimmed scalar text and reject nested tabular values."""

    if not pd.api.types.is_scalar(value):
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        return ""
    return str(value).strip()


def normalize_category(value: Any, taxonomy: list[dict[str, Any]]) -> str:
    categories = [
        normalize_shade_coverage(item.get("name", ""), "")
        for item in taxonomy
        if normalize_shade_coverage(item.get("name", ""), "")
    ]
    fallback = "Needs Review" if "Needs Review" in categories else (categories[-1] if categories else "Needs Review")
    if not scalar_text(value):
        return fallback
    coverage = normalize_shade_coverage(value, fallback)
    return coverage if coverage in categories else fallback


def normalize_review_status(value: Any) -> str:
    if not pd.api.types.is_scalar(value):
        return "Needs Review"
    text = scalar_text(value)
    if not text:
        return "Unlabeled"
    return text if text in REVIEW_STATUS_NAMES else "Needs Review"


def env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def max_upload_bytes() -> int:
    return env_int("SHADE_GIS_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES)


def max_api_bytes() -> int:
    return env_int("SHADE_GIS_MAX_API_BYTES", DEFAULT_MAX_API_BYTES)


def max_zip_members() -> int:
    return env_int("SHADE_GIS_MAX_ZIP_MEMBERS", DEFAULT_MAX_ZIP_MEMBERS)


def max_zip_member_bytes() -> int:
    return env_int("SHADE_GIS_MAX_ZIP_MEMBER_BYTES", DEFAULT_MAX_ZIP_MEMBER_BYTES)


def max_zip_uncompressed_bytes() -> int:
    return env_int("SHADE_GIS_MAX_ZIP_UNCOMPRESSED_BYTES", DEFAULT_MAX_ZIP_UNCOMPRESSED_BYTES)


def format_bytes(value: int) -> str:
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value} bytes"


def validate_bytes_size(contents: bytes, limit: int, label: str) -> None:
    if len(contents) > limit:
        raise ValueError(f"{label} is {format_bytes(len(contents))}; the limit is {format_bytes(limit)}")


def allowed_api_hosts() -> list[str]:
    return [
        host.strip().lower().rstrip(".")
        for host in os.environ.get("SHADE_GIS_ALLOWED_API_HOSTS", "").split(",")
        if host.strip()
    ]


def api_host_matches(host: str, allowed_host: str) -> bool:
    allowed_host = allowed_host.lstrip(".")
    return host == allowed_host or host.endswith(f".{allowed_host}")


def is_private_network_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not address.is_global


def _validated_web_target(
    url: str,
    *,
    label: str,
    allow_private_env: str,
    allowed_hosts: list[str] | None = None,
) -> tuple[str, list[str]]:
    clean_url = url.strip()
    parsed = urllib.parse.urlparse(clean_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"{label} URL must use http or https")
    if parsed.username or parsed.password:
        raise ValueError(f"{label} URL must not include embedded credentials")
    if not parsed.hostname:
        raise ValueError(f"{label} URL must include a host")

    host = parsed.hostname.lower().rstrip(".")
    if allowed_hosts and not any(api_host_matches(host, allowed_host) for allowed_host in allowed_hosts):
        raise ValueError("API URL host is not in SHADE_GIS_ALLOWED_API_HOSTS")

    allow_private = env_flag(allow_private_env)
    if not allow_private and (host == "localhost" or host.endswith(".localhost")):
        raise ValueError(f"Private or localhost {label.lower()} URLs are disabled by default")
    try:
        literal_address = ipaddress.ip_address(host)
    except ValueError:
        literal_address = None
    if literal_address is not None and not allow_private and is_private_network_address(host):
        raise ValueError(f"Private or localhost {label.lower()} URLs are disabled by default")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = sorted(
            {
                ipaddress.ip_address(result[4][0]).compressed
                for result in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            }
        )
    except (ValueError, OSError, socket.gaierror) as error:
        raise ValueError(f"Could not resolve {label} URL host: {host}") from error
    if not addresses:
        raise ValueError(f"Could not resolve {label} URL host: {host}")
    if not allow_private and any(is_private_network_address(address) for address in addresses):
        raise ValueError(f"Private or localhost {label.lower()} URLs are disabled by default")
    return clean_url, addresses


def _validated_api_target(url: str) -> tuple[str, list[str]]:
    return _validated_web_target(
        url,
        label="API",
        allow_private_env="SHADE_GIS_ALLOW_PRIVATE_API_URLS",
        allowed_hosts=allowed_api_hosts(),
    )


def validate_api_url(url: str) -> str:
    clean_url, _addresses = _validated_api_target(url)
    return clean_url


def public_source_url(url: str) -> str:
    """Return provenance-safe URL text without credentials, query, or fragment."""
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    try:
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        return ""
    return urllib.parse.urlunsplit((parsed.scheme, f"{host}{port}", parsed.path, "", ""))


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, address: str, port: int, timeout: int) -> None:
        super().__init__(host, port=port, timeout=timeout)
        self._pinned_address = address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_address, self.port),
            self.timeout,
            self.source_address,
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, port: int, timeout: int) -> None:
        super().__init__(host, port=port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_address = address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_address, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def _open_pinned_api_response(
    url: str, addresses: list[str]
) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    parsed = urllib.parse.urlparse(url)
    host = str(parsed.hostname or "")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    default_port = 443 if parsed.scheme == "https" else 80
    host_label = f"[{host}]" if ":" in host else host
    host_header = host_label if port == default_port else f"{host_label}:{port}"
    request_target = parsed.path or "/"
    if parsed.params:
        request_target += f";{parsed.params}"
    if parsed.query:
        request_target += f"?{parsed.query}"
    last_error: BaseException | None = None
    for address in addresses:
        connection_class = (
            _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
        )
        connection = connection_class(host, address, port, API_FETCH_TIMEOUT_SECONDS)
        try:
            connection.request(
                "GET",
                request_target,
                headers={
                    "Host": host_header,
                    "User-Agent": "Shade-GIS/0.1 (+https://github.com/)",
                    "Accept-Encoding": "identity",
                },
            )
            return connection, connection.getresponse()
        except (OSError, ssl.SSLError, http.client.HTTPException) as error:
            last_error = error
            connection.close()
    raise RuntimeError(f"Could not connect to API URL host: {host}") from last_error


def read_limited_response(response: Any, limit: int) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = 0
        if declared_size > limit:
            raise ValueError(f"API response declares {format_bytes(declared_size)}; the limit is {format_bytes(limit)}")

    buffer = io.BytesIO()
    while True:
        chunk = response.read(64 * 1024)
        if not chunk:
            break
        buffer.write(chunk)
        if buffer.tell() > limit:
            raise ValueError(f"API response exceeded the {format_bytes(limit)} limit")
    return buffer.getvalue()


def validate_zip_bytes(contents: bytes, label: str = "ZIP upload") -> None:
    validate_bytes_size(contents, max_upload_bytes(), label)
    with zipfile.ZipFile(io.BytesIO(contents)) as archive:
        members = archive.infolist()
        if len(members) > max_zip_members():
            raise ValueError(f"{label} contains {len(members)} files; the limit is {max_zip_members()}")
        total_uncompressed = sum(member.file_size for member in members)
        if total_uncompressed > max_zip_uncompressed_bytes():
            raise ValueError(
                f"{label} expands to {format_bytes(total_uncompressed)}; "
                f"the limit is {format_bytes(max_zip_uncompressed_bytes())}"
            )
        for member in members:
            if member.file_size > max_zip_member_bytes():
                raise ValueError(
                    f"{label} member {member.filename!r} expands to {format_bytes(member.file_size)}; "
                    f"the per-file limit is {format_bytes(max_zip_member_bytes())}"
                )


def read_csv_bytes(contents: bytes, *, limit: int | None = None, label: str = "CSV upload") -> pd.DataFrame:
    validate_bytes_size(contents, limit or max_upload_bytes(), label)
    return pd.read_csv(io.BytesIO(contents), dtype=str)


def normalize_column_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def suggest_source_column(target: str, columns: list[str]) -> str:
    normalized_columns = {normalize_column_key(column): column for column in columns}
    for alias in FIELD_ALIASES.get(target, [target]):
        match = normalized_columns.get(normalize_column_key(alias))
        if match:
            return match
    return ""


def geometry_coordinate_pairs(geometry: dict[str, Any] | None) -> list[tuple[float, float]]:
    if not geometry:
        return []
    geometry_type = str(geometry.get("type", "")).lower()
    coordinates = geometry.get("coordinates")
    if geometry_type == "point" and isinstance(coordinates, (list, tuple)) and len(coordinates) >= 2:
        try:
            return [(float(coordinates[0]), float(coordinates[1]))]
        except (TypeError, ValueError):
            return []
    if geometry_type == "geometrycollection":
        pairs: list[tuple[float, float]] = []
        for child in geometry.get("geometries", []) or []:
            pairs.extend(geometry_coordinate_pairs(child))
        return pairs

    pairs: list[tuple[float, float]] = []

    def collect(value: Any) -> None:
        if isinstance(value, (list, tuple)) and len(value) >= 2 and not isinstance(value[0], (list, tuple)):
            try:
                pairs.append((float(value[0]), float(value[1])))
            except (TypeError, ValueError):
                return
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                collect(item)

    collect(coordinates)
    return pairs


def geometry_centroid(geometry: dict[str, Any] | None) -> tuple[float | None, float | None]:
    if not geometry or not geometry_coordinate_pairs(geometry):
        return None, None
    try:
        from shapely.geometry import shape  # type: ignore[import-not-found]
    except ImportError as error:
        raise RuntimeError(
            "Install Shapely to derive representative points from GIS geometry: pip install shapely"
        ) from error

    try:
        geometry_object = shape(geometry)
        if geometry_object.is_empty:
            return None, None
        # A mathematical centroid can lie in the empty area of a concave
        # polygon. representative_point() is guaranteed to lie on or inside
        # the geometry and is therefore safer for a mapped stop location.
        point = geometry_object if geometry_object.geom_type == "Point" else geometry_object.representative_point()
        return float(point.x), float(point.y)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("GIS geometry could not be converted into a representative point") from error


def transform_geometry_coordinates(geometry: dict[str, Any], transformer: Any) -> dict[str, Any]:
    def transform(value: Any) -> Any:
        if (
            isinstance(value, (list, tuple))
            and len(value) >= 2
            and not isinstance(value[0], (list, tuple))
        ):
            lon, lat = transformer.transform(float(value[0]), float(value[1]))
            return [lon, lat, *value[2:]]
        if isinstance(value, (list, tuple)):
            return [transform(item) for item in value]
        return value

    transformed = dict(geometry)
    if str(geometry.get("type", "")).lower() == "geometrycollection":
        transformed["geometries"] = [
            transform_geometry_coordinates(child, transformer)
            for child in geometry.get("geometries", [])
            if isinstance(child, dict)
        ]
    else:
        transformed["coordinates"] = transform(geometry.get("coordinates"))
    return transformed


def shapefile_reader_and_transformer(contents: bytes) -> tuple[Any, Any | None, dict[str, Any]]:
    try:
        import shapefile  # type: ignore[import-not-found]
    except ImportError as error:
        raise RuntimeError("Install pyshp to import zipped Shapefiles: pip install pyshp") from error

    with zipfile.ZipFile(io.BytesIO(contents)) as archive:
        member_names = archive.namelist()
        shp_member = next((name for name in member_names if name.lower().endswith(".shp")), None)
        if not shp_member:
            raise ValueError("Shapefile ZIP must include at least .shp and .dbf files")
        base_name = shp_member.rsplit(".", 1)[0].lower()

        def matching_member(extension: str) -> str | None:
            return next(
                (
                    name
                    for name in member_names
                    if name.rsplit(".", 1)[0].lower() == base_name
                    and name.lower().endswith(extension)
                ),
                None,
            )

        dbf_member = matching_member(".dbf")
        shx_member = matching_member(".shx")
        prj_member = matching_member(".prj")
        if not dbf_member:
            raise ValueError("Shapefile ZIP must include matching .shp and .dbf files")
        shp = io.BytesIO(archive.read(shp_member))
        dbf = io.BytesIO(archive.read(dbf_member))
        shx = io.BytesIO(archive.read(shx_member)) if shx_member else None
        projection = archive.read(prj_member).decode("utf-8-sig").strip() if prj_member else ""

    reader_kwargs = {"shp": shp, "dbf": dbf}
    if shx is not None:
        reader_kwargs["shx"] = shx
    reader = shapefile.Reader(**reader_kwargs)
    if not projection:
        return reader, None, {"source_crs": "assumed EPSG:4326", "reprojected": False}

    try:
        from pyproj import CRS, Transformer  # type: ignore[import-not-found]
    except ImportError as error:
        raise RuntimeError(
            "Install pyproj to read and reproject Shapefile coordinate systems: pip install pyproj"
        ) from error
    try:
        source_crs = CRS.from_wkt(projection)
    except Exception as error:
        raise ValueError("Shapefile .prj does not contain a valid coordinate reference system") from error
    target_crs = CRS.from_epsg(4326)
    source_name = source_crs.to_string()
    if source_crs.equals(target_crs):
        return reader, None, {"source_crs": source_name, "reprojected": False}
    return reader, Transformer.from_crs(source_crs, target_crs, always_xy=True), {
        "source_crs": source_name,
        "reprojected": True,
    }


def geojson_features(payload: dict[str, Any]) -> list[dict[str, Any]]:
    payload_type = str(payload.get("type", "")).lower()
    if payload_type == "featurecollection":
        return [feature for feature in payload.get("features", []) if isinstance(feature, dict)]
    if payload_type == "feature":
        return [payload]
    if payload_type in {
        "point",
        "multipoint",
        "linestring",
        "multilinestring",
        "polygon",
        "multipolygon",
        "geometrycollection",
    }:
        return [{"type": "Feature", "properties": {}, "geometry": payload}]
    raise ValueError("GeoJSON must be a FeatureCollection, Feature, or geometry object")


def parse_geojson_bytes(
    contents: bytes,
    *,
    limit: int | None = None,
    label: str = "GeoJSON upload",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    validate_bytes_size(contents, limit or max_upload_bytes(), label)
    payload = json.loads(contents.decode("utf-8-sig"))
    features = geojson_features(payload)
    records: list[dict[str, Any]] = []
    geometry_types: set[str] = set()
    missing_geometry = 0
    for index, feature in enumerate(features, start=1):
        properties = feature.get("properties") or {}
        if not isinstance(properties, dict):
            properties = {}
        geometry = feature.get("geometry")
        geometry_types.add(str((geometry or {}).get("type", "None")))
        lon, lat = geometry_centroid(geometry)
        record = {str(key): value for key, value in properties.items()}
        record.setdefault("stop_id", str(feature.get("id") or record.get("stop_id") or index))
        record.setdefault("stop_name", record.get("name") or record.get("stop_name") or f"GeoJSON stop {index}")
        if lon is None or lat is None:
            missing_geometry += 1
        else:
            if blank_tabular_value(record.get("stop_lon")):
                record["stop_lon"] = lon
            if blank_tabular_value(record.get("stop_lat")):
                record["stop_lat"] = lat
        record["geometry_type"] = str((geometry or {}).get("type", ""))
        records.append(record)
    if not records:
        raise ValueError("GeoJSON did not contain any features")
    return pd.DataFrame(records), {
        "geometry_types": "; ".join(sorted(geometry_types)),
        "features": len(records),
        "missing_geometry": missing_geometry,
    }


def json_safe_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, Path)):
        return str(value)
    return str(value)


def blank_tabular_value(value: Any) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def clean_geojson_feature(feature: dict[str, Any]) -> dict[str, Any] | None:
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict) or not geometry_coordinate_pairs(geometry):
        return None
    properties = feature.get("properties") or {}
    if not isinstance(properties, dict):
        properties = {}
    cleaned = {
        "type": "Feature",
        "properties": {str(key): json_safe_value(value) for key, value in properties.items()},
        "geometry": geometry,
    }
    if feature.get("id") is not None:
        cleaned["id"] = json_safe_value(feature.get("id"))
    return cleaned


def parse_geojson_overlay_bytes(contents: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_bytes_size(contents, max_upload_bytes(), "GeoJSON overlay upload")
    payload = json.loads(contents.decode("utf-8-sig"))
    features = []
    geometry_types: set[str] = set()
    for feature in geojson_features(payload):
        cleaned = clean_geojson_feature(feature)
        if cleaned is None:
            continue
        geometry_types.add(str(cleaned["geometry"].get("type", "")))
        features.append(cleaned)
    if not features:
        raise ValueError("GIS overlay did not contain any renderable geometries")
    return {"type": "FeatureCollection", "features": features}, {
        "geometry_types": "; ".join(sorted(geometry_types)),
        "features": len(features),
    }


def zip_member_names(contents: bytes) -> list[str]:
    validate_zip_bytes(contents)
    with zipfile.ZipFile(io.BytesIO(contents)) as archive:
        return archive.namelist()


def detect_zip_import_format(contents: bytes) -> str:
    names = [Path(name).name.lower() for name in zip_member_names(contents)]
    if "stops.txt" in names:
        return "GTFS"
    if any(name.endswith(".shp") for name in names) and any(name.endswith(".dbf") for name in names):
        return "Shapefile"
    raise ValueError("ZIP upload must contain GTFS stops.txt or a zipped Shapefile with .shp and .dbf files")


def parse_shapefile_zip(contents: bytes) -> tuple[pd.DataFrame, dict[str, Any]]:
    validate_zip_bytes(contents, "Shapefile ZIP upload")
    reader, transformer, crs_metadata = shapefile_reader_and_transformer(contents)
    fields = [field[0] for field in reader.fields if field[0] != "DeletionFlag"]
    records = []
    missing_geometry = 0
    geometry_types: set[str] = set()
    for index, shape_record in enumerate(reader.iterShapeRecords(), start=1):
        record = {field: value for field, value in zip(fields, shape_record.record)}
        geometry = shape_record.shape.__geo_interface__
        if transformer is not None:
            geometry = transform_geometry_coordinates(geometry, transformer)
        geometry_types.add(str(geometry.get("type", "")))
        lon, lat = geometry_centroid(geometry)
        record.setdefault("stop_id", str(record.get("stop_id") or record.get("id") or index))
        record.setdefault("stop_name", record.get("name") or record.get("stop_name") or f"Shapefile stop {index}")
        if lon is None or lat is None:
            missing_geometry += 1
        else:
            if blank_tabular_value(record.get("stop_lon")):
                record["stop_lon"] = lon
            if blank_tabular_value(record.get("stop_lat")):
                record["stop_lat"] = lat
        record["geometry_type"] = str(geometry.get("type", ""))
        records.append(record)
    if not records:
        raise ValueError("Shapefile did not contain any records")
    return pd.DataFrame(records), {
        **crs_metadata,
        "geometry_types": "; ".join(sorted(geometry_types)),
        "features": len(records),
        "missing_geometry": missing_geometry,
    }


def parse_shapefile_overlay_zip(contents: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_zip_bytes(contents, "Shapefile overlay ZIP upload")
    reader, transformer, crs_metadata = shapefile_reader_and_transformer(contents)
    fields = [field[0] for field in reader.fields if field[0] != "DeletionFlag"]
    features = []
    geometry_types: set[str] = set()
    for shape_record in reader.iterShapeRecords():
        geometry = shape_record.shape.__geo_interface__
        if transformer is not None:
            geometry = transform_geometry_coordinates(geometry, transformer)
        if not geometry_coordinate_pairs(geometry):
            continue
        geometry_types.add(str(geometry.get("type", "")))
        properties = {
            str(field): json_safe_value(value)
            for field, value in zip(fields, shape_record.record)
        }
        features.append({"type": "Feature", "properties": properties, "geometry": geometry})
    if not features:
        raise ValueError("Shapefile overlay did not contain any renderable geometries")
    return {"type": "FeatureCollection", "features": features}, {
        **crs_metadata,
        "geometry_types": "; ".join(sorted(geometry_types)),
        "features": len(features),
    }


def fetch_api_bytes(url: str) -> bytes:
    try:
        clean_url, addresses = _validated_api_target(url)
        for redirect_count in range(API_MAX_REDIRECTS + 1):
            connection, response = _open_pinned_api_response(clean_url, addresses)
            try:
                if response.status in {301, 302, 303, 307, 308}:
                    location = str(response.headers.get("Location") or "").strip()
                    if not location:
                        raise RuntimeError("API redirect did not include a destination")
                    if redirect_count >= API_MAX_REDIRECTS:
                        raise RuntimeError("API URL redirected too many times")
                    clean_url, addresses = _validated_api_target(
                        urllib.parse.urljoin(clean_url, location)
                    )
                    continue
                if response.status < 200 or response.status >= 300:
                    raise RuntimeError(f"API URL returned HTTP {response.status}")
                return read_limited_response(response, max_api_bytes())
            finally:
                response.close()
                connection.close()
        raise RuntimeError("API URL redirected too many times")
    except (OSError, ValueError, ssl.SSLError, http.client.HTTPException) as error:
        raise RuntimeError(f"Could not fetch API URL: {error}") from error


def parse_api_response(contents: bytes, url: str, requested_format: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    validate_bytes_size(contents, max_api_bytes(), "API response")
    source_url = public_source_url(url)
    if requested_format == "CSV":
        return read_csv_bytes(contents, limit=max_api_bytes(), label="API CSV response"), {"source_url": source_url}
    if requested_format == "GeoJSON":
        raw, metadata = parse_geojson_bytes(contents, limit=max_api_bytes(), label="API GeoJSON response")
        metadata["source_url"] = source_url
        return raw, metadata
    try:
        raw, metadata = parse_geojson_bytes(contents, limit=max_api_bytes(), label="API GeoJSON response")
        metadata["source_url"] = source_url
        metadata["detected_format"] = "GeoJSON"
        return raw, metadata
    except Exception:
        raw = read_csv_bytes(contents, limit=max_api_bytes(), label="API CSV response")
        return raw, {"source_url": source_url, "detected_format": "CSV"}


def find_gtfs_member(archive: zipfile.ZipFile, filename: str) -> str | None:
    filename = filename.lower()
    for member in archive.namelist():
        if Path(member).name.lower() == filename:
            return member
    return None


def read_gtfs_table(
    archive: zipfile.ZipFile, filename: str, usecols: list[str] | None = None
) -> pd.DataFrame | None:
    member = find_gtfs_member(archive, filename)
    if member is None:
        return None
    with archive.open(member) as handle:
        try:
            return pd.read_csv(handle, dtype=str, usecols=usecols)
        except ValueError:
            handle.seek(0)
            return pd.read_csv(handle, dtype=str)


def parse_gtfs_zip(contents: bytes) -> tuple[pd.DataFrame, dict[str, Any]]:
    validate_zip_bytes(contents, "GTFS ZIP upload")
    with zipfile.ZipFile(io.BytesIO(contents)) as archive:
        stops = read_gtfs_table(archive, "stops.txt")
        if stops is None:
            raise ValueError("GTFS upload must include stops.txt")

        route_map: dict[str, str] = {}
        stop_times = read_gtfs_table(archive, "stop_times.txt", ["trip_id", "stop_id"])
        trips = read_gtfs_table(archive, "trips.txt", ["trip_id", "route_id"])
        routes = read_gtfs_table(archive, "routes.txt")
        if stop_times is not None and trips is not None and routes is not None:
            route_label_col = "route_short_name" if "route_short_name" in routes.columns else "route_long_name"
            if route_label_col in routes.columns and "route_id" in routes.columns:
                route_lookup = routes.loc[:, ["route_id", route_label_col]].dropna().drop_duplicates()
                joined = stop_times.merge(trips, on="trip_id", how="left").merge(route_lookup, on="route_id", how="left")
                joined = joined.dropna(subset=["stop_id", route_label_col])
                route_map = (
                    joined.groupby("stop_id")[route_label_col]
                    .apply(lambda values: "; ".join(sorted({str(value) for value in values if str(value).strip()})))
                    .to_dict()
                )

    if route_map:
        stops["routes"] = stops["stop_id"].map(route_map).fillna("")
    metadata = {
        "format": "GTFS",
        "tables": ["stops.txt"],
        "routes_joined": bool(route_map),
        "imported_at": timestamp_with_timezone(),
    }
    return stops, metadata


def apply_field_mapping(raw: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    mapped = pd.DataFrame(index=raw.index)
    used_sources = set()
    for target, source in mapping.items():
        if source and source in raw.columns:
            mapped[target] = raw[source]
            used_sources.add(source)
    for column in raw.columns:
        if column not in used_sources and column not in mapped.columns:
            mapped[column] = raw[column]
    for field in REQUIRED_STOP_FIELDS:
        if field not in mapped.columns:
            mapped[field] = ""
    return mapped


def clean_import_key(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower() or "import"


def calculate_priority_scores(df: pd.DataFrame, weights: dict[str, float] | None = None) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=float)
    weights = weights or DEFAULT_PRIORITY_WEIGHTS
    score_parts: list[tuple[float, pd.Series]] = []

    ridership_weight = float(weights.get("ridership", 0.0))
    if ridership_weight > 0 and "ridership" in df.columns:
        ridership = pd.to_numeric(df.get("ridership"), errors="coerce").fillna(0)
        ridership = ridership / ridership.max() if ridership.max() and ridership.max() > 0 else ridership
        score_parts.append((ridership_weight, ridership))

    low_shade_weight = float(weights.get("low_shade", 0.0))
    if low_shade_weight > 0 and "shading" in df.columns:
        low_shade = df.get("shading", pd.Series(index=df.index, dtype=str)).isin(
            ["No Shade", "Limited Shade", "Limited", "Limited Natural Shade", "Needs Review"]
        ).astype(float)
        score_parts.append((low_shade_weight, low_shade))

    total_weight = sum(weight for weight, _ in score_parts)
    if total_weight <= 0:
        return pd.Series(0.0, index=df.index)
    score = sum(series * weight for weight, series in score_parts) / total_weight
    return (score * 100).round(1)


def prepare_stop_dataset(raw: pd.DataFrame, project: dict[str, Any], taxonomy: list[dict[str, Any]]) -> pd.DataFrame:
    df = raw.copy()
    for field in REQUIRED_STOP_FIELDS:
        if field not in df.columns:
            df[field] = ""
    for field in OPTIONAL_FIELDS:
        if field not in df.columns:
            df[field] = ""

    df["stop_id"] = df["stop_id"].map(canonical_identifier)
    df["stop_name"] = df["stop_name"].fillna("").astype(str).str.strip()
    df["stop_name"] = df["stop_name"].where(df["stop_name"] != "", "Unnamed stop")
    df["stop_lat"] = pd.to_numeric(df["stop_lat"], errors="coerce")
    df["stop_lon"] = pd.to_numeric(df["stop_lon"], errors="coerce")
    df["agency"] = df["agency"].fillna("").replace("", project.get("agency", ""))
    df["routes"] = df["routes"].fillna("").astype(str)
    df["municipality"] = df["municipality"].fillna("").astype(str)
    legacy_shading = df["shading"].copy()

    def coverage_for_row(row: pd.Series) -> str:
        explicit_coverage = row.get("shade_coverage", "")
        if not pd.api.types.is_scalar(explicit_coverage):
            return normalize_category(explicit_coverage, taxonomy)
        candidate = explicit_coverage if scalar_text(explicit_coverage) else row.get("shading", "")
        return normalize_category(candidate, taxonomy)

    df["shade_coverage"] = df.apply(coverage_for_row, axis=1)
    df["shading"] = df["shade_coverage"]

    def sources_for_row(index: Any, value: Any) -> str:
        sources = split_shade_sources(value) if pd.api.types.is_scalar(value) else []
        if not sources:
            legacy_value = legacy_shading.loc[index]
            sources = (
                infer_sources_from_legacy_category(legacy_value)
                if pd.api.types.is_scalar(legacy_value)
                else []
            )
        if df.at[index, "shade_coverage"] == "No Shade":
            sources = []
        return "; ".join(sources)

    df["shade_sources"] = [sources_for_row(index, value) for index, value in df["shade_sources"].items()]
    df["review_status"] = df["review_status"].apply(normalize_review_status)

    numeric_fields = ["confidence", "ridership"]
    for field in numeric_fields:
        df[field] = pd.to_numeric(df[field], errors="coerce")

    valid_coordinates = df["stop_lat"].between(-90, 90) & df["stop_lon"].between(-180, 180)
    df = df[valid_coordinates]
    df = df[df["stop_id"] != ""].drop_duplicates(subset=["stop_id"], keep="first")
    df["priority_score"] = calculate_priority_scores(df)
    return df.reset_index(drop=True)


def import_stop_dataset(
    raw: pd.DataFrame,
    mapping: dict[str, str],
    *,
    project: dict[str, Any],
    taxonomy: list[dict[str, Any]],
    source_name: str,
    import_format: str,
    metadata: dict[str, Any] | None = None,
) -> pd.DataFrame:
    prepared = prepare_stop_dataset(apply_field_mapping(raw, mapping), project, taxonomy)
    st.session_state["stops"] = prepared
    if source_name:
        project["source_name"] = source_name
    if metadata and metadata.get("source_url"):
        project["source_url"] = str(metadata["source_url"])
    log_entry = {
        "source": source_name,
        "format": import_format,
        "rows": len(prepared),
        "imported_at": timestamp_with_timezone(),
    }
    if metadata:
        log_entry.update(metadata)
    st.session_state["import_log"].append(log_entry)
    return prepared


def render_mapped_import_controls(
    raw: pd.DataFrame,
    *,
    source_name: str,
    import_format: str,
    project: dict[str, Any],
    taxonomy: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
    key_prefix: str,
    button_label: str,
) -> None:
    metadata = metadata or {}
    st.dataframe(raw.head(25), width="stretch")
    if {"stop_lat", "stop_lon"}.issubset(raw.columns):
        missing_coordinates = raw[["stop_lat", "stop_lon"]].isna().any(axis=1).sum()
        st.caption(f"Geometry validation: {len(raw):,} records, {int(missing_coordinates):,} missing coordinates.")
    elif {"geometry_type", "stop_lat", "stop_lon"}.issubset(raw.columns):
        st.caption(f"Geometry validation: {len(raw):,} records from {metadata.get('geometry_types', 'spatial')} geometries.")

    choices = [""] + list(raw.columns)
    st.markdown("#### Field Mapping")
    mapping: dict[str, str] = {}
    fields = REQUIRED_STOP_FIELDS + OPTIONAL_FIELDS
    grid = st.columns(4)
    for index, field in enumerate(fields):
        suggested = suggest_source_column(field, list(raw.columns))
        default_index = choices.index(suggested) if suggested in choices else 0
        with grid[index % 4]:
            mapping[field] = st.selectbox(
                field,
                choices,
                index=default_index,
                key=f"{key_prefix}_map_{field}",
            )
    if st.button(button_label, type="primary", key=f"{key_prefix}_use"):
        prepared = import_stop_dataset(
            raw,
            mapping,
            project=project,
            taxonomy=taxonomy,
            source_name=source_name,
            import_format=import_format,
            metadata=metadata,
        )
        st.success(f"Imported {len(prepared):,} mapped stops.")

