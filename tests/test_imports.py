from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest

import builder_app
import shade_gis.builder_imports as builder_imports
from builder_app import (
    fetch_api_bytes,
    import_stop_dataset,
    parse_geojson_bytes,
    parse_geojson_overlay_bytes,
    parse_api_response,
    parse_gtfs_zip,
    prepare_stop_dataset,
    read_csv_bytes,
    validate_api_url,
    validate_zip_bytes,
)


def test_csv_import_maps_fields_deduplicates_and_logs(project, taxonomy):
    builder_app.st.session_state.clear()
    builder_app.st.session_state["import_log"] = []
    raw = read_csv_bytes((builder_app.APP_DIR / "tests" / "fixtures" / "stops_minimal.csv").read_bytes())
    mapping = {
        "stop_id": "stop_id",
        "stop_name": "stop_name",
        "stop_lat": "lat",
        "stop_lon": "lon",
        "routes": "route",
        "ridership": "ridership",
        "nearby_destinations": "nearby_destinations",
    }

    prepared = import_stop_dataset(
        raw,
        mapping,
        project=project,
        taxonomy=taxonomy,
        source_name="stops_minimal.csv",
        import_format="CSV",
        metadata={"original_filename": "stops_minimal.csv"},
    )

    assert len(prepared) == 2
    assert prepared.loc[prepared["stop_id"] == "1001", "stop_lat"].iloc[0] == pytest.approx(27.9506)
    assert prepared.loc[prepared["stop_id"] == "1001", "routes"].iloc[0] == "10"
    assert prepared.loc[prepared["stop_id"] == "1001", "context_label"].iloc[0] == "High"
    assert builder_app.st.session_state["import_log"][0]["rows"] == 2
    assert builder_app.st.session_state["import_log"][0]["source"] == "stops_minimal.csv"


def test_gtfs_zip_import_enriches_routes(project, taxonomy):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("stops.txt", "stop_id,stop_name,stop_lat,stop_lon\n2001,GTFS Main,27.9601,-82.4601\n2002,GTFS Central,27.9610,-82.4610\n")
        archive.writestr("stop_times.txt", "trip_id,stop_id\ntrip-a,2001\ntrip-b,2002\n")
        archive.writestr("trips.txt", "trip_id,route_id\ntrip-a,route-10\ntrip-b,route-20\n")
        archive.writestr("routes.txt", "route_id,route_short_name\nroute-10,10\nroute-20,20\n")

    raw, metadata = parse_gtfs_zip(buffer.getvalue())
    prepared = prepare_stop_dataset(raw, {**project, "agency": "GTFS Transit"}, taxonomy)

    assert metadata["routes_joined"] is True
    assert len(prepared) == 2
    assert prepared.loc[prepared["stop_id"] == "2001", "routes"].iloc[0] == "10"


def test_geojson_import_and_overlay_parser(project, taxonomy):
    contents = (builder_app.APP_DIR / "tests" / "fixtures" / "sample_overlay.geojson").read_bytes()

    raw, metadata = parse_geojson_bytes(contents)
    prepared = prepare_stop_dataset(raw, project, taxonomy)
    overlay, overlay_metadata = parse_geojson_overlay_bytes(contents)

    assert metadata["features"] == 2
    assert len(prepared) == 2
    assert prepared.loc[prepared["stop_id"] == "3001", "stop_lon"].iloc[0] == pytest.approx(-82.4701)
    assert overlay["type"] == "FeatureCollection"
    assert overlay_metadata["features"] == 2


def test_polygon_import_uses_a_point_inside_concave_geometry():
    geometry = {
        "type": "Polygon",
        "coordinates": [
            [[0, 0], [4, 0], [4, 4], [3, 4], [3, 1], [1, 1], [1, 4], [0, 4], [0, 0]]
        ],
    }

    lon, lat = builder_imports.geometry_centroid(geometry)

    assert lon is not None and lat is not None
    assert not (1 < lon < 3 and lat > 1), "representative point fell in the polygon's empty gap"


