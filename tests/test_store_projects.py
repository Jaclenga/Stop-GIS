from __future__ import annotations

import json
import inspect
import sqlite3

import pytest

from stop_gis.persistence.store import (
    DuplicateProjectNameError,
    ProjectConflictError,
    add_shade_label,
    copy_readonly_source_to_fallback,
    create_project,
    database_path,
    delete_project,
    init_database,
    list_projects,
    load_project_bundle,
    mark_project_store_initialized,
    migrate_shade_source_labels,
    project_store_initialized,
    save_project_bundle,
    update_project_details,
)


def test_database_path_tracks_environment_override_changes(db_path, monkeypatch):
    first = db_path.with_name("first-isolated.sqlite3")
    second = db_path.with_name("second-isolated.sqlite3")

    monkeypatch.setenv("STOP_GIS_DB_PATH", str(first))
    assert database_path() == first.resolve()

    monkeypatch.setenv("STOP_GIS_DB_PATH", str(second))
    assert database_path() == second.resolve()

    monkeypatch.delenv("STOP_GIS_DB_PATH")
    assert database_path() == db_path.resolve()


def test_create_project_roundtrip(db_path, project, taxonomy, methodology, visualization, minimal_stops):
    methodology["terminology"] = [
        {"term": "Boarding Zone", "operational_definition": "Project-specific boarding location."}
    ]
    project_id = create_project(project, taxonomy, methodology, visualization, minimal_stops, [], db_path)

    bundle = load_project_bundle(project_id, db_path)

    assert bundle["project"]["name"] == "Test Shade Study"
    assert bundle["project"]["agency"] == "Test Transit"
    assert bundle["project"]["region"] == "Test City"
    assert len(bundle["stops"]) == 2
    assert set(bundle["stops"]["stop_id"]) == {"1001", "1002"}
    assert bundle["stops"].loc[bundle["stops"]["stop_id"] == "1001", "context_label"].iloc[0] == "High"
    assert bundle["taxonomy"][0]["name"] == taxonomy[0]["name"]
    assert bundle["methodology"]["terminology"] == methodology["terminology"]


def test_project_bundle_reads_use_one_explicit_snapshot_transaction():
    source = inspect.getsource(load_project_bundle)

    assert 'conn.execute("BEGIN")' in source
    assert source.index('conn.execute("BEGIN")') < source.index('SELECT * FROM projects')


def test_save_project_updates_metadata_without_corrupting_stops(db_path, project, taxonomy, methodology, visualization, minimal_stops):
    project_id = create_project(project, taxonomy, methodology, visualization, minimal_stops, [], db_path)
    project["name"] = "Updated Shade Study"
    project["dataset_version"] = "test-2"

    save_project_bundle(project_id, project, taxonomy, methodology, visualization, minimal_stops, [], db_path)
    bundle = load_project_bundle(project_id, db_path)
    projects = list_projects(db_path)

    assert bundle["project"]["name"] == "Updated Shade Study"
    assert bundle["project"]["dataset_version"] == "test-2"
    assert len(bundle["stops"]) == 2
    assert projects[0]["id"] == project_id
    assert projects[0]["location_count"] == 2
    assert projects[0]["reviewed_count"] == 0
    assert projects[0]["labeled_count"] == 2
    assert projects[0]["awaiting_review_count"] == 2


