from __future__ import annotations

import io
import hashlib
import json
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

import pandas as pd
import pytest

import shade_gis.deployment as deployment_module
from shade_gis.deploy.bundle import DeploymentBundleSpec, build_deployment_bundle

from shade_gis.deployment import (
    CREATED_REPOSITORY_FILES,
    DEFAULT_DEPLOY_COMMIT_MESSAGE,
    CommandResult,
    DeploymentTarget,
    PublishResult,
    deployment_readiness,
    detect_deployment_target,
    github_repository_slug,
    publish_website,
    manifest_owned_deployment_paths,
    repository_root_uses_legacy_published_app,
    unpublish_website,
    validate_deployment_bundle,
    verify_website,
)


def test_release_identity_changes_when_only_configuration_changes():
    from shade_gis.pages import deploy_page

    base = dict(
        repository="owner/study",
        project={"name": "Study"},
        study_id="study-id",
        stops=pd.DataFrame([{"stop_id": "1001"}]),
        raw_labels=pd.DataFrame(),
        priority_weights={},
    )
    first = build_deployment_bundle(DeploymentBundleSpec(config_json="{}", **base))
    second = build_deployment_bundle(
        DeploymentBundleSpec(config_json='{"voting": true}', **base)
    )
    with zipfile.ZipFile(io.BytesIO(first)) as first_zip, zipfile.ZipFile(
        io.BytesIO(second)
    ) as second_zip:
        first_identity = json.loads(first_zip.read("static/shade_gis_identity.json"))
        second_identity = json.loads(second_zip.read("static/shade_gis_identity.json"))

    assert first_identity["release_sha256"] != second_identity["release_sha256"]
    assert deploy_page.website_identity_markers(first)[-1] == first_identity["release_sha256"]


def test_stored_deployment_result_is_bound_to_bundle(monkeypatch):
    from shade_gis.pages import deploy_page

    monkeypatch.setattr(deploy_page.st, "session_state", {})
    target = DeploymentTarget(repository="owner/study", mode="existing")
    first = _bundle_bytes()
    with zipfile.ZipFile(io.BytesIO(first)) as source:
        files = {name: source.read(name) for name in source.namelist()}
    manifest = json.loads(files["deployment_manifest.json"])
    manifest["bundle_id"] = "different-bundle"
    files["deployment_manifest.json"] = json.dumps(manifest).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as changed:
        for name, content in files.items():
            changed.writestr(name, content)
    result = PublishResult(True, verified=True)

    deploy_page._store_result(target, result, first)

    assert deploy_page._stored_result(target, first) is result
    assert deploy_page._stored_result(target, output.getvalue()) is None


