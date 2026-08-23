from __future__ import annotations

import re

import pandas as pd
import pytest

from platform_store import add_image, create_project
from shade_gis.blind_coding import (
    BlindCodingError,
    advance_blind_phase,
    blind_adjudication_queue,
    blind_agreement_report,
    blind_progress,
    configure_blind_protocol,
    create_blind_assignments,
    export_blind_adjudications,
    export_blind_ratings,
    get_blind_protocol,
    krippendorff_alpha,
    list_coder_assignments,
    protocol_json,
    submit_blind_adjudication,
    submit_blind_rating,
)


def make_project(db_path, project, taxonomy, methodology, visualization, minimal_stops):
    project_id = create_project(
        project, taxonomy, methodology, visualization, minimal_stops, [], db_path
    )
    image_ids = [
        add_image(
            project_id,
            {
                "stop_id": stop_id,
                "uri": f"https://images.example.org/standardized/{index}.jpg",
                "image_type": "blind_coding_standardized",
            },
            db_path,
        )
        for index, stop_id in enumerate(["1001", "1002"], start=1)
    ]
    return project_id, image_ids


def standard_rating(**updates):
    rating = {
        "shade_source": "tree",
        "coverage": "low",
        "waiting_area_covered": "partially",
        "permanence": "seasonal",
        "image_adequacy": "sufficient",
        "confidence": 4,
        "location_recognized": "no",
    }
    rating.update(updates)
    return rating


