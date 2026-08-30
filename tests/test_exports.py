from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pytest

import builder_app
import published_app
from builder_app import (
    build_github_deploy_bundle,
    dataframe_to_geojson,
    deployment_session_freshness_issue,
    study_config_json,
)
from platform_store import add_shade_label, create_project, list_shade_labels, save_project_bundle
from shade_gis.deploy import (
    deploy_launcher_script,
    deploy_readme,
    deploy_script,
    streamlit_entrypoint_path,
)


def test_export_csv_geojson_raw_labels_and_config(
    db_path,
    project,
    taxonomy,
    methodology,
    visualization,
    minimal_stops,
):
    terminology = [
        {"term": "Boarding Zone", "operational_definition": "Project-specific boarding location."}
    ]
    source_taxonomy = [
        {"code": "Natural", "shade_source": "Vegetation", "operational_definition": "Custom natural definition."},
        {"code": "Purpose-built", "shade_source": "Shelter", "operational_definition": "Custom built definition."},
        {"code": "Incidental", "shade_source": "Nearby Structure", "operational_definition": "Custom incidental definition."},
    ]
    coverage_taxonomy = [
        {"code": "No Shade", "shade_coverage": "Unshaded", "operational_definition": "Custom no-shade definition."},
        {"code": "Limited Shade", "shade_coverage": "Partial Shade", "operational_definition": "Custom limited definition."},
        {"code": "Significant Shade", "shade_coverage": "Mostly Shaded", "operational_definition": "Custom significant definition."},
    ]
    methodology["terminology"] = terminology
    methodology["shade_source_taxonomy"] = source_taxonomy
    methodology["shade_coverage_taxonomy"] = coverage_taxonomy
    taxonomy[1]["description"] = "Custom limited definition."
    project["deployment"] = {
        "github_username": "private-owner-setting",
        "destination_repository": "private-repository-setting",
    }
    project_id = create_project(project, taxonomy, methodology, visualization, minimal_stops, [], db_path)
    add_shade_label(
        project_id,
        {
            "stop_id": "1001",
            "labeler_id": "alice",
            "labeler_role": "Expert",
            "shade_category": "No Shade",
            "confidence": 0.95,
            "source": "expert_review",
        },
        db_path,
    )
    builder_app.st.session_state.clear()
    builder_app.st.session_state["active_project_id"] = project_id
    builder_app.st.session_state["project"] = project
    builder_app.st.session_state["taxonomy"] = taxonomy
    builder_app.st.session_state["methodology"] = methodology
    builder_app.st.session_state["visualization"] = visualization
    builder_app.st.session_state["stops"] = minimal_stops
    builder_app.st.session_state["import_log"] = [{"source": "pytest", "format": "CSV", "rows": 2}]

    stops_csv = minimal_stops.to_csv(index=False)
    labels_csv = list_shade_labels(project_id, path=db_path).to_csv(index=False)
    geojson = json.loads(dataframe_to_geojson(minimal_stops))
    config = json.loads(study_config_json())

    assert len(pd.read_csv(io.StringIO(stops_csv))) == 2
    assert len(pd.read_csv(io.StringIO(labels_csv))) == 1
    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 2
    assert geojson["features"][0]["properties"]["stop_id"] == "1001"
    assert config["project"]["name"] == "Test Shade Study"
    assert "deployment" not in config["project"]
    assert config["study_id"] == project_id
    assert config["taxonomy"][0]["name"] == taxonomy[0]["name"]
    assert config["terminology"] == terminology
    assert config["shade_source_taxonomy"] == source_taxonomy
    assert config["shade_coverage_taxonomy"] == coverage_taxonomy
    assert config["taxonomy"][1]["description"] == "Custom limited definition."
    assert config["visualization"]["voting"]["shade_source_taxonomy"] == source_taxonomy
    assert "terminology" not in config["methodology"]
    assert "shade_source_taxonomy" not in config["methodology"]
    assert "shade_coverage_taxonomy" not in config["methodology"]
    assert config["import_log"][0]["rows"] == 2

    commit_message = "Publish July field review"
    bundle_bytes = build_github_deploy_bundle(
        "owner/test-shade-study",
        commit_message=commit_message,
    )
    with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as bundle:
        bundle_names = set(bundle.namelist())
        assert "public_voting.py" in bundle_names
        assert "stop_gis/public_app.py" in bundle_names
        assert "stop_gis/public_voting.py" in bundle_names
        assert "stop_gis/assessment_modes.py" in bundle_names
        assert "stop_audit_codebook.json" in bundle_names
        assert "deployment_manifest.json" in bundle_names
        assert "migrations/001_public_voting.sql" in bundle_names
        assert "migrations/least_privilege_roles.sql.example" in bundle_names
        assert "scripts/verify_database.py" in bundle_names
        assert "scripts/migrate_database.py" in bundle_names
        assert ".streamlit/secrets.toml.example" in bundle_names
        assert ".env.example" in bundle_names
        assert "DEPLOYMENT.md" in bundle_names
        assert "builder_app.py" not in bundle_names
        assert "platform_store.py" not in bundle_names
        assert not any(name.startswith("shade_gis/") for name in bundle_names)
        assert "psycopg[binary]>=3.2,<4" in bundle.read("requirements.txt").decode("utf-8")
        assert "psycopg_pool>=3.2,<4" in bundle.read("requirements.txt").decode("utf-8")
        assert "Authlib>=1.3.2,<2" in bundle.read("requirements.txt").decode("utf-8")
        secrets_example = bundle.read(".streamlit/secrets.toml.example").decode("utf-8")
        assert 'STOP_GIS_VOTE_DATABASE_URL = "postgresql://RUNTIME_USER:PASSWORD@HOST:5432/DATABASE?sslmode=require"' in secrets_example
        assert 'STOP_GIS_VOTE_FINGERPRINT_SECRET = "YOUR_BROWSER_GENERATED_SECRET"' in secrets_example
        assert "postgresql://user:password@" not in secrets_example.lower()
        assert "CREATE UNIQUE INDEX IF NOT EXISTS shade_votes_network_unique_idx" in bundle.read(
            "migrations/001_public_voting.sql"
        ).decode("utf-8")
        migration = bundle.read("migrations/001_public_voting.sql").decode("utf-8")
        assert "pg_advisory_xact_lock" in migration
        assert "schema_version" in migration
        assert "DROP TABLE" not in migration.upper()
        roles = bundle.read("migrations/least_privilege_roles.sql.example").decode("utf-8")
        assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE shade_votes" in roles
        assert "GRANT SELECT ON TABLE shade_vote_settings" in roles
        assert "ALTER DEFAULT PRIVILEGES" not in roles
        assert "ON TABLES TO shade_gis_app" not in roles
        assert "CREATE ROLE" not in roles.upper()
        assert "not create, own, centrally store" in bundle.read(
            "DEPLOYMENT.md"
        ).decode("utf-8")
        assert "*.sqlite3" in bundle.read(".gitignore").decode("utf-8")
        gitignore = bundle.read(".gitignore").decode("utf-8")
        assert ".env.*" in gitignore
        assert ".streamlit/secrets.toml" in gitignore
        assert "enableXsrfProtection = true" in bundle.read(
            ".streamlit/config.toml"
        ).decode("utf-8")
        assert "confirm_vote_database_read_write" in bundle.read(
            "scripts/verify_database.py"
        ).decode("utf-8")
        bundle_readme = bundle.read("README.md").decode("utf-8")
        assert bundle_readme.count("& {") >= 1
        assert "[string]::IsNullOrWhiteSpace($RepositoryName)" in bundle_readme
        assert 'Filter "deploy_to_github.ps1" -File -Recurse' in bundle_readme
        manifest = json.loads(bundle.read("deployment_manifest.json"))
        assert manifest["schema_version"] == 1
        assert manifest["repository"] == "owner/test-shade-study"
        assert manifest["deploy_mode"] == "existing"
        assert manifest["commit_message"] == commit_message
        assert manifest["entrypoint"] == "preview_app/app.py"
        assert manifest["files"]["app.py"] == hashlib.sha256(bundle.read("app.py")).hexdigest()
        deployed_stops = pd.read_csv(bundle.open("shade_study_stops.csv"), dtype={"stop_id": str})
        assert deployed_stops["stop_id"].tolist() == minimal_stops["stop_id"].tolist()
        assert deployed_stops["context_label"].tolist() == minimal_stops["context_label"].tolist()
        assert manifest["dataset"] == {
            "file": "shade_study_stops.csv",
            "rows": 2,
            "columns": deployed_stops.columns.tolist(),
            "sha256": hashlib.sha256(bundle.read("shade_study_stops.csv")).hexdigest(),
        }
        assert f"test-shade-study-{manifest['bundle_id'][:12]}.zip" in bundle_readme
        assert "$CommitMessage = 'Publish July field review'" in bundle_readme
        assert '-CommitMessage $CommitMessage' in bundle_readme
        assert 'Invoke-Native "git" @("commit", "-m", $CommitMessage)' in bundle.read(
            "deploy_to_github.ps1"
        ).decode("utf-8")
        deployed_config = json.loads(bundle.read("shade_study_config.json"))
        deployed_codebook = json.loads(bundle.read("stop_audit_codebook.json"))
        assert deployed_codebook["schema_version"] == 1
        assert deployed_codebook["dimensions"] == deployed_config["codebook"]["dimensions"]
        assert deployed_config["terminology"] == terminology
        assert deployed_config["shade_source_taxonomy"] == source_taxonomy
        assert deployed_config["shade_coverage_taxonomy"] == coverage_taxonomy
        assert "data_taxonomy" not in deployed_config
        assert deployed_config["visualization"]["voting"]["enabled"] is False
        assert deployed_config["visualization"]["voting"]["options"] == [
            "No Shade",
            "Limited Shade",
            "Significant Shade",
        ]

    generated_runtime = db_path.parent / "generated-runtime"
    with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as bundle:
        bundle.extractall(generated_runtime)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import app; import stop_gis.public_app as shared; "
                "assert app is shared; assert callable(app.main)"
            ),
        ],
        cwd=generated_runtime,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_deployment_bundle_requires_imported_project_data(
    project,
    visualization,
):
    builder_app.st.session_state.clear()
    builder_app.st.session_state["active_project_id"] = "empty-project"
    builder_app.st.session_state["project"] = project
    builder_app.st.session_state["taxonomy"] = []
    builder_app.st.session_state["methodology"] = {}
    builder_app.st.session_state["visualization"] = visualization
    builder_app.st.session_state["stops"] = pd.DataFrame()
    builder_app.st.session_state["import_log"] = []

    with pytest.raises(ValueError, match="Import project data"):
        build_github_deploy_bundle("owner/empty-project")


