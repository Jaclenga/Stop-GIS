from __future__ import annotations

import io
import hashlib
import json
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

import pytest

from shade_gis.deployment import (
    DEFAULT_DEPLOY_COMMIT_MESSAGE,
    CommandResult,
    DeploymentTarget,
    deployment_readiness,
    detect_deployment_target,
    github_repository_slug,
    publish_website,
    repository_root_uses_legacy_published_app,
    unpublish_website,
)


def test_deploy_page_restores_and_remembers_settings_per_project(monkeypatch):
    from shade_gis.pages import deploy_page

    session_state = {"active_project_id": "project-one"}
    monkeypatch.setattr(
        deploy_page.st,
        "session_state",
        session_state,
    )
    project = {
        "visibility": "Private",
        "deployment": {
            "github_username": "saved-owner",
            "destination_repository": "saved-site",
            "branch": "release",
            "commit_message": "Publish saved settings",
            "mode": "existing",
            "visibility": "private",
            "public_url": "https://saved-site.streamlit.app",
        },
    }
    detected = DeploymentTarget(branch="main")

    deploy_page._initialize_target_state(detected, project)

    assert session_state["deploy_github_username"] == "saved-owner"
    assert session_state["deploy_destination_repository"] == "saved-site"
    assert session_state["deploy_branch"] == "release"
    target = DeploymentTarget(
        repository="saved-owner/saved-site",
        branch="release",
        mode="existing",
        visibility="private",
        public_url="https://saved-site.streamlit.app",
        commit_message="Publish saved settings",
    )
    deploy_page._remember_target_settings(project, target)
    assert project["deployment"]["repository"] == "saved-owner/saved-site"

    session_state["active_project_id"] = "project-two"
    deploy_page._initialize_target_state(detected, {"visibility": "Public"})
    assert session_state["deploy_github_username"] == ""
    assert session_state["deploy_destination_repository"] == ""
    assert session_state["deploy_branch"] == "main"
    assert session_state["deploy_visibility"] == "public"


