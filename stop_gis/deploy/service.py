from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Callable, Sequence

from stop_gis.builder.imports import _open_pinned_api_response, _validated_web_target


GITHUB_HOST = "github.com"
STREAMLIT_WORKSPACE_URL = "https://share.streamlit.io/"
EXISTING_PREVIEW_DIR = "preview_app"
WEBSITE_IDENTITY_FILE = "static/shade_gis_identity.json"
EXISTING_BUNDLE_FILES = (
    "app.py",
    "public_voting.py",
    "shade_study_stops.csv",
    "shade_study_raw_labels.csv",
    "shade_study_config.json",
    "deployment_manifest.json",
    "requirements.txt",
    WEBSITE_IDENTITY_FILE,
)
EXISTING_ROOT_DATA_FILES = (
    "shade_study_stops.csv",
    "shade_study_raw_labels.csv",
    "shade_study_config.json",
)
EXISTING_ROOT_RUNTIME_FILES = (
    "app.py",
    "public_voting.py",
    "requirements.txt",
    WEBSITE_IDENTITY_FILE,
)
EXISTING_ROOT_CONFIG_FILES = (".streamlit/config.toml",)
CREATED_REPOSITORY_FILES = (
    *EXISTING_BUNDLE_FILES,
    "README.md",
    "deploy_to_github.ps1",
    ".gitignore",
    ".streamlit/config.toml",
    ".streamlit/secrets.toml.example",
    ".env.example",
    "migrations/001_public_voting.sql",
    "migrations/least_privilege_roles.sql.example",
    "scripts/verify_database.py",
    "scripts/migrate_database.py",
    "DEPLOYMENT.md",
)
DEFAULT_DEPLOY_COMMIT_MESSAGE = "Publish website update"


def normalize_deploy_commit_message(value: object) -> str:
    return str(value or "").strip() or DEFAULT_DEPLOY_COMMIT_MESSAGE


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def output(self) -> str:
        return "\n".join(part.strip() for part in (self.stdout, self.stderr) if part.strip())


@dataclass(frozen=True)
class DeploymentTarget:
    repository: str = ""
    repository_url: str = ""
    branch: str = "main"
    root: Path | None = None
    mode: str = "existing"
    visibility: str = "private"
    public_url: str = ""
    commit_message: str = DEFAULT_DEPLOY_COMMIT_MESSAGE
    detected: bool = False
    allow_public_target: bool = False
    visibility_verified: bool = False

    @property
    def entrypoint(self) -> str:
        return "preview_app/app.py" if self.mode == "existing" else "app.py"


@dataclass(frozen=True)
class ReadinessResult:
    ready: bool
    title: str
    message: str
    action_label: str = ""
    action: str = ""


@dataclass
class PublishResult:
    success: bool
    changed: bool = False
    commit: str = ""
    public_url: str = ""
    verified: bool = False
    needs_host_setup: bool = False
    verification_skipped: bool = False
    message: str = ""
    logs: list[str] = field(default_factory=list)


CommandRunner = Callable[[Sequence[str], Path | None, int], CommandResult]
ProgressCallback = Callable[[str, str], None]


def run_command(args: Sequence[str], cwd: Path | None = None, timeout: int = 45) -> CommandResult:
    try:
        completed = subprocess.run(
            list(args),
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(1, stderr=str(exc))
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def github_repository_slug(value: str) -> str:
    candidate = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", candidate):
        return candidate.removesuffix(".git")
    scp_match = re.fullmatch(
        r"(?:[^@/]+@)?github\.com:(?P<owner>[A-Za-z0-9_.-]+)/"
        r"(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?",
        candidate,
        flags=re.IGNORECASE,
    )
    if scp_match:
        return f"{scp_match.group('owner')}/{scp_match.group('repo')}"
    parsed = urllib.parse.urlparse(candidate)
    if parsed.hostname and parsed.hostname.lower() == GITHUB_HOST:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1].removesuffix('.git')}"
    return ""


def github_repository_url(repository: str) -> str:
    slug = github_repository_slug(repository)
    return f"https://github.com/{slug}" if slug else ""


def normalize_public_url(value: str | None) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def _command_value(
    args: Sequence[str],
    cwd: Path | None,
    runner: CommandRunner,
    timeout: int = 8,
) -> str:
    result = runner(args, cwd, timeout)
    return result.stdout.strip() if result.returncode == 0 else ""