def test_geojson_export_handles_nested_properties_and_skips_invalid_coordinates():
    frame = pd.DataFrame(
        [
            {"stop_id": "valid", "stop_lat": 27.9, "stop_lon": -82.4, "tags": ["a", "b"]},
            {"stop_id": "invalid", "stop_lat": 999, "stop_lon": float("inf"), "tags": []},
        ]
    )

    exported = json.loads(dataframe_to_geojson(frame))

    assert len(exported["features"]) == 1
    assert exported["features"][0]["properties"]["tags"] == ["a", "b"]


def test_spreadsheet_csv_export_neutralizes_formula_strings():
    exported = published_app.dataframe_to_safe_csv(
        pd.DataFrame([{"name": "=1+1", "note": "@SUM(A1:A2)", "number": -2}])
    ).decode("utf-8")

    assert "'=1+1" in exported
    assert "'@SUM(A1:A2)" in exported
    assert ",-2" in exported


def test_deployment_bundle_rejects_data_quality_failures(project, visualization):
    builder_app.st.session_state.clear()
    builder_app.st.session_state["active_project_id"] = "invalid-project"
    builder_app.st.session_state["project"] = project
    builder_app.st.session_state["taxonomy"] = []
    builder_app.st.session_state["methodology"] = {}
    builder_app.st.session_state["visualization"] = visualization
    builder_app.st.session_state["stops"] = pd.DataFrame(
        [
            {
                "stop_id": "bad-geometry",
                "stop_name": "Outside coordinate bounds",
                "stop_lat": 95,
                "stop_lon": -82.4,
            }
        ]
    )
    builder_app.st.session_state["import_log"] = []

    with pytest.raises(ValueError, match="Data Quality found 1 publication-blocking"):
        build_github_deploy_bundle("owner/invalid-project")