def test_protocol_assignments_use_neutral_ids_and_private_coder_records(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project_id, image_ids = make_project(
        db_path, project, taxonomy, methodology, visualization, minimal_stops
    )
    protocol = configure_blind_protocol(
        project_id,
        codebook_version="2026.1",
        target_ratings=3,
        agreement_threshold=0.7,
        instructions="Code the image only.",
        path=db_path,
    )

    created = create_blind_assignments(
        project_id, ["CODER-A", "CODER-B", "CODER-C"], image_ids, db_path
    )
    coder_a = list_coder_assignments(project_id, "CODER-A", db_path)

    assert protocol["phase"] == "setup"
    assert created == 6
    assert len(coder_a) == 2
    assert set(coder_a.columns) == {
        "assignment_id", "display_id", "uri", "storage_path", "sort_order", "status", "submitted_at"
    }
    assert "stop_id" not in coder_a
    assert "image_id" not in coder_a
    assert all(re.fullmatch(r"IMG-\d{6}", value) for value in coder_a["display_id"])
    assert blind_progress(project_id, db_path) == {
        "assignments": 6,
        "submitted": 0,
        "remaining": 6,
        "coders": 3,
        "images": 2,
    }
    assert advance_blind_phase(project_id, db_path) == "coding"
    assert get_blind_protocol(project_id, db_path)["phase"] == "coding"


def test_ratings_are_immutable_and_results_stay_hidden_during_coding(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project_id, image_ids = make_project(
        db_path, project, taxonomy, methodology, visualization, minimal_stops
    )
    create_blind_assignments(
        project_id, ["CODER-A", "CODER-B", "CODER-C"], image_ids[:1], db_path
    )
    advance_blind_phase(project_id, db_path)
    assignment_id = list_coder_assignments(project_id, "CODER-A", db_path).iloc[0][
        "assignment_id"
    ]

    submit_blind_rating(
        project_id, assignment_id, "CODER-A", standard_rating(), db_path
    )

    with pytest.raises(BlindCodingError, match="locked"):
        submit_blind_rating(
            project_id, assignment_id, "CODER-A", standard_rating(coverage="high"), db_path
        )
    with pytest.raises(BlindCodingError, match="hidden"):
        blind_agreement_report(project_id, db_path)
    with pytest.raises(BlindCodingError, match="hidden"):
        export_blind_ratings(project_id, db_path)
    with pytest.raises(BlindCodingError, match="2 assignments remain"):
        advance_blind_phase(project_id, db_path)


def test_agreement_adjudication_and_exports_preserve_raw_ratings(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project_id, image_ids = make_project(
        db_path, project, taxonomy, methodology, visualization, minimal_stops
    )
    configure_blind_protocol(
        project_id,
        codebook_version="v2",
        target_ratings=3,
        agreement_threshold=0.8,
        path=db_path,
    )
    coders = ["CODER-A", "CODER-B", "CODER-C"]
    create_blind_assignments(project_id, coders, image_ids[:1], db_path)
    advance_blind_phase(project_id, db_path)
    ratings = {
        "CODER-A": standard_rating(),
        "CODER-B": standard_rating(confidence=5),
        "CODER-C": standard_rating(
            shade_source="shelter",
            coverage="high",
            waiting_area_covered="no",
            permanence="permanent",
            image_adequacy="marginal",
            confidence=2,
            location_recognized="yes",
        ),
    }
    for coder in coders:
        assignment = list_coder_assignments(project_id, coder, db_path).iloc[0]
        submit_blind_rating(
            project_id, assignment["assignment_id"], coder, ratings[coder], db_path
        )

    assert advance_blind_phase(project_id, db_path) == "adjudication"
    report = blind_agreement_report(project_id, db_path).set_index("variable")
    raw_before = export_blind_ratings(project_id, db_path)
    queue = blind_adjudication_queue(project_id, db_path)

    assert report.loc["shade_source", "percent_agreement"] == pytest.approx(1 / 3)
    assert report.loc["coverage", "measurement"] == "ordinal"
    assert len(raw_before) == 3
    assert set(raw_before["rater_id"]) == set(coders)
    assert raw_before["stop_id"].tolist() == ["1001", "1001", "1001"]
    assert queue.loc[0, "needs_adjudication"]
    assert queue.loc[0, "shade_source"] == "1 shelter, 2 tree"
    assert "rater_id" not in queue
    assert "coder_id" not in queue
    with pytest.raises(BlindCodingError, match="1 required decision remains"):
        advance_blind_phase(project_id, db_path)

    blind_image_id = queue.loc[0, "blind_image_id"]
    decision = standard_rating(location_recognized="no", confidence=5)
    adjudication_id = submit_blind_adjudication(
        project_id,
        blind_image_id,
        "ADJ-1",
        decision,
        notes="Image supports tree shade.",
        path=db_path,
    )
    with pytest.raises(BlindCodingError, match="already locked"):
        submit_blind_adjudication(
            project_id, blind_image_id, "ADJ-2", decision, path=db_path
        )

    adjudications = export_blind_adjudications(project_id, db_path)
    raw_after = export_blind_ratings(project_id, db_path)
    assert adjudication_id
    assert adjudications.loc[0, "adjudicator_id"] == "ADJ-1"
    assert adjudications.loc[0, "notes"] == "Image supports tree shade."
    assert raw_after.equals(raw_before)
    assert advance_blind_phase(project_id, db_path) == "closed"
    assert '"submission_policy": "immutable"' in protocol_json(project_id, db_path)


def test_protocol_rejects_underpowered_or_invalid_configuration(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project_id = create_project(
        project, taxonomy, methodology, visualization, minimal_stops, [], db_path
    )
    with pytest.raises(BlindCodingError, match="at least three"):
        configure_blind_protocol(
            project_id, codebook_version="v1", target_ratings=2, path=db_path
        )
    with pytest.raises(BlindCodingError, match="between 0 and 1"):
        configure_blind_protocol(
            project_id,
            codebook_version="v1",
            target_ratings=3,
            agreement_threshold=1.2,
            path=db_path,
        )


def test_stop_level_protocol_creates_one_rating_per_stop_with_bundled_evidence(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project_id, _ = make_project(
        db_path, project, taxonomy, methodology, visualization, minimal_stops
    )
    add_image(
        project_id,
        {
            "stop_id": "1001",
            "uri": "https://images.example.org/standardized/extra.jpg",
            "image_type": "field_survey",
        },
        db_path,
    )
    protocol = configure_blind_protocol(
        project_id,
        codebook_version="stop-v1",
        target_ratings=3,
        assessment_unit="stop",
        path=db_path,
    )
    coders = ["CODER-A", "CODER-B", "CODER-C"]

    assert create_blind_assignments(project_id, coders, ["1001"], db_path) == 3
    coder_assignment = list_coder_assignments(project_id, "CODER-A", db_path)

    assert protocol["assessment_unit"] == "stop"
    assert len(coder_assignment) == 1
    assert coder_assignment.loc[0, "stop_id"] == "1001"
    assert coder_assignment.loc[0, "stop_name"] == "Main St & 1st Ave"
    assert len(coder_assignment.loc[0, "evidence_sources"]) == 2
    assert re.fullmatch(r"STOP-\d{6}", coder_assignment.loc[0, "display_id"])
    assert blind_progress(project_id, db_path)["stops"] == 1

    assert advance_blind_phase(project_id, db_path) == "coding"
    for coder in coders:
        assignment_id = list_coder_assignments(project_id, coder, db_path).loc[0, "assignment_id"]
        submit_blind_rating(
            project_id,
            assignment_id,
            coder,
            standard_rating(review_method="field_survey"),
            db_path,
        )
    assert advance_blind_phase(project_id, db_path) == "adjudication"

    exported = export_blind_ratings(project_id, db_path)
    assert len(exported) == 3
    assert exported["assessment_unit"].tolist() == ["stop", "stop", "stop"]
    assert exported["stop_id"].tolist() == ["1001", "1001", "1001"]
    assert exported["image_id"].isna().all()
    assert exported["review_method"].tolist() == ["field_survey"] * 3
    assert '"assessment_unit": "stop"' in protocol_json(project_id, db_path)

    queue = blind_adjudication_queue(project_id, db_path)
    assert queue.loc[0, "blind_stop_id"] == queue.loc[0, "blind_unit_id"]
    submit_blind_adjudication(
        project_id,
        queue.loc[0, "blind_unit_id"],
        "ADJ-STOP",
        standard_rating(),
        notes="Stop-level consensus.",
        path=db_path,
    )
    adjudications = export_blind_adjudications(project_id, db_path)
    assert adjudications.loc[0, "assessment_unit"] == "stop"
    assert adjudications.loc[0, "stop_id"] == "1001"
    assert adjudications.loc[0, "image_id"] is None


def test_explicit_empty_unit_selection_does_not_assign_everything(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project_id, _ = make_project(
        db_path, project, taxonomy, methodology, visualization, minimal_stops
    )
    with pytest.raises(BlindCodingError, match="Select at least one"):
        create_blind_assignments(
            project_id, ["CODER-A", "CODER-B", "CODER-C"], [], db_path
        )


def test_krippendorff_alpha_normalizes_units_with_unequal_rater_counts():
    ratings = pd.DataFrame(
        [("U1", "none"), ("U1", "high"), ("U2", "none"), ("U2", "none"), ("U2", "none")],
        columns=["blind_image_id", "coverage"],
    )

    assert krippendorff_alpha(ratings, "coverage") == pytest.approx(0.0)


def test_assignment_creation_rejects_concurrent_assessment_unit_change(
    db_path, project, taxonomy, methodology, visualization, minimal_stops, monkeypatch
):
    import shade_gis.blind_coding as blind_coding

    project_id, _ = make_project(
        db_path, project, taxonomy, methodology, visualization, minimal_stops
    )
    current = configure_blind_protocol(
        project_id,
        codebook_version="v1",
        target_ratings=3,
        assessment_unit="stop",
        path=db_path,
    )
    stale = {**current, "assessment_unit": "image"}
    monkeypatch.setattr(blind_coding, "get_blind_protocol", lambda *_args, **_kwargs: stale)

    with pytest.raises(BlindCodingError, match="Protocol settings changed"):
        create_blind_assignments(project_id, ["A", "B", "C"], path=db_path)