def test_create_publish_rejects_invalid_visibility_before_running_commands():
    def unexpected_runner(*_args, **_kwargs):
        raise AssertionError("runner should not be called")

    result = publish_website(
        _bundle_bytes(),
        DeploymentTarget(repository="owner/study", mode="create", visibility="help"),
        runner=unexpected_runner,
    )

    assert result.success is False
    assert "visibility must be 'public' or 'private'" in result.message.lower()


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
        name: f"placeholder for {name}\n".encode()
        for name in CREATED_REPOSITORY_FILES
        if name not in {"deployment_manifest.json", "shade_study_raw_labels.csv"}
    }
    files.update({
        "app.py": b"print('published')\n",
        "public_voting.py": b"VOTING = True\n",
        "shade_study_stops.csv": b"stop_id\n1001\n",
        "shade_study_config.json": b"{}",
        "requirements.txt": b"streamlit\n",
        ".streamlit/config.toml": b"[server]\nenableStaticServing = true\n",
    })
    files["static/shade_gis_identity.json"] = json.dumps(
        {
            "schema_version": 1,
            "study_id": "test-study",
            "repository": "owner/study",
            "dataset_sha256": hashlib.sha256(files["shade_study_stops.csv"]).hexdigest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    identity_file_hashes = {
        name: hashlib.sha256(content).hexdigest()
        for name, content in files.items()
        if name != "README.md"
    }
    manifest_core = {
        "schema_version": 1,
        "study_id": "test-study",
        "project_name": "Test study",
        "repository": "owner/study",
        "deploy_mode": "existing",
        "commit_message": commit_message,
        "entrypoint": "preview_app/app.py",
        "dataset": {
            "file": "shade_study_stops.csv",
            "rows": 1,
            "columns": ["stop_id"],
            "sha256": identity_file_hashes["shade_study_stops.csv"],
        },
        "files": identity_file_hashes,
    }
    identity_json = json.dumps(manifest_core, sort_keys=True, separators=(",", ":"))
    manifest = {
        **manifest_core,
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
        "identity_json": identity_json,
        "bundle_id": hashlib.sha256(identity_json.encode("utf-8")).hexdigest(),
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
        bundle.writestr("deployment_manifest.json", json.dumps(manifest))
    return output.getvalue()


def test_default_deploy_commit_message_is_generic():
    assert DEFAULT_DEPLOY_COMMIT_MESSAGE == "Publish website update"


def test_bundle_builder_canonicalizes_a_github_repository_url():
    bundle_data = build_deployment_bundle(
        DeploymentBundleSpec(
            repository="https://github.com/owner/study.git",
            project={"name": "Study"},
            study_id="study-id",
            stops=pd.DataFrame([{"stop_id": "1001"}]),
            raw_labels=pd.DataFrame(),
            config_json="{}",
            priority_weights={},
        )
    )

    with zipfile.ZipFile(io.BytesIO(bundle_data)) as bundle:
        manifest = json.loads(bundle.read("deployment_manifest.json"))
        publisher = bundle.read("deploy_to_github.ps1").decode("utf-8")

    assert manifest["repository"] == "owner/study"
    assert "$RepositoryName = 'owner/study'" in publisher


def test_generated_bundle_validates_against_its_own_release_identity():
    bundle_data = build_deployment_bundle(
        DeploymentBundleSpec(
            repository="owner/study",
            project={"name": "Study"},
            study_id="study-id",
            stops=pd.DataFrame([{"stop_id": "1001"}]),
            raw_labels=pd.DataFrame(),
            config_json="{}",
            priority_weights={},
        )
    )

    manifest = validate_deployment_bundle(
        bundle_data,
        DeploymentTarget(repository="owner/study", mode="existing"),
    )

    assert manifest["repository"] == "owner/study"
    assert len(manifest["release_sha256"]) == 64


def test_github_repository_slug_supports_detected_remote_formats():
    assert github_repository_slug("https://github.com/owner/study.git") == "owner/study"
    assert github_repository_slug("git@github.com:owner/study.git") == "owner/study"
    assert github_repository_slug("owner/study") == "owner/study"
    assert github_repository_slug("https://example.com/owner/study") == ""
    assert github_repository_slug("https://evilgithub.com/owner/study") == ""


def test_bundle_validation_rejects_unhashed_app_and_forged_identity():
    original = _bundle_bytes()
    with zipfile.ZipFile(io.BytesIO(original)) as source:
        contents = {name: source.read(name) for name in source.namelist()}

    manifest = json.loads(contents["deployment_manifest.json"])
    manifest["files"].pop("app.py")
    contents["deployment_manifest.json"] = json.dumps(manifest).encode()
    unhashed = io.BytesIO()
    with zipfile.ZipFile(unhashed, "w") as changed:
        for name, content in contents.items():
            changed.writestr(name, content)
    with pytest.raises(RuntimeError, match="unhashed: app.py"):
        validate_deployment_bundle(
            unhashed.getvalue(), DeploymentTarget(repository="owner/study", mode="existing")
        )

    with zipfile.ZipFile(io.BytesIO(original)) as source:
        forged_contents = {name: source.read(name) for name in source.namelist()}
    forged_manifest = json.loads(forged_contents["deployment_manifest.json"])
    forged_manifest["files"]["app.py"] = hashlib.sha256(forged_contents["app.py"]).hexdigest()
    forged_manifest["bundle_id"] = "0" * 64
    forged_contents["deployment_manifest.json"] = json.dumps(forged_manifest).encode()
    forged = io.BytesIO()
    with zipfile.ZipFile(forged, "w") as changed:
        for name, content in forged_contents.items():
            changed.writestr(name, content)
    with pytest.raises(RuntimeError, match="identity does not match"):
        validate_deployment_bundle(
            forged.getvalue(), DeploymentTarget(repository="owner/study", mode="existing")
        )


def test_bundle_validation_requires_the_complete_generated_file_set():
    with zipfile.ZipFile(io.BytesIO(_bundle_bytes())) as source:
        contents = {name: source.read(name) for name in source.namelist()}
    contents.pop("DEPLOYMENT.md")
    manifest = json.loads(contents["deployment_manifest.json"])
    manifest["files"].pop("DEPLOYMENT.md")
    identity = {
        key: value
        for key, value in manifest.items()
        if key not in {"bundle_id", "deployed_paths", "identity_json"}
    }
    identity["files"] = {
        name: digest for name, digest in manifest["files"].items() if name != "README.md"
    }
    manifest["identity_json"] = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    manifest["bundle_id"] = hashlib.sha256(manifest["identity_json"].encode()).hexdigest()
    contents["deployment_manifest.json"] = json.dumps(manifest).encode()
    incomplete = io.BytesIO()
    with zipfile.ZipFile(incomplete, "w") as changed:
        for name, content in contents.items():
            changed.writestr(name, content)

    with pytest.raises(RuntimeError, match="DEPLOYMENT.md"):
        validate_deployment_bundle(
            incomplete.getvalue(), DeploymentTarget(repository="owner/study", mode="existing")
        )


def test_website_verification_rejects_unrelated_success_page(monkeypatch):
    class FakeConnection:
        def close(self):
            pass

    class FakeResponse:
        status = 200
        headers = {}

        def read(self, _limit):
            return b"<html><title>Some other application</title></html>"

        def close(self):
            pass

    monkeypatch.setattr(
        "shade_gis.deployment._validated_web_target",
        lambda url, **_kwargs: (url, ["8.8.8.8"]),
    )
    monkeypatch.setattr(
        "shade_gis.deployment._open_pinned_api_response",
        lambda _url, _addresses: (FakeConnection(), FakeResponse()),
    )

    verified, message = verify_website(
        "https://website.example", attempts=1, interval=0, expected_markers=("study-123",)
    )

    assert verified is False
    assert "did not identify this Shade-GIS study" in message


def test_website_verification_requires_all_exact_static_identity_values(monkeypatch):
    requested_urls = []

    class FakeConnection:
        def close(self):
            pass

    class FakeResponse:
        status = 200
        headers = {}

        def read(self, _limit):
            return json.dumps(
                {
                    "schema_version": 1,
                    "study_id": "study-123",
                    "repository": "owner/study",
                    "dataset_sha256": "abc123",
                }
            ).encode()

        def close(self):
            pass

    monkeypatch.setattr(
        "shade_gis.deployment._validated_web_target",
        lambda url, **_kwargs: (url, ["8.8.8.8"]),
    )

    def fake_open(url, _addresses):
        requested_urls.append(url)
        return FakeConnection(), FakeResponse()

    monkeypatch.setattr("shade_gis.deployment._open_pinned_api_response", fake_open)

    verified, _message = verify_website(
        "https://website.example",
        attempts=1,
        interval=0,
        expected_markers=("study-123", "owner/study", "abc123"),
    )

    assert verified is True
    assert requested_urls == [
        "https://website.example/app/static/shade_gis_identity.json"
    ]


def test_legacy_root_runtime_detection_protects_builder_entrypoint(deployment_tmp):
    legacy_root = deployment_tmp / "legacy"
    legacy_root.mkdir()
    (legacy_root / "app.py").write_text(
        'CONFIG_PATH = APP_DIR / "shade_study_config.json"\n'
        'DATA_PATH = APP_DIR / "shade_study_stops.csv"\n',
        encoding="utf-8",
    )
    legacy_source = (legacy_root / "app.py").read_bytes()
    (legacy_root / "deployment_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "study_id": "legacy-study",
                "repository": "owner/study",
                "deploy_mode": "create",
                "files": {"app.py": hashlib.sha256(legacy_source).hexdigest()},
            }
        ),
        encoding="utf-8",
    )
    builder_root = deployment_tmp / "builder"
    builder_root.mkdir()
    (builder_root / "app.py").write_text(
        "from builder_app import main\n\nmain()\n",
        encoding="utf-8",
    )
    unsigned_root = deployment_tmp / "unsigned"
    unsigned_root.mkdir()
    (unsigned_root / "app.py").write_text(
        'CONFIG_PATH = "shade_study_config.json"\nDATA_PATH = "shade_study_stops.csv"\n',
        encoding="utf-8",
    )

    assert repository_root_uses_legacy_published_app(legacy_root) is True
    assert repository_root_uses_legacy_published_app(builder_root) is False
    assert repository_root_uses_legacy_published_app(unsigned_root) is False


def test_manifest_cannot_claim_arbitrary_repository_files(deployment_tmp):
    preview = deployment_tmp / "preview_app"
    preview.mkdir()
    license_path = deployment_tmp / "LICENSE"
    license_path.write_text("user-owned\n", encoding="utf-8")
    (preview / "deployment_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repository": "owner/study",
                "deploy_mode": "existing",
                "files": {"LICENSE": hashlib.sha256(license_path.read_bytes()).hexdigest()},
                "deployed_paths": ["LICENSE", "preview_app/deployment_manifest.json"],
            }
        ),
        encoding="utf-8",
    )

    owned = manifest_owned_deployment_paths(
        deployment_tmp,
        DeploymentTarget(repository="owner/study", mode="existing"),
    )

    assert owned == []


