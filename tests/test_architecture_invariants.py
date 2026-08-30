from __future__ import annotations

import io
import json
import math
import random
import zipfile

import numpy as np
import pandas as pd
import pytest

import published_app
import stop_gis.deploy.bundle as bundle_module
from stop_gis.builder.imports import prepare_stop_dataset
from stop_gis.builder.labels import (
    disagreement_queue_table,
    fleiss_kappa,
    krippendorff_alpha_nominal,
    majority_label_table,
)
from stop_gis.builder.visuals import mappable_stop_rows as builder_mappable_stop_rows
from stop_gis.deploy.bundle import DeploymentBundleSpec, build_deployment_bundle
from stop_gis.deploy.service import (
    DeploymentTarget,
    github_repository_slug,
    validate_deployment_bundle,
)
from stop_gis.domain.identifiers import canonical_identifier
from stop_gis.pages import deploy_page


@pytest.mark.parametrize(
    "value",
    [42, 42.0, np.int64(42), np.float64(42), "42", " 42 "],
)
def test_numeric_identifier_representations_share_one_canonical_value(value):
    assert canonical_identifier(value) == "42"
    assert published_app.canonical_identifier(value) == "42"
    filtered = published_app.labels_for_visible_stops(
        pd.DataFrame([{"stop_id": "42", "shade_category": "No Shade"}]),
        pd.DataFrame([{"stop_id": value}]),
    )
    assert len(filtered) == 1


def test_textual_identifier_formatting_remains_meaningful():
    assert canonical_identifier("0042") == "0042"
    assert canonical_identifier(None) == ""
    assert canonical_identifier(pd.NA) == ""
    assert canonical_identifier(float("nan")) == ""


@pytest.mark.parametrize(
    "repository",
    [
        "Owner/Study",
        "Owner/Study.git",
        "https://github.com/Owner/Study.git",
        "git@github.com:Owner/Study.git",
        "ssh://git@github.com/Owner/Study.git",
    ],
)
def test_repository_spellings_canonicalize_to_the_same_slug(repository):
    assert github_repository_slug(repository).casefold() == "owner/study"


@pytest.mark.parametrize(
    "repository",
    [
        "https://example.com/Owner/Study",
        "https://evilgithub.com/Owner/Study",
        "not-a-repository",
        "Owner/Study/Other",
    ],
)
def test_non_github_or_ambiguous_repository_names_do_not_canonicalize(repository):
    assert github_repository_slug(repository) == ""


def _release_fingerprint(spec: DeploymentBundleSpec) -> str:
    bundle_data = build_deployment_bundle(spec)
    validated_manifest = validate_deployment_bundle(
        bundle_data,
        DeploymentTarget(
            repository=spec.repository,
            mode=spec.deploy_mode,
            commit_message=spec.commit_message,
        ),
    )
    with zipfile.ZipFile(io.BytesIO(bundle_data)) as bundle:
        manifest = json.loads(bundle.read("deployment_manifest.json"))
        identity = json.loads(bundle.read("static/shade_gis_identity.json"))
    assert validated_manifest == manifest
    assert manifest["release_sha256"] == identity["release_sha256"]
    return str(manifest["release_sha256"])


def test_release_fingerprint_is_stable_and_sensitive_to_every_runtime_data_class(monkeypatch):
    base = dict(
        repository="owner/study",
        project={"name": "Study"},
        study_id="study-id",
        stops=pd.DataFrame([{"stop_id": "1", "ridership": 10}]),
        raw_labels=pd.DataFrame(),
        config_json="{}",
        priority_weights={"ridership": 1.0},
    )
    baseline = DeploymentBundleSpec(**base)
    mutations = [
        DeploymentBundleSpec(**{**base, "config_json": '{"voting": true}'}),
        DeploymentBundleSpec(
            **{
                **base,
                "stops": pd.DataFrame([{"stop_id": "1", "ridership": 11}]),
            }
        ),
        DeploymentBundleSpec(
            **{
                **base,
                "raw_labels": pd.DataFrame(
                    [{"stop_id": "1", "labeler_id": "a", "shade_category": "No Shade"}]
                ),
            }
        ),
    ]

    baseline_hash = _release_fingerprint(baseline)

    assert _release_fingerprint(baseline) == baseline_hash
    assert all(_release_fingerprint(spec) != baseline_hash for spec in mutations)
    original_source = bundle_module.published_app_source
    monkeypatch.setattr(
        bundle_module,
        "published_app_source",
        lambda: original_source() + "\n# generated runtime mutation\n",
    )
    assert _release_fingerprint(baseline) != baseline_hash


def test_deployment_result_state_accepts_missing_or_malformed_bundle_bytes(monkeypatch):
    monkeypatch.setattr(deploy_page.st, "session_state", {})
    target = deploy_page.DeploymentTarget(repository="owner/study")

    assert deploy_page._stored_result(target, b"") is None
    assert deploy_page._stored_result(target, b"not a zip") is None

    failure = deploy_page.PublishResult(False, message="invalid bundle")
    deploy_page._store_result(target, failure, b"not a zip")

    assert deploy_page._stored_result(target, b"not a zip") is failure
    assert deploy_page._stored_result(target, b"different invalid bytes") is None