def test_deployment_blocks_a_stale_browser_session(
    db_path,
    project,
    taxonomy,
    methodology,
    visualization,
    minimal_stops,
    monkeypatch,
):
    monkeypatch.setenv("SHADE_GIS_DB_PATH", str(db_path))
    project_id = create_project(project, taxonomy, methodology, visualization, minimal_stops, [])
    builder_app.st.session_state.clear()
    builder_app.load_project_into_session(project_id)

    assert deployment_session_freshness_issue() == ""

    changed_project = {**project, "description": "Changed in another browser tab"}
    save_project_bundle(
        project_id,
        changed_project,
        taxonomy,
        methodology,
        visualization,
        minimal_stops,
        [],
    )

    assert "not using the latest saved project state" in deployment_session_freshness_issue()


def test_export_file_catalog_describes_files_and_metadata(minimal_stops):
    raw_labels = pd.DataFrame(
        [
            {
                "stop_id": "1001",
                "shade_category": "No Shade",
                "created_at": "2026-07-09T14:30:00-04:00",
            }
        ]
    )
    config = {"project": {"name": "Test"}, "import_log": []}
    import_log = [
        {
            "source": "Test GTFS",
            "format": "GTFS",
            "rows": 2,
            "imported_at": "2026-07-08T10:15:00-04:00",
        }
    ]

    catalog = published_app.export_file_catalog(minimal_stops, raw_labels, config, import_log)

    assert [item["name"] for item in catalog] == [
        "Stops CSV",
        "Stops GeoJSON",
        "Raw Labels CSV",
        "Coding Dimensions Codebook",
        "Study Configuration",
    ]
    assert [item["records"] for item in catalog] == [2, 2, 1, 0, 1]
    assert all(item["description"] for item in catalog)
    assert all(item["size"].endswith(("B", "KB", "MB", "GB")) for item in catalog)
    assert catalog[0]["updated"] == "2026-07-08 10:15"
    assert catalog[2]["updated"] == "2026-07-09 14:30"
    assert json.loads(catalog[1]["data"])["type"] == "FeatureCollection"
    assert json.loads(catalog[3]["data"])["dimensions"] == []