def test_shapefile_import_reprojects_prj_coordinates_to_wgs84():
    import shapefile
    from pyproj import CRS, Transformer

    source_lon, source_lat = -82.4572, 27.9506
    source_x, source_y = Transformer.from_crs(4326, 3857, always_xy=True).transform(
        source_lon, source_lat
    )
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    writer = shapefile.Writer(shp=shp, shx=shx, dbf=dbf)
    writer.field("stop_id", "C")
    writer.field("name", "C")
    writer.point(source_x, source_y)
    writer.record("mercator-1", "Projected Stop")
    writer.close()

    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("projected.shp", shp.getvalue())
        archive.writestr("projected.shx", shx.getvalue())
        archive.writestr("projected.dbf", dbf.getvalue())
        archive.writestr("projected.prj", CRS.from_epsg(3857).to_wkt())

    raw, metadata = builder_imports.parse_shapefile_zip(bundle.getvalue())
    overlay, overlay_metadata = builder_imports.parse_shapefile_overlay_zip(bundle.getvalue())

    assert raw.loc[0, "stop_lon"] == pytest.approx(source_lon, abs=1e-6)
    assert raw.loc[0, "stop_lat"] == pytest.approx(source_lat, abs=1e-6)
    assert metadata["reprojected"] is True
    assert overlay["features"][0]["geometry"]["coordinates"] == pytest.approx(
        [source_lon, source_lat], abs=1e-6
    )
    assert overlay_metadata["reprojected"] is True


def test_bad_csv_missing_coordinates_drops_rows(project, taxonomy):
    raw = pd.DataFrame(
        [
            {"stop_id": "bad-1", "stop_name": "Missing Lat", "stop_lat": "", "stop_lon": -82.0},
            {"stop_id": "bad-2", "stop_name": "Missing Lon", "stop_lat": 27.0, "stop_lon": ""},
        ]
    )

    prepared = prepare_stop_dataset(raw, project, taxonomy)

    assert prepared.empty


def test_legacy_combined_labels_are_split_into_coverage_and_sources(project, taxonomy):
    raw = pd.DataFrame(
        [
            {
                "stop_id": "legacy-1",
                "stop_name": "Legacy natural",
                "stop_lat": 27.95,
                "stop_lon": -82.45,
                "shading": "Limited Natural Shade",
            },
            {
                "stop_id": "legacy-2",
                "stop_name": "Legacy shelter",
                "stop_lat": 27.96,
                "stop_lon": -82.46,
                "shading": "Intentional Built Shade",
            },
        ]
    )

    prepared = prepare_stop_dataset(raw, project, taxonomy).set_index("stop_id")

    assert prepared.loc["legacy-1", "shade_coverage"] == "Limited Shade"
    assert prepared.loc["legacy-1", "shading"] == "Limited Shade"
    assert prepared.loc["legacy-1", "shade_sources"] == "Natural"
    assert prepared.loc["legacy-2", "shade_coverage"] == "Needs Review"
    assert prepared.loc["legacy-2", "shade_sources"] == "Purpose-built"


def test_api_url_guard_blocks_private_and_credentialed_urls(monkeypatch):
    monkeypatch.delenv("SHADE_GIS_ALLOW_PRIVATE_API_URLS", raising=False)
    monkeypatch.delenv("SHADE_GIS_ALLOWED_API_HOSTS", raising=False)

    with pytest.raises(ValueError, match="http or https"):
        validate_api_url("ftp://example.org/stops.csv")
    with pytest.raises(ValueError, match="credentials"):
        credentialed_url = "https://" + "user" + ":" + "pass" + "@example.org/stops.csv"
        validate_api_url(credentialed_url)
    with pytest.raises(ValueError, match="Private or localhost"):
        validate_api_url("http://127.0.0.1/stops.csv")


def test_api_url_guard_supports_deployment_allowlist(monkeypatch):
    monkeypatch.setenv("SHADE_GIS_ALLOW_PRIVATE_API_URLS", "1")
    monkeypatch.setenv("SHADE_GIS_ALLOWED_API_HOSTS", "transit.example.org")
    monkeypatch.setattr(
        builder_imports.socket,
        "getaddrinfo",
        lambda host, port, type: [
            (builder_imports.socket.AF_INET, type, 6, "", ("8.8.8.8", port))
        ],
    )

    assert validate_api_url("https://data.transit.example.org/stops.csv") == "https://data.transit.example.org/stops.csv"
    with pytest.raises(ValueError, match="not in SHADE_GIS_ALLOWED_API_HOSTS"):
        validate_api_url("https://other.example.org/stops.csv")