def test_recursive_geojson_properties_are_always_strict_json_values():
    nested_values = [
        [1, np.int64(2), pd.NA, {"nested": [np.nan, np.inf, "ok"]}],
        {"set": {"b", "a"}, "timestamp": pd.Timestamp("2026-08-23T12:00:00Z")},
        (True, None, np.float64(2.5)),
    ]

    for value in nested_values:
        cleaned = published_app.json_safe_geojson_property(value)
        json.dumps(cleaned, allow_nan=False)


def test_generated_coordinate_cases_never_escape_map_or_geojson_bounds(project, taxonomy):
    rng = random.Random(20260823)
    coordinates = [
        (-90.0, -180.0),
        (90.0, 180.0),
        (float("nan"), 0.0),
        (0.0, float("inf")),
        (91.0, 0.0),
        (0.0, -181.0),
    ]
    coordinates.extend((rng.uniform(-200, 200), rng.uniform(-300, 300)) for _ in range(100))
    raw = pd.DataFrame(
        [
            {"stop_id": index, "stop_lat": latitude, "stop_lon": longitude}
            for index, (latitude, longitude) in enumerate(coordinates)
        ]
    )

    prepared = prepare_stop_dataset(raw, project, taxonomy)
    published_rows = published_app.mappable_stop_rows(raw)
    builder_rows = builder_mappable_stop_rows(raw)
    geojson = json.loads(published_app.dataframe_to_geojson(raw))

    for frame in (prepared, published_rows, builder_rows):
        assert frame["stop_lat"].between(-90, 90).all()
        assert frame["stop_lon"].between(-180, 180).all()
    assert published_rows[["stop_lat", "stop_lon"]].equals(
        builder_rows[["stop_lat", "stop_lon"]]
    )
    assert all(
        math.isfinite(coordinate)
        for feature in geojson["features"]
        for coordinate in feature["geometry"]["coordinates"]
    )


def _generated_labels(seed: int) -> pd.DataFrame:
    rng = random.Random(seed)
    categories = ["No Shade", "Limited Shade", "Significant Shade"]
    id_variants = {
        1: [1, 1.0, "1", " 1 "],
        2: [2, 2.0, "2", np.int64(2)],
        3: [3, 3.0, "3", np.float64(3)],
    }
    rows = []
    for stop_id, variants in id_variants.items():
        for rater in range(rng.randint(2, 5)):
            rows.append(
                {
                    "stop_id": variants[rater % len(variants)],
                    "labeler_id": f"rater-{rater}",
                    "shade_category": rng.choice(categories),
                    "created_at": f"2026-08-23T12:{stop_id}{rater}:00Z",
                }
            )
    return pd.DataFrame(rows)


@pytest.mark.parametrize("seed", range(20))
def test_builder_and_published_reliability_analytics_remain_equivalent(seed):
    labels = _generated_labels(seed)
    builder_majority = majority_label_table(labels).sort_values("stop_id").reset_index(drop=True)
    published_majority = published_app.majority_label_table(labels).sort_values("stop_id").reset_index(drop=True)

    pd.testing.assert_frame_equal(builder_majority, published_majority, check_dtype=False)
    assert fleiss_kappa(labels) == published_app.fleiss_kappa(labels)
    builder_alpha = krippendorff_alpha_nominal(labels)
    published_alpha = published_app.krippendorff_alpha_nominal(labels)
    if builder_alpha is None or published_alpha is None:
        assert builder_alpha is published_alpha
    else:
        assert builder_alpha == pytest.approx(published_alpha)


@pytest.mark.parametrize(
    ("resolved_at", "new_label_at", "expected_visible"),
    [
        ("", "2026-08-23T12:00:00Z", True),
        ("2026-08-23T13:00:00Z", "2026-08-23T12:00:00Z", False),
        ("2026-08-23T13:00:00Z", "2026-08-23T14:00:00Z", True),
        ("2026-08-23T15:00:00Z", "2026-08-23T14:00:00Z", False),
    ],
)
def test_review_resolution_state_machine_is_consistent_across_apps(
    resolved_at, new_label_at, expected_visible
):
    labels = pd.DataFrame(
        [
            {
                "stop_id": 1,
                "labeler_id": "a",
                "shade_category": "No Shade",
                "created_at": "2026-08-23T11:00:00Z",
            },
            {
                "stop_id": "1",
                "labeler_id": "b",
                "shade_category": "Limited Shade",
                "created_at": new_label_at,
            },
        ]
    )
    stops = pd.DataFrame(
        [
            {
                "stop_id": 1.0,
                "stop_name": "State machine stop",
                "review_status": "Accepted",
                "review_resolved_at": resolved_at,
            }
        ]
    )

    builder_visible = not disagreement_queue_table(stops, labels).empty
    published_visible = not published_app.published_disagreement_queue(labels, stops).empty

    assert builder_visible is expected_visible
    assert published_visible is expected_visible


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_spreadsheet_formula_prefixes_are_neutralized_as_a_class(prefix):
    unsafe = f"{prefix}SUM(A1:A2)"
    exported = published_app.dataframe_to_safe_csv(pd.DataFrame([{"value": unsafe}]))
    round_tripped = pd.read_csv(io.BytesIO(exported), keep_default_na=False).loc[0, "value"]

    assert round_tripped == "'" + unsafe