@pytest.fixture
def deployment_tmp():
    directory = Path(".pytest-shade-deployment") / uuid.uuid4().hex
    directory.mkdir(parents=True)
    try:
        yield directory.resolve()
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _bundle_bytes(commit_message: str = DEFAULT_DEPLOY_COMMIT_MESSAGE) -> bytes:
    files = {
        "app.py": b"print('published')\n",
        "public_voting.py": b"VOTING = True\n",
        "shade_study_stops.csv": b"stop_id\n1001\n",
        "shade_study_config.json": b"{}",
        "requirements.txt": b"streamlit\n",
    }
    manifest_core = {
        "schema_version": 1,
        "study_id": "test-study",
        "project_name": "Test study",
        "repository": "owner/study",
        "deploy_mode": "existing",
        "commit_message": commit_message,
        "entrypoint": "preview_app/app.py",
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    }
    manifest_core["bundle_id"] = hashlib.sha256(
        json.dumps(manifest_core, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
        bundle.writestr("deployment_manifest.json", json.dumps(manifest_core))
    return output.getvalue()


def test_default_deploy_commit_message_is_generic():
    assert DEFAULT_DEPLOY_COMMIT_MESSAGE == "Publish website update"


def test_github_repository_slug_supports_detected_remote_formats():
    assert github_repository_slug("https://github.com/owner/study.git") == "owner/study"
    assert github_repository_slug("git@github.com:owner/study.git") == "owner/study"
    assert github_repository_slug("owner/study") == "owner/study"
    assert github_repository_slug("https://example.com/owner/study") == ""


def test_legacy_root_runtime_detection_protects_builder_entrypoint(deployment_tmp):
    legacy_root = deployment_tmp / "legacy"
    legacy_root.mkdir()
    (legacy_root / "app.py").write_text(
        'CONFIG_PATH = APP_DIR / "shade_study_config.json"\n'
        'DATA_PATH = APP_DIR / "shade_study_stops.csv"\n',
        encoding="utf-8",
    )
    builder_root = deployment_tmp / "builder"
    builder_root.mkdir()
    (builder_root / "app.py").write_text(
        "from builder_app import main\n\nmain()\n",
        encoding="utf-8",
    )

    assert repository_root_uses_legacy_published_app(legacy_root) is True
    assert repository_root_uses_legacy_published_app(builder_root) is False


def test_publish_rejects_bundle_for_another_repository_before_git_runs():
    result = publish_website(
        _bundle_bytes(),
        DeploymentTarget(repository="owner/other", repository_url="https://github.com/owner/other"),
    )

    assert result.success is False
    assert "targets owner/study, not owner/other" in result.message


def test_publish_rejects_tampered_bundle_before_git_runs():
    original = _bundle_bytes()
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(output, "w") as changed:
        for name in source.namelist():
            content = source.read(name)
            changed.writestr(name, b"tampered\n" if name == "app.py" else content)

    result = publish_website(
        output.getvalue(),
        DeploymentTarget(repository="owner/study", repository_url="https://github.com/owner/study"),
    )

    assert result.success is False
    assert "app.py does not match its manifest hash" in result.message


def test_publish_rejects_bundle_with_a_different_commit_message():
    result = publish_website(
        _bundle_bytes(),
        DeploymentTarget(
            repository="owner/study",
            repository_url="https://github.com/owner/study",
            commit_message="Publish July field review",
        ),
    )

    assert result.success is False
    assert "different commit message" in result.message


def test_detect_deployment_target_uses_origin_and_remote_default_branch(deployment_tmp):
    tmp_path = deployment_tmp
    responses = {
        ("git", "rev-parse", "--show-toplevel"): str(tmp_path),
        ("git", "config", "--get", "remote.origin.url"): "https://github.com/owner/study.git",
        ("git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"): "origin/release",
    }

    def fake_runner(args, _cwd, _timeout):
        value = responses.get(tuple(args), "")
        return CommandResult(0 if value else 1, stdout=value)

    target = detect_deployment_target(tmp_path, runner=fake_runner)

    assert target.repository == "owner/study"
    assert target.repository_url == "https://github.com/owner/study.git"
    assert target.branch == "release"
    assert target.detected is True


def test_readiness_returns_one_outcome_level_blocker():
    target = DeploymentTarget()

    result = deployment_readiness(stops_empty=True, target=target, bundle_error="low-level build error")

    assert result.ready is False
    assert result.title == "Add project data before publishing"
    assert result.action_label == "Open Data"
    assert "low-level" not in result.message


def test_readiness_sends_quality_failures_to_the_dashboard():
    target = DeploymentTarget(
        repository="owner/study",
        repository_url="https://github.com/owner/study.git",
    )

    result = deployment_readiness(
        stops_empty=False,
        target=target,
        data_quality_issues=3,
    )

    assert result.ready is False
    assert result.title == "Resolve data quality issues before publishing"
    assert result.message.startswith("Data Quality found 3 publication-blocking")
    assert result.action_label == "Open Data Quality"
    assert result.action == "data_quality"


def test_publish_and_unpublish_existing_repository_automatically(deployment_tmp, monkeypatch):
    target = DeploymentTarget(
        repository="owner/study",
        repository_url="https://github.com/owner/study.git",
        branch="main",
        mode="existing",
        commit_message="Publish July field review",
    )
    stages: list[str] = []
    observed: dict[str, object] = {"published": False, "pushes": 0, "statuses": 0}
    temporary_counter = iter(range(10))

    class WorkspaceTemporaryDirectory:
        def __init__(self, prefix="tmp"):
            self.path = deployment_tmp / f"{prefix}{next(temporary_counter)}"

        def __enter__(self):
            self.path.mkdir()
            return str(self.path)

        def __exit__(self, _exc_type, _exc, _traceback):
            shutil.rmtree(self.path, ignore_errors=True)

    monkeypatch.setattr(tempfile, "TemporaryDirectory", WorkspaceTemporaryDirectory)

    def fake_runner(args, cwd, _timeout):
        command = tuple(args)
        if command[:2] == ("git", "clone"):
            worktree = Path(args[-1])
            worktree.mkdir(parents=True)
            (worktree / "README.md").write_text("existing repository\n", encoding="utf-8")
            (worktree / "app.py").write_text(
                'CONFIG_PATH = APP_DIR / "shade_study_config.json"\n'
                'DATA_PATH = APP_DIR / "shade_study_stops.csv"\n'
                "render_metric_cards(visible_stops)\n",
                encoding="utf-8",
            )
            (worktree / "public_voting.py").write_text("OLD_VOTING = True\n", encoding="utf-8")
            (worktree / "requirements.txt").write_text("streamlit<1\n", encoding="utf-8")
            (worktree / "shade_study_stops.csv").write_text("stop_id\nold\n", encoding="utf-8")
            if observed["published"]:
                preview = worktree / "preview_app"
                preview.mkdir()
                (preview / "app.py").write_text("print('published')\n", encoding="utf-8")
            return CommandResult(0)
        if command[:3] == ("git", "diff", "--cached"):
            return CommandResult(1)
        if command == ("git", "config", "user.name"):
            return CommandResult(0, stdout="Shade-GIS Test")
        if command == ("git", "config", "user.email"):
            return CommandResult(0, stdout="shade-gis-test@example.com")
        if command[:2] == ("git", "add"):
            observed["app"] = (Path(cwd) / "preview_app" / "app.py").read_text(encoding="utf-8")
            observed["data"] = (Path(cwd) / "preview_app" / "shade_study_stops.csv").read_text(
                encoding="utf-8"
            )
            observed["root_data"] = (Path(cwd) / "shade_study_stops.csv").read_text(encoding="utf-8")
            observed["root_config"] = (Path(cwd) / "shade_study_config.json").read_text(encoding="utf-8")
            observed["root_app"] = (Path(cwd) / "app.py").read_text(encoding="utf-8")
            observed["root_voting"] = (Path(cwd) / "public_voting.py").read_text(encoding="utf-8")
            observed["root_requirements"] = (Path(cwd) / "requirements.txt").read_text(encoding="utf-8")
            observed["readme"] = (Path(cwd) / "README.md").read_text(encoding="utf-8")
            observed["manifest"] = (Path(cwd) / "preview_app" / "deployment_manifest.json").exists()
            return CommandResult(0)
        if command[:2] == ("git", "rm"):
            observed["removed"] = list(args[5:])
            return CommandResult(0)
        if command[:2] == ("git", "push"):
            observed["published"] = not bool(observed["published"])
            observed["pushes"] = int(observed["pushes"]) + 1
            return CommandResult(0)
        if command[:2] == ("git", "commit"):
            observed["commit_message"] = args[3]
            return CommandResult(0)
        if command == ("git", "status", "--short", "--branch"):
            observed["statuses"] = int(observed["statuses"]) + 1
            return CommandResult(0, stdout="## main...origin/main")
        if command == ("git", "rev-parse", "HEAD"):
            return CommandResult(0, stdout="abc123")
        return CommandResult(0)

    result = publish_website(
        _bundle_bytes(target.commit_message),
        target,
        progress=lambda stage, _message: stages.append(stage),
        runner=fake_runner,
        verify_attempts=1,
        verify_interval=0,
    )

    assert result.success is True
    assert result.changed is True
    assert result.needs_host_setup is True
    assert result.verification_skipped is False
    assert stages == ["Check project", "Prepare website", "Publish", "Verify website"]
    assert observed["app"] == "print('published')\n"
    assert observed["data"] == "stop_id\n1001\n"
    assert observed["root_data"] == "stop_id\n1001\n"
    assert observed["root_config"] == "{}"
    assert observed["root_app"] == "print('published')\n"
    assert observed["root_voting"] == "VOTING = True\n"
    assert observed["root_requirements"] == "streamlit\n"
    assert observed["readme"] == "existing repository\n"
    assert observed["manifest"] is True
    assert observed["commit_message"] == "Publish July field review"
    assert observed["statuses"] == 2
    assert any("refreshed its active runtime" in log for log in result.logs)

    unpublished = unpublish_website(target, runner=fake_runner)

    assert unpublished.success is True
    assert unpublished.changed is True
    assert observed["removed"] == ["preview_app"]
    assert observed["pushes"] == 2