def test_publish_rejects_bundle_for_another_repository_before_git_runs():
    result = publish_website(
        _bundle_bytes(),
        DeploymentTarget(repository="owner/other", repository_url="https://github.com/owner/other"),
    )

    assert result.success is False
    assert "targets owner/study, not owner/other" in result.message


def test_publish_rejects_a_clone_url_for_another_repository_before_git_runs():
    result = publish_website(
        _bundle_bytes(),
        DeploymentTarget(
            repository="owner/study",
            repository_url="https://github.com/owner/other.git",
            mode="existing",
            allow_public_target=True,
        ),
    )

    assert result.success is False
    assert "clone URL does not match" in result.message

    unpublished = unpublish_website(
        DeploymentTarget(
            repository="owner/study",
            repository_url="https://github.com/owner/other.git",
            mode="existing",
        )
    )
    assert unpublished.success is False
    assert "clone URL does not match" in unpublished.message


def test_publish_and_unpublish_translate_filesystem_failures(monkeypatch):
    def inaccessible_temporary_directory(*_args, **_kwargs):
        raise PermissionError("temporary storage denied")

    monkeypatch.setattr(
        deployment_module,
        "validate_deployment_bundle",
        lambda _bundle, _target: {
            "bundle_id": "0" * 64,
            "repository": "owner/study",
        },
    )
    monkeypatch.setattr(
        deployment_module.tempfile,
        "TemporaryDirectory",
        inaccessible_temporary_directory,
    )

    published = publish_website(
        _bundle_bytes(),
        DeploymentTarget(
            repository="owner/study",
            mode="create",
            visibility="private",
        ),
    )
    unpublished = unpublish_website(
        DeploymentTarget(
            repository="owner/study",
            repository_url="https://github.com/owner/study.git",
            mode="existing",
        )
    )

    assert published.success is False
    assert "Publishing could not access" in published.message
    assert "temporary storage denied" in published.message
    assert unpublished.success is False
    assert "Unpublishing could not access" in unpublished.message
    assert "temporary storage denied" in unpublished.message


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