def test_raw_label_export_remains_visible_but_disabled_without_labels(minimal_stops):
    catalog = published_app.export_file_catalog(
        minimal_stops,
        pd.DataFrame(),
        {"import_log": []},
    )
    raw_labels = next(item for item in catalog if item["name"] == "Raw Labels CSV")

    assert raw_labels["records"] == 0
    assert raw_labels["available"] is False
    assert raw_labels["updated"] == "No labels"


def test_deploy_script_supports_existing_private_repositories():
    script = deploy_script("owner/private-shade-study")

    assert '[Alias("TargetRepo")]' in script
    assert '[Alias("Repo")]' not in script
    assert '[ValidateSet("create", "existing")]' in script
    assert '$Mode = "create"' in script
    assert "$AllowPublicTarget" in script
    assert 'function Invoke-Native' in script
    assert 'function Invoke-NativeOutput' in script
    assert "Could not access GitHub repository '$repoSlug'" in script
    assert 'Invoke-NativeOutput "gh" @("repo", "view", $repoSlug' in script
    assert '$env:TEMP' in script
    assert '[guid]::NewGuid()' in script
    assert 'Invoke-Native "gh" @("repo", "clone", $RepositoryName, $publishDir)' in script
    assert 'Invoke-Native "git" @("clone", $remoteUrl, $publishDir)' in script
    assert "Clone command completed but publish directory was not created" in script
    assert 'Copy-SafeBundleFiles -Destination $publishDir' in script
    assert '$rootDataItems = @(' in script
    assert 'Write-Host "Updating generated root data file: $item"' in script
    assert '$rootRawLabels = Join-Path $Destination "shade_study_raw_labels.csv"' in script
    assert "function Test-LegacyRootPublishedApp" in script
    assert '$refreshLegacyRootRuntime = Test-LegacyRootPublishedApp' in script
    assert 'Write-Host "Updated active legacy root runtime: $item"' in script
    assert '$previewDirectory = Join-Path $Destination "preview_app"' in script
    assert '$destinationPath = Join-Path $previewDirectory $item' in script
    assert '$existingPublishFiles = @(\n        "preview_app",' in script
    assert '        "shade_study_stops.csv",' in script
    assert '        "shade_study_config.json"' in script
    assert 'function Test-StreamlitStaticServing' in script
    assert '".streamlit/config.toml"' in script
    assert 'README.md' in script
    assert '.env.*' in script
    assert '"public_voting.py"' in script
    assert 'Invoke-Native "git" @("status")' in script
    assert 'Invoke-Native "git" @("diff", "--stat")' in script
    assert 'Invoke-Native "git" @("diff", "--cached", "--stat")' in script
    assert 'Read-Host "$Message Type PUBLISH to continue"' in script
    assert 'Commit-And-Push -TargetBranch $Branch' in script
    assert 'Remove-Item -LiteralPath $publishDir -Recurse -Force' in script