def test_save_project_preserves_stop_blind_assignments(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project_id = create_project(
        project, taxonomy, methodology, visualization, minimal_stops, [], db_path
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO blind_stops (id, project_id, stop_id, display_id, created_at)
            VALUES ('blind-stop-1', ?, '1001', 'STOP-000001', '2026-08-01T12:00:00Z')
            """,
            (project_id,),
        )
        conn.execute(
            """
            INSERT INTO blind_stop_assignments (
                id, project_id, blind_stop_id, coder_id, sort_order, assigned_at
            ) VALUES ('assignment-1', ?, 'blind-stop-1', 'coder-a', 1, '2026-08-01T12:00:00Z')
            """,
            (project_id,),
        )
        conn.commit()

    bundle = load_project_bundle(project_id, db_path)
    save_project_bundle(
        project_id,
        bundle["project"],
        bundle["taxonomy"],
        bundle["methodology"],
        bundle["visualization"],
        bundle["stops"],
        bundle["import_log"],
        db_path,
    )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM blind_stops").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM blind_stop_assignments").fetchone()[0] == 1


def test_stale_project_snapshot_cannot_overwrite_newer_save(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project_id = create_project(
        project, taxonomy, methodology, visualization, minimal_stops, [], db_path
    )
    session_a = load_project_bundle(project_id, db_path)
    session_b = load_project_bundle(project_id, db_path)
    session_a["project"]["name"] = "Session A"
    save_project_bundle(
        project_id,
        session_a["project"],
        session_a["taxonomy"],
        session_a["methodology"],
        session_a["visualization"],
        session_a["stops"],
        session_a["import_log"],
        db_path,
    )

    session_b["project"]["name"] = "Session B"
    with pytest.raises(ProjectConflictError, match="updated in another session"):
        save_project_bundle(
            project_id,
            session_b["project"],
            session_b["taxonomy"],
            session_b["methodology"],
            session_b["visualization"],
            session_b["stops"],
            session_b["import_log"],
            db_path,
        )

    assert load_project_bundle(project_id, db_path)["project"]["name"] == "Session A"


def test_stale_bundle_cannot_delete_stop_with_new_child_evidence(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project_id = create_project(
        project, taxonomy, methodology, visualization, minimal_stops, [], db_path
    )
    stale = load_project_bundle(project_id, db_path)
    add_shade_label(
        project_id,
        {"stop_id": "1002", "shade_category": "No Shade", "source": "manual"},
        db_path,
    )
    stale["stops"] = stale["stops"][stale["stops"]["stop_id"] != "1002"].copy()

    with pytest.raises(ProjectConflictError, match="received new evidence"):
        save_project_bundle(
            project_id, stale["project"], stale["taxonomy"], stale["methodology"],
            stale["visualization"], stale["stops"], stale["import_log"], db_path,
        )

    assert set(load_project_bundle(project_id, db_path)["stops"]["stop_id"]) == {"1001", "1002"}


def test_fresh_bundle_can_delete_stop_with_preexisting_child_evidence(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project_id = create_project(
        project, taxonomy, methodology, visualization, minimal_stops, [], db_path
    )
    add_shade_label(
        project_id,
        {"stop_id": "1002", "shade_category": "No Shade", "source": "manual"},
        db_path,
    )
    fresh = load_project_bundle(project_id, db_path)
    fresh["stops"] = fresh["stops"][fresh["stops"]["stop_id"] != "1002"].copy()

    save_project_bundle(
        project_id, fresh["project"], fresh["taxonomy"], fresh["methodology"],
        fresh["visualization"], fresh["stops"], fresh["import_log"], db_path,
    )

    assert set(load_project_bundle(project_id, db_path)["stops"]["stop_id"]) == {"1001"}


def test_bundle_review_event_rolls_back_with_project_update(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project_id = create_project(
        project, taxonomy, methodology, visualization, minimal_stops, [], db_path
    )
    bundle = load_project_bundle(project_id, db_path)
    original_name = bundle["project"]["name"]
    bundle["project"]["name"] = "Must roll back"

    with pytest.raises(sqlite3.IntegrityError, match="review history stop"):
        save_project_bundle(
            project_id, bundle["project"], bundle["taxonomy"], bundle["methodology"],
            bundle["visualization"], bundle["stops"], bundle["import_log"], db_path,
            review_event={"stop_id": "missing", "action": "invalid"},
        )

    assert load_project_bundle(project_id, db_path)["project"]["name"] == original_name


def test_source_label_migration_does_not_rewrite_arbitrary_prose(db_path):
    init_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO projects (id, name, created_at, updated_at) VALUES ('p', 'P', 'now', 'now')"
        )
        methodology = {
            "summary": "A constructed example; Manmade is quoted here as historical prose.",
            "enum": "Constructed",
        }
        conn.execute(
            "INSERT INTO project_settings (project_id, methodology_json, visualization_json, updated_at) VALUES (?, ?, '{}', 'now')",
            ("p", json.dumps(methodology)),
        )
        migrate_shade_source_labels(conn)
        migrated = json.loads(
            conn.execute("SELECT methodology_json FROM project_settings WHERE project_id='p'").fetchone()[0]
        )

    assert migrated["summary"] == methodology["summary"]
    assert migrated["enum"] == "Purpose-built"


def test_readonly_source_never_overwrites_a_populated_fallback(db_path):
    source = db_path.with_name("readonly-source.sqlite3")
    fallback = db_path.with_name("writable-fallback.sqlite3")
    for path, project_id in ((source, "source-only"), (fallback, "fallback-only")):
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO projects (id) VALUES (?)", (project_id,))

    copy_readonly_source_to_fallback(source, fallback)

    with sqlite3.connect(fallback) as conn:
        assert conn.execute("SELECT id FROM projects").fetchall() == [("fallback-only",)]

    fresh_fallback = db_path.with_name("fresh-fallback.sqlite3")
    copy_readonly_source_to_fallback(source, fresh_fallback)
    with sqlite3.connect(fresh_fallback) as conn:
        assert conn.execute("SELECT id FROM projects").fetchall() == [("source-only",)]

    initialized_fallback = db_path.with_name("initialized-empty-fallback.sqlite3")
    init_database(initialized_fallback)
    mark_project_store_initialized(initialized_fallback)
    copy_readonly_source_to_fallback(source, initialized_fallback)
    with sqlite3.connect(initialized_fallback) as conn:
        assert conn.execute("SELECT id FROM projects").fetchall() == [("source-only",)]


def test_nested_custom_stop_values_roundtrip_as_json_values(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    minimal_stops["sensor_metadata"] = [
        {"bands": ["temperature", "humidity"], "calibration": {"offset": 1.5}},
        ["mobile", {"quality": "reviewed"}],
    ]

    project_id = create_project(
        project, taxonomy, methodology, visualization, minimal_stops, [], db_path
    )
    loaded = load_project_bundle(project_id, db_path)["stops"].set_index("stop_id")

    assert loaded.loc["1001", "sensor_metadata"] == {
        "bands": ["temperature", "humidity"],
        "calibration": {"offset": 1.5},
    }
    assert loaded.loc["1002", "sensor_metadata"] == ["mobile", {"quality": "reviewed"}]


def test_project_boundary_triggers_reject_cross_project_evidence(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    first_project = create_project(
        dict(project), taxonomy, methodology, visualization, minimal_stops, [], db_path
    )
    second_project = create_project(
        {**project, "name": "Second Study"},
        taxonomy,
        methodology,
        visualization,
        minimal_stops,
        [],
        db_path,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO images (id, project_id, stop_id, uri, created_at) VALUES (?, ?, ?, ?, ?)",
            ("first-image", first_project, "1001", "https://example.com/first.jpg", "2026-08-22"),
        )
        conn.execute(
            "INSERT INTO blind_images (id, project_id, image_id, display_id, created_at) VALUES (?, ?, ?, ?, ?)",
            ("first-blind-image", first_project, "first-image", "IMG-000001", "2026-08-22"),
        )

        with pytest.raises(sqlite3.IntegrityError, match="label references"):
            conn.execute(
                """
                INSERT INTO shade_labels (id, project_id, stop_id, image_id, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("cross-label", second_project, "1001", "first-image", "manual", "2026-08-22"),
            )
        with pytest.raises(sqlite3.IntegrityError, match="blind assignment"):
            conn.execute(
                """
                INSERT INTO blind_assignments (
                    id, project_id, blind_image_id, coder_id, sort_order, assigned_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("cross-assignment", second_project, "first-blind-image", "coder", 1, "2026-08-22"),
            )
        with pytest.raises(sqlite3.IntegrityError, match="blind stop"):
            conn.execute(
                """
                INSERT INTO blind_stops (id, project_id, stop_id, display_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("cross-blind-stop", second_project, "missing-stop", "STOP-X", "2026-08-22"),
            )


def test_image_reassignment_cannot_invalidate_a_linked_label(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project_id = create_project(
        project, taxonomy, methodology, visualization, minimal_stops, [], db_path
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO images (id, project_id, stop_id, uri, created_at) VALUES (?, ?, ?, ?, ?)",
            ("image-1", project_id, "1001", "https://example.com/one.jpg", "2026-08-22"),
        )
        conn.execute(
            """
            INSERT INTO shade_labels (id, project_id, stop_id, image_id, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("label-1", project_id, "1001", "image-1", "manual", "2026-08-22"),
        )

        with pytest.raises(sqlite3.IntegrityError, match="invalidate linked labels"):
            conn.execute("UPDATE images SET stop_id = ? WHERE id = ?", ("1002", "image-1"))


def test_stop_delete_cascades_blind_stop_records_when_foreign_keys_are_disabled(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project_id = create_project(
        project, taxonomy, methodology, visualization, minimal_stops, [], db_path
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "INSERT INTO blind_stops (id, project_id, stop_id, display_id, created_at) VALUES (?, ?, ?, ?, ?)",
            ("blind-stop", project_id, "1001", "STOP-1", "2026-08-22"),
        )
        conn.execute(
            """
            INSERT INTO blind_stop_assignments (
                id, project_id, blind_stop_id, coder_id, sort_order, assigned_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("blind-assignment", project_id, "blind-stop", "coder", 1, "2026-08-22"),
        )
        conn.execute(
            "DELETE FROM stops WHERE project_id = ? AND stop_id = ?",
            (project_id, "1001"),
        )

        assert conn.execute("SELECT COUNT(*) FROM blind_stops").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM blind_stop_assignments").fetchone()[0] == 0


def test_project_details_update_rejects_a_stale_revision(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project_id = create_project(
        project, taxonomy, methodology, visualization, minimal_stops, [], db_path
    )
    stale_revision = load_project_bundle(project_id, db_path)["project"]["_store_updated_at"]
    update_project_details(project_id, name="First edit", path=db_path)

    with pytest.raises(ProjectConflictError, match="updated in another session"):
        update_project_details(
            project_id,
            name="Stale edit",
            expected_revision=stale_revision,
            path=db_path,
        )


def test_list_projects_counts_raw_labels_toward_label_progress(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    minimal_stops[["shading", "shade_coverage"]] = "Unknown"
    project_id = create_project(
        project, taxonomy, methodology, visualization, minimal_stops, [], db_path
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO shade_labels (id, project_id, stop_id, source, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("raw-label", project_id, "1001", "manual", "2026-07-16T12:00:00-04:00"),
        )
        conn.commit()

    projects = list_projects(db_path)

    assert projects[0]["location_count"] == 2
    assert projects[0]["labeled_count"] == 1


def test_deployment_settings_roundtrip_with_project(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project["deployment"] = {
        "github_username": "example-owner",
        "destination_repository": "shade-study-site",
        "repository": "example-owner/shade-study-site",
        "branch": "main",
        "commit_message": "Publish field update",
        "mode": "existing",
        "visibility": "private",
        "public_url": "https://example-shade.streamlit.app",
    }

    project_id = create_project(
        project, taxonomy, methodology, visualization, minimal_stops, [], db_path
    )
    reloaded = load_project_bundle(project_id, db_path)

    assert reloaded["project"]["deployment"] == project["deployment"]


def test_update_project_details_preserves_project_data(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project_id = create_project(
        project, taxonomy, methodology, visualization, minimal_stops, [], db_path
    )

    update_project_details(
        project_id,
        name="Renamed Shade Study",
        agency="Regional Transit",
        region="New Study Area",
        description="Updated from project settings.",
        visibility="Public",
        path=db_path,
    )
    bundle = load_project_bundle(project_id, db_path)

    assert bundle["project"]["name"] == "Renamed Shade Study"
    assert bundle["project"]["agency"] == "Regional Transit"
    assert bundle["project"]["region"] == "New Study Area"
    assert bundle["project"]["description"] == "Updated from project settings."
    assert bundle["project"]["visibility"] == "Public"
    assert len(bundle["stops"]) == 2
    assert bundle["taxonomy"] == taxonomy
    assert bundle["methodology"]["summary"] == methodology["summary"]

    with pytest.raises(ValueError, match="Project name is required"):
        update_project_details(project_id, name="   ", path=db_path)


def test_project_names_are_unique_ignoring_case_and_whitespace(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    first_project_id = create_project(
        project, taxonomy, methodology, visualization, minimal_stops, [], db_path
    )

    with pytest.raises(DuplicateProjectNameError, match="already exists"):
        create_project(
            {**project, "name": "  test   SHADE study  "},
            taxonomy,
            methodology,
            visualization,
            minimal_stops,
            [],
            db_path,
        )

    second_project_id = create_project(
        {**project, "name": "Second Study"},
        taxonomy,
        methodology,
        visualization,
        minimal_stops,
        [],
        db_path,
    )
    with pytest.raises(DuplicateProjectNameError, match="already exists"):
        update_project_details(
            second_project_id,
            name=" TEST  shade Study ",
            path=db_path,
        )

    second_bundle = load_project_bundle(second_project_id, db_path)
    second_bundle["project"]["name"] = " test shade study "
    with pytest.raises(DuplicateProjectNameError, match="already exists"):
        save_project_bundle(
            second_project_id,
            second_bundle["project"],
            second_bundle["taxonomy"],
            second_bundle["methodology"],
            second_bundle["visualization"],
            second_bundle["stops"],
            second_bundle["import_log"],
            db_path,
        )

    assert [item["name"] for item in list_projects(db_path)] == [
        "Second Study",
        "Test Shade Study",
    ]
    assert load_project_bundle(first_project_id, db_path)["project"]["name"] == (
        "Test Shade Study"
    )


def test_delete_project_cascades_through_related_data(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project_id = create_project(
        project, taxonomy, methodology, visualization, minimal_stops, [], db_path
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO images (id, project_id, stop_id, uri, created_at) VALUES (?, ?, ?, ?, ?)",
            ("image-1", project_id, "1001", "https://example.com/image.jpg", "2026-07-14T12:00:00-04:00"),
        )
        conn.execute(
            """
            INSERT INTO shade_labels (id, project_id, stop_id, image_id, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("label-1", project_id, "1001", "image-1", "manual", "2026-07-14T12:01:00-04:00"),
        )
        conn.execute(
            """
            INSERT INTO review_history (id, project_id, stop_id, action, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("review-1", project_id, "1001", "accepted", "2026-07-14T12:02:00-04:00"),
        )
        conn.execute(
            """
            INSERT INTO releases (id, project_id, version, created_at)
            VALUES (?, ?, ?, ?)
            """,
            ("release-1", project_id, "v1", "2026-07-14T12:03:00-04:00"),
        )
        conn.commit()

    assert delete_project(project_id, db_path) is True
    assert delete_project(project_id, db_path) is False
    assert list_projects(db_path) == []
    with pytest.raises(KeyError, match="was not found"):
        load_project_bundle(project_id, db_path)

    with sqlite3.connect(db_path) as conn:
        for table in (
            "project_settings",
            "shade_taxonomy",
            "stops",
            "images",
            "shade_labels",
            "review_history",
            "releases",
            "import_logs",
        ):
            count = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
            assert count == 0, table


def test_project_store_initialization_marker_survives_deleting_last_project(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    assert project_store_initialized(db_path) is False
    project_id = create_project(
        project, taxonomy, methodology, visualization, minimal_stops, [], db_path
    )
    mark_project_store_initialized(db_path)

    assert project_store_initialized(db_path) is True
    assert delete_project(project_id, db_path) is True
    assert list_projects(db_path) == []
    assert project_store_initialized(db_path) is True


def test_init_database_adds_deployment_settings_column_to_existing_database(db_path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (id TEXT PRIMARY KEY);
            CREATE TABLE project_settings (
                project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
                methodology_json TEXT NOT NULL DEFAULT '{}',
                visualization_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            );
            """
        )

    init_database(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(project_settings)")}
    assert "deployment_json" in columns


def test_init_database_migrates_retired_shade_source_labels(
    db_path, project, taxonomy, methodology, visualization, minimal_stops
):
    project_id = create_project(
        project, taxonomy, methodology, visualization, minimal_stops, [], db_path
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE stops SET shading = ?, shade_sources = ? WHERE project_id = ? AND stop_id = ?",
            ("Constructed Shade", "Natural; Manmade", project_id, "1001"),
        )
        conn.execute(
            """
            INSERT INTO shade_labels (
                id, project_id, stop_id, shade_category, shade_sources, source, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-label",
                project_id,
                "1001",
                "Constructed Shade",
                "Constructed; Manmade",
                "manual",
                '{"source_label": "Manmade"}',
                "2026-07-14T00:00:00-04:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO review_history (
                id, project_id, stop_id, action, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-review",
                project_id,
                "1001",
                "label_updated",
                '{"from_sources": "Constructed", "to_sources": "Manmade"}',
                "2026-07-14T00:00:00-04:00",
            ),
        )
        conn.commit()

    init_database(db_path)

    with sqlite3.connect(db_path) as conn:
        stop = conn.execute(
            "SELECT shading, shade_sources FROM stops WHERE project_id = ? AND stop_id = ?",
            (project_id, "1001"),
        ).fetchone()
        label = conn.execute(
            "SELECT shade_category, shade_sources, metadata_json FROM shade_labels WHERE id = 'legacy-label'"
        ).fetchone()
        review_metadata = conn.execute(
            "SELECT metadata_json FROM review_history WHERE id = 'legacy-review'"
        ).fetchone()[0]

    assert stop == ("Purpose-built Shade", "Natural; Incidental")
    assert label == (
        "Purpose-built Shade",
        "Purpose-built; Incidental",
        '{"source_label": "Incidental"}',
    )
    assert review_metadata == '{"from_sources": "Purpose-built", "to_sources": "Incidental"}'