def test_existing_publish_requires_static_serving_in_user_owned_streamlit_config(
    deployment_tmp, monkeypatch
):
    target = DeploymentTarget(
        repository="owner/study",
        repository_url="https://github.com/owner/study.git",
        mode="existing",
        allow_public_target=True,
    )

    class WorkspaceTemporaryDirectory:
        def __init__(self, prefix="tmp"):
            self.path = deployment_tmp / f"{prefix}config-check"

        def __enter__(self):
            self.path.mkdir()
            return str(self.path)

        def __exit__(self, _exc_type, _exc, _traceback):
            shutil.rmtree(self.path, ignore_errors=True)

    monkeypatch.setattr(tempfile, "TemporaryDirectory", WorkspaceTemporaryDirectory)

    def fake_runner(args, _cwd, _timeout):
        if tuple(args[:2]) == ("git", "clone"):
            worktree = Path(args[-1])
            config_path = worktree / ".streamlit" / "config.toml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text("[server]\nenableStaticServing = false\n", encoding="utf-8")
        return CommandResult(0)

    result = publish_website(_bundle_bytes(), target, runner=fake_runner)

    assert result.success is False
    assert "server.enableStaticServing = true" in result.message


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


def test_readiness_requires_confirmation_for_existing_public_repository():
    target = DeploymentTarget(
        repository="owner/study",
        repository_url="https://github.com/owner/study.git",
        visibility="public",
    )

    blocked = deployment_readiness(stops_empty=False, target=target)
    confirmed = deployment_readiness(
        stops_empty=False,
        target=DeploymentTarget(**{**target.__dict__, "allow_public_target": True}),
    )

    assert blocked.ready is False
    assert "public repository" in blocked.title
    assert confirmed.ready is True