def test_api_import_provenance_removes_query_credentials(monkeypatch):
    monkeypatch.setattr(
        builder_imports.socket,
        "getaddrinfo",
        lambda host, port, type: [
            (builder_imports.socket.AF_INET, type, 6, "", ("8.8.8.8", port))
        ],
    )
    _, metadata = parse_api_response(
        b"stop_id,stop_name\n1001,Main\n",
        "https://example.org/stops.csv?api_key=super-secret&format=csv#token",
        "CSV",
    )

    assert metadata["source_url"] == "https://example.org/stops.csv"
    assert "super-secret" not in repr(metadata)


def test_geojson_geometry_collection_and_blank_coordinate_properties_use_geometry():
    payload = b'''{
      "type": "FeatureCollection",
      "features": [{
        "type": "Feature",
        "properties": {"stop_id": "collection", "stop_lon": "", "stop_lat": null},
        "geometry": {"type": "GeometryCollection", "geometries": [
          {"type": "Point", "coordinates": [-82.45, 27.95]}
        ]}
      }]
    }'''

    records, metadata = parse_geojson_bytes(payload)
    top_level, _ = parse_geojson_bytes(
        b'{"type":"GeometryCollection","geometries":[{"type":"Point","coordinates":[-82.46,27.96]}]}'
    )

    assert records.loc[0, "stop_lon"] == pytest.approx(-82.45)
    assert records.loc[0, "stop_lat"] == pytest.approx(27.95)
    assert metadata["geometry_types"] == "GeometryCollection"
    assert top_level.loc[0, "stop_lon"] == pytest.approx(-82.46)


def test_api_fetch_revalidates_redirect_targets(monkeypatch):
    monkeypatch.delenv("SHADE_GIS_ALLOW_PRIVATE_API_URLS", raising=False)
    monkeypatch.delenv("SHADE_GIS_ALLOWED_API_HOSTS", raising=False)
    monkeypatch.setattr(
        builder_imports.socket,
        "getaddrinfo",
        lambda host, port, type: [
            (builder_imports.socket.AF_INET, type, 6, "", ("8.8.8.8", port))
        ],
    )

    class FakeConnection:
        def close(self):
            return None

    class FakeResponse:
        status = 302
        headers = {"Location": "http://127.0.0.1/internal"}

        def close(self):
            return None

    monkeypatch.setattr(
        builder_imports,
        "_open_pinned_api_response",
        lambda url, addresses: (FakeConnection(), FakeResponse()),
    )

    with pytest.raises(RuntimeError, match="Private or localhost"):
        fetch_api_bytes("https://public.example/stops.csv")


def test_pinned_http_connection_uses_validated_address(monkeypatch):
    observed = {}

    class FakeSocket:
        pass

    def fake_create_connection(target, timeout, source_address):
        observed["target"] = target
        observed["timeout"] = timeout
        return FakeSocket()

    monkeypatch.setattr(builder_imports.socket, "create_connection", fake_create_connection)
    connection = builder_imports._PinnedHTTPConnection(
        "public.example", "203.0.113.10", 80, 12
    )

    connection.connect()

    assert observed["target"] == ("203.0.113.10", 80)
    assert observed["timeout"] == 12


def test_import_size_limits_are_enforced(monkeypatch):
    monkeypatch.setenv("SHADE_GIS_MAX_UPLOAD_BYTES", "16")
    monkeypatch.setenv("SHADE_GIS_MAX_API_BYTES", "16")

    with pytest.raises(ValueError, match="CSV upload"):
        read_csv_bytes(b"stop_id,stop_name\n1001,This row is too large\n")
    with pytest.raises(ValueError, match="API response"):
        parse_api_response(b"stop_id,stop_name\n1001,This row is too large\n", "https://example.org/stops.csv", "CSV")


def test_zip_guard_limits_members_and_expanded_size(monkeypatch):
    monkeypatch.setenv("SHADE_GIS_MAX_ZIP_MEMBERS", "1")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("stops.txt", "stop_id,stop_name,stop_lat,stop_lon\n")
        archive.writestr("routes.txt", "route_id,route_short_name\n")

    with pytest.raises(ValueError, match="contains 2 files"):
        validate_zip_bytes(buffer.getvalue(), "test ZIP")

    monkeypatch.setenv("SHADE_GIS_MAX_ZIP_MEMBERS", "10")
    monkeypatch.setenv("SHADE_GIS_MAX_ZIP_UNCOMPRESSED_BYTES", "8")
    with pytest.raises(ValueError, match="expands to"):
        validate_zip_bytes(buffer.getvalue(), "test ZIP")