def detect_deployment_target(
    start: Path | None = None,
    runner: CommandRunner = run_command,
) -> DeploymentTarget:
    start = (start or Path.cwd()).resolve()
    root_value = _command_value(["git", "rev-parse", "--show-toplevel"], start, runner)
    root = Path(root_value).resolve() if root_value else None
    command_cwd = root or start
    remote = _command_value(["git", "config", "--get", "remote.origin.url"], command_cwd, runner)
    repository = github_repository_slug(remote)
    head_ref = _command_value(
        ["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        command_cwd,
        runner,
    )
    branch = head_ref.removeprefix("origin/") if head_ref else ""
    if not branch:
        branch = _command_value(["git", "branch", "--show-current"], command_cwd, runner)
    if not branch:
        branch = "main"
    public_url = normalize_public_url(
        os.environ.get("STOP_GIS_PUBLIC_URL") or os.environ.get("SHADE_GIS_PUBLIC_URL")
    )
    return DeploymentTarget(
        repository=repository,
        repository_url=remote or github_repository_url(repository),
        branch=branch,
        root=root,
        public_url=public_url,
        detected=bool(repository and root),
    )


def repository_metadata(target: DeploymentTarget, runner: CommandRunner = run_command) -> dict:
    if not target.repository or shutil.which("gh") is None:
        return {}
    result = runner(
        [
            "gh",
            "repo",
            "view",
            target.repository,
            "--json",
            "nameWithOwner,defaultBranchRef,url,homepageUrl,visibility",
        ],
        target.root,
        12,
    )
    if result.returncode != 0:
        return {}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def repository_has_published_app(
    target: DeploymentTarget,
    runner: CommandRunner = run_command,
) -> bool:
    if target.mode != "existing" or not target.root:
        return False
    website_path = f"{EXISTING_PREVIEW_DIR}/app.py"
    refs = [f"refs/remotes/origin/{target.branch}", "HEAD"]
    for ref in refs:
        result = runner(["git", "ls-tree", "-r", "--name-only", ref, "--", website_path], target.root, 8)
        if result.returncode == 0 and website_path in result.stdout.splitlines():
            return True
    return False


def public_url_from_sources(project: dict, target: DeploymentTarget, metadata: dict | None = None) -> str:
    deployment = project.get("deployment") if isinstance(project.get("deployment"), dict) else {}
    candidates = [
        target.public_url,
        deployment.get("public_url"),
        project.get("public_url"),
        project.get("deployment_url"),
        (metadata or {}).get("homepageUrl"),
    ]
    for candidate in candidates:
        url = normalize_public_url(candidate)
        if url:
            return url
    return ""


def deployment_readiness(
    stops_empty: bool,
    target: DeploymentTarget,
    bundle_error: str = "",
    data_quality_issues: int = 0,
) -> ReadinessResult:
    if stops_empty:
        return ReadinessResult(
            False,
            "Add project data before publishing",
            "Import a stop dataset, then return here to publish the website.",
            "Open Data",
            "data",
        )
    if data_quality_issues:
        return ReadinessResult(
            False,
            "Resolve data quality issues before publishing",
            f"Data Quality found {data_quality_issues:,} publication-blocking issue "
            f"occurrence{'s' if data_quality_issues != 1 else ''}. Review the affected records, "
            "correct the source data, and run the checks again.",
            "Open Data Quality",
            "data_quality",
        )
    if bundle_error:
        return ReadinessResult(
            False,
            "Fix the project before publishing",
            bundle_error,
            "Review project",
            "project",
        )
    if not target.repository:
        return ReadinessResult(
            False,
            "Complete settings before publishing",
            "Enter your GitHub username and destination repository before publishing.",
            "Open settings",
            "advanced",
        )
    if target.mode == "existing" and not target.repository_url:
        return ReadinessResult(
            False,
            "Reconnect the publishing destination",
            "The saved destination no longer has a usable address.",
            "Open settings",
            "advanced",
        )
    needs_public_confirmation = (
        target.mode == "create" and target.visibility == "public"
    ) or (
        target.mode == "existing"
        and (target.visibility == "public" or not target.visibility_verified)
    )
    if needs_public_confirmation and not target.allow_public_target:
        return ReadinessResult(
            False,
            "Confirm public repository publishing",
            "Repository visibility is public or could not be verified. The deployment includes study data and raw label history.",
            "Open settings",
            "advanced",
        )
    if shutil.which("git") is None:
        return ReadinessResult(
            False,
            "Install the publishing helper",
            "Git is required once on this computer so Stop-GIS can publish updates.",
            "View setup help",
            "advanced",
        )
    if target.mode == "create" and shutil.which("gh") is None:
        return ReadinessResult(
            False,
            "Connect a publishing account",
            "A one-time account connection is needed to create the website destination.",
            "View setup help",
            "advanced",
        )
    return ReadinessResult(
        True,
        "Ready to publish",
        "Stop-GIS found the project data and publishing destination.",
    )


def _checked(
    args: Sequence[str],
    cwd: Path,
    logs: list[str],
    runner: CommandRunner,
    timeout: int = 90,
) -> CommandResult:
    result = runner(args, cwd, timeout)
    command_name = " ".join(args[:2])
    safe_args = [_redact_sensitive(str(arg)) for arg in args]
    logs.append(f"$ {' '.join(safe_args)}")
    if result.output:
        logs.append(_redact_sensitive(result.output))
    if result.returncode != 0:
        detail = _redact_sensitive(
            result.output or f"{command_name} exited with status {result.returncode}."
        )
        raise RuntimeError(detail)
    return result


def _redact_sensitive(value: str) -> str:
    return re.sub(r"(https?://)[^/@\s]+@", r"\1[credentials]@", value)


def _safe_zip_read(bundle: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        info = bundle.getinfo(name)
    except KeyError:
        return None
    normalized = Path(info.filename)
    if normalized.is_absolute() or ".." in normalized.parts:
        raise RuntimeError(f"Unsafe generated file path: {info.filename}")
    return bundle.read(info)


def deployment_bundle_manifest(bundle_data: bytes) -> dict:
    try:
        with zipfile.ZipFile(BytesIO(bundle_data)) as bundle:
            content = _safe_zip_read(bundle, "deployment_manifest.json")
    except zipfile.BadZipFile as exc:
        raise RuntimeError("The deployment package is not a valid ZIP file.") from exc
    if content is None:
        raise RuntimeError(
            "The deployment package has no validation manifest. Download a fresh package from Stop-GIS."
        )
    try:
        manifest = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("The deployment package validation manifest is invalid.") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise RuntimeError("The deployment package uses an unsupported validation manifest.")
    return manifest


def validate_deployment_bundle(bundle_data: bytes, target: DeploymentTarget) -> dict:
    manifest = deployment_bundle_manifest(bundle_data)
    expected_repository = github_repository_slug(target.repository) or target.repository.strip().removesuffix(".git")
    manifest_repository = str(manifest.get("repository") or "")
    if manifest_repository.casefold() != expected_repository.casefold():
        raise RuntimeError(
            f"This package targets {manifest.get('repository') or 'an unknown repository'}, not {expected_repository}. "
            "Create a fresh package for the selected repository."
        )
    if manifest.get("deploy_mode") != target.mode:
        raise RuntimeError(
            f"This package was created for {manifest.get('deploy_mode') or 'an unknown'} deployment mode, "
            f"not {target.mode}. Create a fresh package."
        )
    expected_commit_message = normalize_deploy_commit_message(target.commit_message)
    if manifest.get("commit_message") != expected_commit_message:
        raise RuntimeError(
            "This package was created with a different commit message. "
            "Create a fresh package using the current deployment settings."
        )
    file_hashes = manifest.get("files")
    if not isinstance(file_hashes, dict) or not file_hashes:
        raise RuntimeError("The deployment package manifest contains no file hashes.")
    try:
        with zipfile.ZipFile(BytesIO(bundle_data)) as bundle:
            archive_names = [info.filename for info in bundle.infolist() if not info.is_dir()]
            if len(archive_names) != len(set(archive_names)):
                raise RuntimeError("The deployment package contains duplicate file paths.")
            manifest_names = set(file_hashes)
            expected_archive_names = manifest_names | {"deployment_manifest.json"}
            if set(archive_names) != expected_archive_names:
                missing = sorted(expected_archive_names - set(archive_names))
                extra = sorted(set(archive_names) - expected_archive_names)
                detail = "; ".join(
                    part for part in (
                        f"missing: {', '.join(missing)}" if missing else "",
                        f"unhashed: {', '.join(extra)}" if extra else "",
                    ) if part
                )
                raise RuntimeError(f"The deployment package contents do not match its manifest ({detail}).")
            required = set(CREATED_REPOSITORY_FILES) - {
                "deployment_manifest.json",
                "shade_study_raw_labels.csv",
            }
            missing_required = sorted(required - manifest_names)
            if missing_required:
                raise RuntimeError(
                    "The deployment package is incomplete; required files are missing: "
                    + ", ".join(missing_required)
                )
            for name, expected_hash in file_hashes.items():
                if not isinstance(name, str) or not isinstance(expected_hash, str):
                    raise RuntimeError("The deployment package manifest contains an invalid file entry.")
                content = _safe_zip_read(bundle, name)
                if content is None:
                    raise RuntimeError(f"The deployment package is incomplete; {name} is missing.")
                actual_hash = hashlib.sha256(content).hexdigest()
                if actual_hash != expected_hash.lower():
                    raise RuntimeError(
                        f"The deployment package is stale or damaged; {name} does not match its manifest hash."
                    )
            identity_content = _safe_zip_read(bundle, WEBSITE_IDENTITY_FILE)
            try:
                website_identity = json.loads((identity_content or b"").decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("The deployment package website identity is invalid.") from exc
            dataset = manifest.get("dataset") if isinstance(manifest.get("dataset"), dict) else {}
            release_sha256 = manifest.get("release_sha256")
            expected_website_identity = {
                "schema_version": 2 if release_sha256 is not None else 1,
                "study_id": manifest.get("study_id"),
                "repository": manifest.get("repository"),
                "dataset_sha256": dataset.get("sha256"),
            }
            if release_sha256 is not None:
                if not isinstance(release_sha256, str) or not re.fullmatch(
                    r"[0-9a-f]{64}", release_sha256
                ):
                    raise RuntimeError(
                        "The deployment package has an invalid release identity."
                    )
                release_hashes = {
                    name: digest
                    for name, digest in sorted(file_hashes.items())
                    if name not in {WEBSITE_IDENTITY_FILE, "README.md"}
                }
                release_json = json.dumps(
                    release_hashes, sort_keys=True, separators=(",", ":")
                )
                expected_release_sha256 = hashlib.sha256(
                    release_json.encode("utf-8")
                ).hexdigest()
                if release_sha256 != expected_release_sha256:
                    raise RuntimeError(
                        "The deployment package release identity does not match its files."
                    )
                expected_website_identity["release_sha256"] = release_sha256
            if website_identity != expected_website_identity:
                raise RuntimeError("The deployment package website identity does not match its manifest.")
            streamlit_config_content = _safe_zip_read(bundle, ".streamlit/config.toml")
            try:
                streamlit_config = tomllib.loads(
                    (streamlit_config_content or b"").decode("utf-8")
                )
            except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                raise RuntimeError("The deployment package Streamlit configuration is invalid.") from exc
            server_config = (
                streamlit_config.get("server")
                if isinstance(streamlit_config, dict)
                else None
            )
            if not isinstance(server_config, dict) or server_config.get("enableStaticServing") is not True:
                raise RuntimeError(
                    "The deployment package must enable Streamlit static file serving."
                )
    except zipfile.BadZipFile as exc:
        raise RuntimeError("The deployment package is not a valid ZIP file.") from exc
    bundle_id = manifest.get("bundle_id")
    if not isinstance(bundle_id, str) or not re.fullmatch(r"[0-9a-f]{64}", bundle_id):
        raise RuntimeError("The deployment package has an invalid bundle identity.")
    identity_json = manifest.get("identity_json")
    if not isinstance(identity_json, str):
        raise RuntimeError("The deployment package has no canonical bundle identity.")
    identity = {
        key: value
        for key, value in manifest.items()
        if key not in {"bundle_id", "deployed_paths", "identity_json"}
    }
    identity["files"] = {
        name: digest for name, digest in file_hashes.items() if name != "README.md"
    }
    canonical_identity = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    try:
        parsed_identity = json.loads(identity_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError("The deployment package canonical bundle identity is invalid.") from exc
    if parsed_identity != identity or identity_json != canonical_identity:
        raise RuntimeError("The deployment package canonical identity does not match its manifest.")
    expected_bundle_id = hashlib.sha256(identity_json.encode("utf-8")).hexdigest()
    if bundle_id != expected_bundle_id:
        raise RuntimeError("The deployment package bundle identity does not match its manifest.")
    return manifest


def _write_bundle_files(bundle_data: bytes, destination: Path, names: Sequence[str]) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BytesIO(bundle_data)) as bundle:
        for name in names:
            content = _safe_zip_read(bundle, name)
            target = destination / Path(name)
            if content is None:
                if target.exists() and target.is_file():
                    target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)


def _ensure_commit_identity(worktree: Path, logs: list[str], runner: CommandRunner) -> None:
    name = runner(["git", "config", "user.name"], worktree, 8)
    email = runner(["git", "config", "user.email"], worktree, 8)
    if not name.stdout.strip():
        _checked(["git", "config", "user.name", "Stop-GIS Publisher"], worktree, logs, runner)
    if not email.stdout.strip():
        _checked(
            ["git", "config", "user.email", "stop-gis-publisher@users.noreply.github.com"],
            worktree,
            logs,
            runner,
        )


def repository_root_uses_legacy_published_app(worktree: Path) -> bool:
    """Recognize the old generated root runtime without touching a builder entrypoint."""
    app_path = worktree / "app.py"
    if not app_path.is_file():
        return False
    try:
        source = app_path.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False
    builder_markers = (
        "from stop_gis.builder.app import",
        "import stop_gis.builder.app as builder_app",
        "from builder_app import",
        "import builder_app",
        "builder_app.main",
    )
    if any(marker in source for marker in builder_markers):
        return False
    if "shade_study_config.json" not in source or "shade_study_stops.csv" not in source:
        return False
    for manifest_path in (
        worktree / "deployment_manifest.json",
        worktree / EXISTING_PREVIEW_DIR / "deployment_manifest.json",
    ):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if (
            isinstance(manifest, dict)
            and manifest.get("schema_version") == 1
            and isinstance(manifest.get("study_id"), str)
            and isinstance(manifest.get("repository"), str)
            and manifest.get("deploy_mode") in {"create", "existing"}
            and isinstance(manifest.get("files"), dict)
            and _file_matches_hash(app_path, manifest["files"].get("app.py"))
        ):
            return True
    return False


def _file_matches_hash(path: Path, expected_hash: object) -> bool:
    if not path.is_file() or not isinstance(expected_hash, str):
        return False
    return hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash.lower()


def _streamlit_static_serving_enabled(path: Path) -> bool:
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    server = config.get("server") if isinstance(config, dict) else None
    return isinstance(server, dict) and server.get("enableStaticServing") is True


def manifest_owned_deployment_paths(worktree: Path, target: DeploymentTarget) -> list[str]:
    """Return only unchanged files proven to belong to this deployment bundle."""
    manifest_relative = (
        Path(EXISTING_PREVIEW_DIR) / "deployment_manifest.json"
        if target.mode == "existing"
        else Path("deployment_manifest.json")
    )
    manifest_path = worktree / manifest_relative
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    expected_repository = github_repository_slug(target.repository) or target.repository.strip().removesuffix(".git")
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or str(manifest.get("repository") or "").casefold() != expected_repository.casefold()
        or manifest.get("deploy_mode") != target.mode
        or not isinstance(manifest.get("files"), dict)
    ):
        return []

    hashes: dict[str, object] = manifest["files"]
    declared_paths = manifest.get("deployed_paths")
    if isinstance(declared_paths, list) and all(isinstance(path, str) for path in declared_paths):
        allowed_paths = (
            {
                *[f"{EXISTING_PREVIEW_DIR}/{name}" for name in EXISTING_BUNDLE_FILES],
                *EXISTING_ROOT_DATA_FILES,
                *EXISTING_ROOT_RUNTIME_FILES,
                *EXISTING_ROOT_CONFIG_FILES,
            }
            if target.mode == "existing"
            else set(CREATED_REPOSITORY_FILES)
        )
        candidates = []
        for value in declared_paths:
            relative = Path(value)
            if relative.is_absolute() or ".." in relative.parts:
                return []
            if relative.as_posix() not in allowed_paths:
                return []
            if relative.as_posix() == manifest_relative.as_posix():
                continue
            bundle_name = relative.as_posix()
            if target.mode == "existing" and bundle_name.startswith(f"{EXISTING_PREVIEW_DIR}/"):
                bundle_name = bundle_name.removeprefix(f"{EXISTING_PREVIEW_DIR}/")
            candidates.append((relative, hashes.get(bundle_name)))
        owned = [path.as_posix() for path, expected_hash in candidates if _file_matches_hash(worktree / path, expected_hash)]
        owned.append(manifest_relative.as_posix())
        return list(dict.fromkeys(owned))
    candidates: list[tuple[Path, object]] = []
    if target.mode == "existing":
        for name in EXISTING_BUNDLE_FILES:
            if name != "deployment_manifest.json":
                candidates.append((Path(EXISTING_PREVIEW_DIR) / name, hashes.get(name)))
        for name in EXISTING_ROOT_DATA_FILES:
            candidates.append((Path(name), hashes.get(name)))
        for name in EXISTING_ROOT_RUNTIME_FILES:
            candidates.append((Path(name), hashes.get(name)))
        for name in EXISTING_ROOT_CONFIG_FILES:
            candidates.append((Path(name), hashes.get(name)))
    else:
        candidates.extend(
            (Path(name), hashes.get(name))
            for name in CREATED_REPOSITORY_FILES
            if name != "deployment_manifest.json"
        )

    owned = [path.as_posix() for path, expected_hash in candidates if _file_matches_hash(worktree / path, expected_hash)]
    owned.append(manifest_relative.as_posix())
    return list(dict.fromkeys(owned))


def _write_deployed_paths(manifest_path: Path, paths: Sequence[str]) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["deployed_paths"] = list(dict.fromkeys(path.replace("\\", "/") for path in paths))
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def _refuse_unowned_collisions(
    worktree: Path,
    target: DeploymentTarget,
    planned_paths: Sequence[str],
    legacy_allowed: set[str] | None = None,
) -> None:
    owned = set(manifest_owned_deployment_paths(worktree, target))
    allowed = legacy_allowed or set()
    collisions = sorted(
        path for path in planned_paths
        if (worktree / path).exists() and path not in owned and path not in allowed
    )
    if collisions:
        raise RuntimeError(
            "Publishing would overwrite repository files not owned by this Stop-GIS deployment: "
            + ", ".join(collisions)
        )


def _publish_existing(
    bundle_data: bytes,
    target: DeploymentTarget,
    temp_root: Path,
    logs: list[str],
    runner: CommandRunner,
) -> tuple[bool, str]:
    worktree = temp_root / "repository"
    _checked(
        ["git", "clone", "--branch", target.branch, "--single-branch", target.repository_url, str(worktree)],
        temp_root,
        logs,
        runner,
        180,
    )
    refresh_legacy_root_runtime = repository_root_uses_legacy_published_app(worktree)
    owned_paths = set(manifest_owned_deployment_paths(worktree, target))
    streamlit_config_name = EXISTING_ROOT_CONFIG_FILES[0]
    streamlit_config_path = worktree / streamlit_config_name
    manage_streamlit_config = (
        not streamlit_config_path.exists() or streamlit_config_name in owned_paths
    )
    if (
        streamlit_config_path.exists()
        and not manage_streamlit_config
        and not _streamlit_static_serving_enabled(streamlit_config_path)
    ):
        raise RuntimeError(
            "The repository's .streamlit/config.toml does not enable static file serving. "
            "Set server.enableStaticServing = true there before publishing so Stop-GIS can verify the hosted study."
        )
    planned_paths = [
        *[f"{EXISTING_PREVIEW_DIR}/{name}" for name in EXISTING_BUNDLE_FILES],
        *EXISTING_ROOT_DATA_FILES,
    ]
    if manage_streamlit_config:
        planned_paths.extend(EXISTING_ROOT_CONFIG_FILES)
    legacy_allowed = set(EXISTING_ROOT_DATA_FILES)
    if refresh_legacy_root_runtime:
        planned_paths.extend(EXISTING_ROOT_RUNTIME_FILES)
        legacy_allowed.update(EXISTING_ROOT_RUNTIME_FILES)
    _refuse_unowned_collisions(
        worktree,
        target,
        planned_paths,
        legacy_allowed if refresh_legacy_root_runtime else set(),
    )
    root_data_existed = {
        name: (worktree / name).exists() for name in EXISTING_ROOT_DATA_FILES
    }
    _write_bundle_files(bundle_data, worktree / EXISTING_PREVIEW_DIR, EXISTING_BUNDLE_FILES)
    _write_bundle_files(bundle_data, worktree, EXISTING_ROOT_DATA_FILES)
    if manage_streamlit_config:
        _write_bundle_files(bundle_data, worktree, EXISTING_ROOT_CONFIG_FILES)
    staged_paths = [
        EXISTING_PREVIEW_DIR,
        *[
            name
            for name in EXISTING_ROOT_DATA_FILES
            if (worktree / name).exists() or root_data_existed[name]
        ],
    ]
    if manage_streamlit_config:
        staged_paths.extend(EXISTING_ROOT_CONFIG_FILES)
    if refresh_legacy_root_runtime:
        _write_bundle_files(bundle_data, worktree, EXISTING_ROOT_RUNTIME_FILES)
        staged_paths.extend(EXISTING_ROOT_RUNTIME_FILES)
        logs.append(
            "Detected a legacy generated root public app and refreshed its active runtime."
        )
    deployed_paths = [
        path for path in planned_paths if (worktree / path).is_file()
    ]
    manifest_relative = f"{EXISTING_PREVIEW_DIR}/deployment_manifest.json"
    _write_deployed_paths(worktree / manifest_relative, deployed_paths)
    _checked(
        ["git", "add", "--", *staged_paths],
        worktree,
        logs,
        runner,
    )
    _checked(["git", "status", "--short", "--branch"], worktree, logs, runner)
    diff = runner(["git", "diff", "--cached", "--quiet"], worktree, 30)
    if diff.returncode == 0:
        commit = _checked(["git", "rev-parse", "HEAD"], worktree, logs, runner).stdout.strip()
        return False, commit
    if diff.returncode != 1:
        raise RuntimeError(diff.output or "Stop-GIS could not inspect the prepared website update.")
    _ensure_commit_identity(worktree, logs, runner)
    _checked(
        ["git", "commit", "-m", normalize_deploy_commit_message(target.commit_message)],
        worktree,
        logs,
        runner,
    )
    _checked(["git", "push", "origin", f"HEAD:{target.branch}"], worktree, logs, runner, 180)
    _checked(["git", "status", "--short", "--branch"], worktree, logs, runner)
    commit = _checked(["git", "rev-parse", "HEAD"], worktree, logs, runner).stdout.strip()
    return True, commit


def _publish_created(
    bundle_data: bytes,
    target: DeploymentTarget,
    temp_root: Path,
    logs: list[str],
    runner: CommandRunner,
) -> tuple[bool, str]:
    worktree = temp_root / "repository"
    worktree.mkdir()
    _write_bundle_files(bundle_data, worktree, CREATED_REPOSITORY_FILES)
    created_paths = [name for name in CREATED_REPOSITORY_FILES if (worktree / name).exists()]
    _write_deployed_paths(worktree / "deployment_manifest.json", created_paths)
    _checked(["git", "init"], worktree, logs, runner)
    _checked(["git", "branch", "-M", target.branch], worktree, logs, runner)
    _ensure_commit_identity(worktree, logs, runner)
    _checked(["git", "add", "--", *created_paths], worktree, logs, runner)
    _checked(["git", "status", "--short", "--branch"], worktree, logs, runner)
    _checked(
        ["git", "commit", "-m", normalize_deploy_commit_message(target.commit_message)],
        worktree,
        logs,
        runner,
    )
    _checked(
        [
            "gh",
            "repo",
            "create",
            target.repository,
            f"--{target.visibility}",
            "--source=.",
            "--remote=origin",
            "--push",
        ],
        worktree,
        logs,
        runner,
        180,
    )
    _checked(["git", "status", "--short", "--branch"], worktree, logs, runner)
    commit = _checked(["git", "rev-parse", "HEAD"], worktree, logs, runner).stdout.strip()
    return True, commit


def verify_website(
    url: str,
    attempts: int = 9,
    interval: float = 10.0,
    expected_markers: Sequence[str] = (),
) -> tuple[bool, str]:
    public_url = normalize_public_url(url)
    if not public_url:
        return False, "No website address is available yet."
    markers = [str(marker).strip().lower() for marker in expected_markers if str(marker).strip()]
    if not markers:
        return False, "No expected website identity was provided."
    identity_url = f"{public_url.rstrip('/')}/app/{WEBSITE_IDENTITY_FILE}"
    last_error = "Website did not become available."
    for attempt in range(attempts):
        try:
            clean_url, addresses = _validated_web_target(
                identity_url,
                label="Website",
                allow_private_env="STOP_GIS_ALLOW_PRIVATE_WEBSITE_URLS",
            )
            for redirect_count in range(6):
                connection, response = _open_pinned_api_response(clean_url, addresses)
                try:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = str(response.headers.get("Location") or "").strip()
                        if not location or redirect_count >= 5:
                            raise ValueError("Website redirected too many times or without a destination.")
                        clean_url, addresses = _validated_web_target(
                            urllib.parse.urljoin(clean_url, location),
                            label="Website",
                            allow_private_env="STOP_GIS_ALLOW_PRIVATE_WEBSITE_URLS",
                        )
                        continue
                    if 200 <= response.status < 400:
                        reader = getattr(response, "read", None)
                        body = reader(1024 * 1024) if callable(reader) else b""
                        try:
                            identity_document = json.loads(body.decode("utf-8"))
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            identity_document = None
                        available_values = (
                            {str(value).strip().lower() for value in identity_document.values()}
                            if isinstance(identity_document, dict)
                            else set()
                        )
                        if all(marker in available_values for marker in markers):
                            return True, f"Website identity was verified with HTTP {response.status}."
                        last_error = (
                            f"Website responded with HTTP {response.status}, but did not identify this Stop-GIS study."
                        )
                        break
                    last_error = f"Website responded with HTTP {response.status}."
                    break
                finally:
                    response.close()
                    connection.close()
        except (
            OSError,
            TimeoutError,
            ValueError,
            ssl.SSLError,
            http.client.HTTPException,
        ) as exc:
            last_error = str(exc)
        if attempt + 1 < attempts:
            time.sleep(interval)
    return False, last_error


def publish_website(
    bundle_data: bytes,
    target: DeploymentTarget,
    progress: ProgressCallback | None = None,
    runner: CommandRunner = run_command,
    verify_attempts: int = 9,
    verify_interval: float = 10.0,
) -> PublishResult:
    logs: list[str] = []
    notify = progress or (lambda _stage, _message: None)
    try:
        notify("Check project", "Checking project data and publishing access")
        if not bundle_data:
            raise RuntimeError("The website package is empty. Return to the project and try again.")
        if target.mode == "create" and target.visibility not in {"public", "private"}:
            raise RuntimeError("Repository visibility must be 'public' or 'private' when creating a repository.")
        if target.mode == "create" and target.visibility == "public" and not target.allow_public_target:
            raise RuntimeError(
                "Confirm public-repository publishing explicitly before creating this repository."
            )
        manifest = validate_deployment_bundle(bundle_data, target)
        if target.mode == "existing":
            expected_repository = github_repository_slug(target.repository)
            clone_repository = github_repository_slug(target.repository_url)
            if (
                not clone_repository
                or clone_repository.casefold() != expected_repository.casefold()
            ):
                raise RuntimeError(
                    "The repository clone URL does not match the selected GitHub repository. "
                    "Reconnect the publishing destination before continuing."
                )
            metadata = repository_metadata(target, runner)
            actual_repository = github_repository_slug(str(metadata.get("nameWithOwner") or ""))
            if (
                actual_repository
                and actual_repository.casefold() != expected_repository.casefold()
            ):
                raise RuntimeError(
                    f"GitHub returned metadata for {actual_repository}, not {expected_repository}."
                )
            actual_visibility = str(metadata.get("visibility") or "").strip().lower()
            if not actual_visibility and not target.allow_public_target:
                raise RuntimeError(
                    "Repository visibility could not be verified. Confirm public-repository publishing explicitly before continuing."
                )
            if actual_visibility and actual_visibility != "private" and not target.allow_public_target:
                raise RuntimeError(
                    f"Target repository {target.repository} is {actual_visibility}. "
                    "Confirm public-repository publishing explicitly before continuing."
                )
        logs.append(
            f"Validated deployment bundle {manifest.get('bundle_id', '')} for {manifest.get('repository', '')}."
        )
        with zipfile.ZipFile(BytesIO(bundle_data)) as bundle:
            if "app.py" not in bundle.namelist():
                raise RuntimeError("The website package is missing its application file.")

        notify("Prepare website", "Preparing a clean website update")
        with tempfile.TemporaryDirectory(prefix="shade_gis_publish_") as directory:
            temp_root = Path(directory)
            if target.mode == "create":
                changed, commit = _publish_created(bundle_data, target, temp_root, logs, runner)
            else:
                changed, commit = _publish_existing(bundle_data, target, temp_root, logs, runner)

        notify("Publish", "Website files are published; waiting for the host")
        if not target.public_url:
            notify("Verify website", "A one-time hosting connection is needed")
            return PublishResult(
                True,
                changed=changed,
                commit=commit,
                needs_host_setup=True,
                message="The website files are published. Finish the one-time website setup to make them public.",
                logs=logs,
            )

        notify("Verify website", "Checking the public website")
        verified, verification_message = verify_website(
            target.public_url,
            attempts=verify_attempts,
            interval=verify_interval,
            expected_markers=(
                str(manifest.get("study_id") or ""),
                str(manifest.get("repository") or ""),
                str((manifest.get("dataset") or {}).get("sha256") or ""),
                str(manifest.get("release_sha256") or ""),
            ),
        )
        return PublishResult(
            True,
            changed=changed,
            commit=commit,
            public_url=target.public_url,
            verified=verified,
            message=(
                "The website is published and responding."
                if verified
                else "The update was published, but the website could not be verified yet."
            ),
            logs=[*logs, verification_message],
        )
    except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
        message = (
            f"Publishing could not access a required file or temporary directory: {exc}"
            if isinstance(exc, OSError)
            else str(exc)
        )
        logs.append(message)
        return PublishResult(False, message=message, logs=logs)


def unpublish_website(
    target: DeploymentTarget,
    runner: CommandRunner = run_command,
) -> PublishResult:
    logs: list[str] = []
    try:
        expected_repository = github_repository_slug(target.repository)
        clone_repository = github_repository_slug(target.repository_url)
        if (
            not clone_repository
            or clone_repository.casefold() != expected_repository.casefold()
        ):
            raise RuntimeError(
                "The repository clone URL does not match the selected GitHub repository. "
                "Reconnect the publishing destination before continuing."
            )
        with tempfile.TemporaryDirectory(prefix="shade_gis_unpublish_") as directory:
            temp_root = Path(directory)
            worktree = temp_root / "repository"
            _checked(
                ["git", "clone", "--branch", target.branch, "--single-branch", target.repository_url, str(worktree)],
                temp_root,
                logs,
                runner,
                180,
            )
            existing = manifest_owned_deployment_paths(worktree, target)
            owned_content = [
                path for path in existing if not path.endswith("deployment_manifest.json")
            ]
            if not owned_content:
                return PublishResult(
                    False,
                    changed=False,
                    message=(
                        "No unchanged Stop-GIS deployment manifest was found. "
                        "Unpublish was stopped to protect repository files."
                    ),
                    logs=logs,
                )
            manifest_relative = (
                f"{EXISTING_PREVIEW_DIR}/deployment_manifest.json"
                if target.mode == "existing" else "deployment_manifest.json"
            )
            manifest_path = worktree / manifest_relative
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            declared = manifest.get("deployed_paths")
            if not isinstance(declared, list) or not declared:
                hashes = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
                if target.mode == "existing":
                    legacy_runtime_present = repository_root_uses_legacy_published_app(worktree) or any(
                        _file_matches_hash(worktree / name, hashes.get(name))
                        for name in EXISTING_ROOT_RUNTIME_FILES
                    )
                    declared = [
                        *[
                            f"{EXISTING_PREVIEW_DIR}/{name}"
                            for name in EXISTING_BUNDLE_FILES
                            if name == "deployment_manifest.json" or name in hashes
                        ],
                        *[name for name in EXISTING_ROOT_DATA_FILES if name in hashes],
                        *[
                            name for name in EXISTING_ROOT_RUNTIME_FILES
                            if legacy_runtime_present and name in hashes
                        ],
                        *[
                            name for name in EXISTING_ROOT_CONFIG_FILES
                            if _file_matches_hash(worktree / name, hashes.get(name))
                        ],
                    ]
                else:
                    declared = [name for name in CREATED_REPOSITORY_FILES if name in hashes]
                    declared.append("deployment_manifest.json")
            existing_set = set(existing)
            modified = sorted(
                path for path in declared
                if path != manifest_relative and (worktree / path).exists() and path not in existing_set
            )
            if modified:
                return PublishResult(
                    False,
                    changed=False,
                    message=(
                        "Unpublish was stopped because deployed files were modified: "
                        + ", ".join(modified)
                    ),
                    logs=logs,
                )
            existing = [
                path for path in declared
                if path != manifest_relative and (worktree / path).exists()
            ]
            existing.append(manifest_relative)
            _checked(["git", "rm", "--ignore-unmatch", "--", *existing], worktree, logs, runner)
            _ensure_commit_identity(worktree, logs, runner)
            _checked(["git", "commit", "-m", "Unpublish Stop-GIS website"], worktree, logs, runner)
            _checked(["git", "push", "origin", f"HEAD:{target.branch}"], worktree, logs, runner, 180)
            commit = _checked(["git", "rev-parse", "HEAD"], worktree, logs, runner).stdout.strip()
        return PublishResult(
            True,
            changed=True,
            commit=commit,
            message="The published website files were removed.",
            logs=logs,
        )
    except (RuntimeError, OSError) as exc:
        message = (
            f"Unpublishing could not access a required file or temporary directory: {exc}"
            if isinstance(exc, OSError)
            else str(exc)
        )
        logs.append(message)
        return PublishResult(False, message=message, logs=logs)