def test_website_verification_rejects_private_targets_and_redirects(monkeypatch):
    monkeypatch.delenv("SHADE_GIS_ALLOW_PRIVATE_WEBSITE_URLS", raising=False)
    monkeypatch.setattr(
        "shade_gis.builder_imports.socket.getaddrinfo",
        lambda host, port, type: [(None, type, None, "", ("127.0.0.1", port))],
    )

    verified, message = verify_website(
        "https://website.example",
        attempts=1,
        interval=0,
        expected_markers=("test-study",),
    )

    assert verified is False
    assert "Private or localhost" in message


def test_publish_and_unpublish_existing_repository_automatically(deployment_tmp, monkeypatch):
    target = DeploymentTarget(
        repository="owner/study",
        repository_url="https://github.com/owner/study.git",
        branch="main",
        mode="existing",
        commit_message="Publish July field review",
        allow_public_target=True,
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
            legacy_app = (
                'CONFIG_PATH = APP_DIR / "shade_study_config.json"\n'
                'DATA_PATH = APP_DIR / "shade_study_stops.csv"\n'
                "render_metric_cards(visible_stops)\n"
            )
            (worktree / "app.py").write_text(legacy_app, encoding="utf-8")
            (worktree / "deployment_manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "study_id": "legacy-study",
                        "repository": "owner/study",
                        "deploy_mode": "create",
                        "files": {
                            "app.py": hashlib.sha256((worktree / "app.py").read_bytes()).hexdigest(),
                        },
                    }
                ),
                encoding="utf-8",
            )
            (worktree / "public_voting.py").write_text("OLD_VOTING = True\n", encoding="utf-8")
            (worktree / "requirements.txt").write_text("streamlit<1\n", encoding="utf-8")
            (worktree / "shade_study_stops.csv").write_text("stop_id\nold\n", encoding="utf-8")
            if observed["published"]:
                with zipfile.ZipFile(io.BytesIO(_bundle_bytes(target.commit_message))) as bundle:
                    preview = worktree / "preview_app"
                    preview.mkdir()
                    for name in bundle.namelist():
                        destination = preview / name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(bundle.read(name))
                    for name in (
                        "app.py",
                        "public_voting.py",
                        "requirements.txt",
                        "shade_study_stops.csv",
                        "shade_study_config.json",
                        "static/shade_gis_identity.json",
                        ".streamlit/config.toml",
                    ):
                        destination = worktree / name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(bundle.read(name))
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
            observed["removed"] = list(args[4:])
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
    assert observed["removed"] == [
        "preview_app/app.py",
        "preview_app/public_voting.py",
        "preview_app/shade_study_stops.csv",
        "preview_app/shade_study_config.json",
        "preview_app/requirements.txt",
        "preview_app/static/shade_gis_identity.json",
        "shade_study_stops.csv",
        "shade_study_config.json",
        "app.py",
        "public_voting.py",
        "requirements.txt",
        "static/shade_gis_identity.json",
        ".streamlit/config.toml",
        "preview_app/deployment_manifest.json",
    ]
    assert observed["pushes"] == 2