def test_deploy_script_stages_changes_and_only_reports_success_after_push():
    script = deploy_script("owner/private-shade-study", "Publish July field review")

    stage_files = script.index('$existingPaths = @($Paths | Where-Object { Test-Path $_ })')
    stage_command = script.index('& git add -- $existingPaths')
    status_after_staging = script.index('Write-Host "Repository status after staging:"')
    staged_summary = script.index('Write-Host "Staged diff summary:"')
    diff_check = script.index('& git diff --cached --quiet')
    commit = script.index('Invoke-Native "git" @("commit", "-m", $CommitMessage)')
    push = script.index('Invoke-Native "git" @("push", "origin", $TargetBranch)')
    status_after_publish = script.index('Write-Host "Repository status after commit/push:"')
    conditional_success = script.index('if ($publishedChanges)')
    success_message = script.index('Write-Host "Published changes to $RepositoryName on branch $Branch."')

    assert stage_files < stage_command < status_after_staging < staged_summary < diff_check < commit < push
    assert push < status_after_publish < conditional_success < success_message
    assert '$diffExitCode = $LASTEXITCODE' in script
    assert 'if ($diffExitCode -eq 0)' in script
    assert 'if ($diffExitCode -ne 1)' in script
    assert 'throw "Failed to stage generated deployment files."' in script
    assert '& $Command @Arguments | Out-Host' in script
    assert 'return $false' in script
    assert 'return $true' in script
    assert '$publishedChanges = Commit-And-Push' in script
    assert 'Existing repository already matches the generated deployment; nothing was pushed.' in script
    assert 'Invoke-Native "git" @("status", "--short", "--branch")' in script
    assert "function Assert-DeploymentBundle" in script
    assert "[string]$CommitMessage = 'Publish July field review'" in script
    assert "[string]$manifest.commit_message -ne $CommitMessage" in script
    assert 'Get-FileHash -LiteralPath $relativePath -Algorithm SHA256' in script
    assert 'throw "This bundle targets' in script
    assert "Assert-DeploymentBundle" in script
    assert '"deployment_manifest.json"' in script
    assert 'Write-Host "Published to existing repository $RepositoryName on branch $Branch."' not in script
    assert '$createdCommit = Commit-And-Push -TargetBranch $Branch -Paths $newRepoFiles -SkipPush' in script
    assert 'No generated deployment changes were staged for the new repository.' in script


def test_deploy_script_validates_visibility_only_for_create_mode():
    script = deploy_script("owner/private-shade-study")

    assert '[ValidateSet("public", "private")]' not in script
    assert '[string]$Visibility = "private"' in script

    existing_mode = script.index('if ($Mode -eq "existing")')
    existing_exit = script.index("    exit 0", existing_mode)
    visibility_validation = script.index('if ($Visibility -notin @("public", "private"))')
    public_creation_guard = script.index('if ($Visibility -eq "public" -and -not $AllowPublicTarget)')
    create_repository = script.index('Invoke-Native "gh" @("repo", "create"')

    assert existing_mode < existing_exit < visibility_validation < public_creation_guard < create_repository
    assert "Visibility must be 'public' or 'private' when creating a repository." in script
    assert '"--$Visibility"' in script


def test_deploy_launcher_is_one_guarded_block_with_bundle_discovery():
    script = deploy_launcher_script(
        "private-shade-study.zip",
        "owner/private-shade-study",
        branch="release",
        deploy_mode="existing",
        commit_message="Publish July field review",
    )

    assert script.startswith("& {")
    assert 'Set-StrictMode -Version Latest' in script
    assert '$ErrorActionPreference = "Stop"' in script
    assert "$BundleName = 'private-shade-study.zip'" in script
    assert '$BundlePath = ""' in script
    assert "$RepositoryName = 'owner/private-shade-study'" in script
    assert "$Branch = 'release'" in script
    assert "$CommitMessage = 'Publish July field review'" in script
    assert "[string]::IsNullOrWhiteSpace($RepositoryName)" in script
    assert '$DocumentsDirectory = [Environment]::GetFolderPath("MyDocuments")' in script
    assert 'if ($env:OneDrive) { Join-Path $env:OneDrive "Downloads" }' in script
    assert '(Get-Location).Path' in script
    assert 'if (-not [string]::IsNullOrWhiteSpace($BundlePath))' in script
    assert 'Get-Item -LiteralPath $ExpandedBundlePath' in script
    assert 'Get-ChildItem -LiteralPath $_ -Filter "$BundleStem*.zip" -File' in script
    assert '$BundleNamePattern = "^" + [Regex]::Escape($BundleStem) + "( \\([0-9]+\\))?\\.zip$"' in script
    assert 'Where-Object { $_.Name -match $BundleNamePattern }' in script
    assert 'Sort-Object LastWriteTime -Descending' in script
    assert 'Could not find \'$BundleName\' or a numbered browser copy' in script
    assert 'Write-Host "Using newest deployment bundle: $($ZipCandidate.Name)"' in script
    assert '$ZipPath = Join-Path $DownloadsDirectory $BundleName' not in script
    assert 'Available shade ZIP files: $Available' in script
    assert "Click 'Download website package' first." in script
    assert 'Searched: $Searched.' in script
    assert 'set `$BundlePath at the top of this block' in script
    assert 'Get-ChildItem -LiteralPath $ExtractTo -Filter "deploy_to_github.ps1" -File -Recurse' in script
    assert 'Push-Location -LiteralPath $DeployScript.Directory.FullName' in script
    assert '\n        gh repo view $RepositoryName --json nameWithOwner' in script
    assert '\n        & $DeployScript.FullName `' in script
    assert '-Mode existing `' in script
    assert '-CommitMessage $CommitMessage' in script


def test_deploy_launcher_preserves_public_create_safeguard():
    script = deploy_launcher_script(
        "new-study.zip",
        "new-study",
        deploy_mode="create",
        visibility="public",
    )

    assert 'gh repo view $RepositoryName' not in script
    assert '-Mode create `' in script
    assert '-Visibility $Visibility -AllowPublicTarget' in script


def test_streamlit_entrypoint_separates_builder_repo_from_public_preview():
    assert streamlit_entrypoint_path("existing") == "preview_app/app.py"
    assert streamlit_entrypoint_path("create") == "app.py"


def test_deploy_page_requires_destination_settings_before_publishing():
    source = Path("shade_gis/pages/deploy_page.py").read_text(encoding="utf-8")

    assert "max-width: 900px" in source
    assert 'STAGES = ("Check project", "Prepare website", "Publish", "Verify website")' in source
    assert '"Publish website", type="primary", width="stretch"' in source
    assert "This usually takes 1–3 minutes." in source
    assert 'st.expander("Settings", expanded=expanded)' in source
    assert '"GitHub username"' in source
    assert 'key="deploy_github_username"' in source
    assert 'placeholder="github-user"' in source
    assert '"Destination repository"' in source
    assert 'key="deploy_destination_repository"' in source
    assert 'placeholder="shade-study-site"' in source
    assert "These settings save automatically with this project." in source
    assert '"Commit message"' in source
    assert 'key="deploy_commit_message"' in source
    assert 'saved.get("github_username", "")' in source
    assert 'saved.get("destination_repository", "")' in source
    assert "_remember_target_settings(project, target)" in source
    assert 'repository = f"{username}/{destination}" if username and destination else ""' in source
    assert '"Repository address"' not in source
    assert "Visibility can only be selected when Stop-GIS creates a new repository." in source
    assert "change its visibility in the repository's GitHub settings." in source
    assert 'render_stages("Verify website", set(STAGES[:3]))' not in source
    assert "\n        render_stages()\n" not in source
    assert 'action_columns[1].button("Skip for now", width="stretch")' in source
    assert 'result.verification_skipped = True' in source
    assert 'status_label = "Repository published" if result.changed else "Repository already up to date"' in source
    assert "Website hosting and verification were skipped." in source
    assert "deployment_session_freshness_issue()" in source
    assert 'action_label="Reload saved project"' in source
    assert "deployment_bundle_manifest(bundle_data)" in source
    assert "manifest['bundle_id'][:12]" in source
    assert '"Repository already up to date"' in source
    assert 'label = "Error details" if failed else "View technical details"' in source
    assert 'with st.expander(label, expanded=failed)' in source
    assert 'st.markdown("##### Deployment error details")' in source
    assert "_render_publish_error(updated_result)" in source
    assert "_render_publish_error(result)" in source
    assert '"Open website"' in source
    assert "Copy link" in source
    assert '"Publish update"' in source
    assert '"Unpublish"' in source
    assert "Manual fallback" in source
    assert '"Download website package"' in source
    assert source.count("st.code(") == 3
    assert "deploy_launcher_script(" in source
    assert 'st.radio("Publish mode"' not in source
    assert '"Download deployment bundle"' not in source


def test_deploy_readme_documents_existing_private_repo_flow(project):
    readme = deploy_readme(
        "owner/private-shade-study",
        project,
        commit_message="Publish July field review",
    )

    assert "After Downloading The Zip" in readme
    assert "By default, your browser should save the content-addressed bundle" in readme
    assert "$BundleName = 'private-shade-study.zip'" in readme
    assert '$BundlePath = ""' in readme
    assert '$BundleNamePattern = "^" + [Regex]::Escape($BundleStem)' in readme
    assert 'Where-Object { $_.Name -match $BundleNamePattern }' in readme
    assert 'Using newest deployment bundle' in readme
    assert "numbered browser copy" in readme
    assert "[string]::IsNullOrWhiteSpace($RepositoryName)" in readme
    assert 'Filter "$BundleStem*.zip"' in readme
    assert "Expand-Archive -LiteralPath $ZipPath -DestinationPath $ExtractTo -Force" in readme
    assert 'Filter "deploy_to_github.ps1" -File -Recurse' in readme
    assert "not found anywhere inside it" in readme
    assert "gh auth status" in readme
    assert 'gh repo view $RepositoryName --json nameWithOwner' in readme
    assert "$CommitMessage = 'Publish July field review'" in readme
    assert "-CommitMessage 'Publish July field review'" in readme
    assert "Could not resolve to a Repository" in readme
    assert '-Mode create -RepositoryName "owner/private-shade-study"' in readme
    assert '-Mode existing -RepositoryName "owner/private-shade-study" -Branch "main"' in readme
    assert "-Repo " not in readme
    assert "pre-existing private repository" in readme
    assert "_shade_gis_publish_*" in readme
    assert "git diff --stat" in readme
    assert "PUBLISH" in readme
    assert "-AllowPublicTarget" in readme
    assert ".env*" in readme
    assert "STOP_GIS_VOTE_DATABASE_URL" in readme
    assert "Local SQLite remains suitable only for local evaluation" in readme
    assert "only the public preview" in readme
    assert "protects a repository-root Stop-GIS builder" in readme
    assert "upgrades that active runtime in place" in readme
    assert "refreshes the generated" in readme
    assert "`shade_study_stops.csv`, `shade_study_raw_labels.csv`, and `shade_study_config.json`" in readme
    assert "main file path: `preview_app/app.py`" in readme


def test_deploy_readme_create_mode_does_not_verify_missing_repo(project):
    readme = deploy_readme("owner/new-shade-study", project, deploy_mode="create")

    assert 'gh repo view "owner/new-shade-study"' not in readme
    assert '-Mode create -RepositoryName "owner/new-shade-study"' in readme
    assert "create the target repository used for this bundle" in readme
    assert "new repository contains only this public preview app" in readme
    assert "main file path: `app.py`" in readme
