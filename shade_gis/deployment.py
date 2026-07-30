from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Callable, Sequence


GITHUB_HOST = "github.com"
STREAMLIT_WORKSPACE_URL = "https://share.streamlit.io/"
EXISTING_PREVIEW_DIR = "preview_app"
EXISTING_BUNDLE_FILES = (
    "app.py",
    "public_voting.py",
    "shade_study_stops.csv",
    "shade_study_raw_labels.csv",
    "shade_study_config.json",
    "deployment_manifest.json",
    "requirements.txt",
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
)
CREATED_REPOSITORY_FILES = (
    *EXISTING_BUNDLE_FILES,
    "README.md",
    "deploy_to_github.ps1",
    ".gitignore",
    ".streamlit/config.toml",
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
    ssh_match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$", candidate)
    if ssh_match:
        return f"{ssh_match.group('owner')}/{ssh_match.group('repo')}"
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
    public_url = normalize_public_url(os.environ.get("SHADE_GIS_PUBLIC_URL"))
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
    if shutil.which("git") is None:
        return ReadinessResult(
            False,
            "Install the publishing helper",
            "Git is required once on this computer so Shade-GIS can publish updates.",
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
        "Shade-GIS found the project data and publishing destination.",
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
            "The deployment package has no validation manifest. Download a fresh package from Shade-GIS."
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
    if manifest.get("repository") != expected_repository:
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
    except zipfile.BadZipFile as exc:
        raise RuntimeError("The deployment package is not a valid ZIP file.") from exc
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
        _checked(["git", "config", "user.name", "Shade-GIS Publisher"], worktree, logs, runner)
    if not email.stdout.strip():
        _checked(
            ["git", "config", "user.email", "shade-gis-publisher@users.noreply.github.com"],
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
        "from builder_app import",
        "import builder_app",
        "builder_app.main",
    )
    if any(marker in source for marker in builder_markers):
        return False
    return "shade_study_config.json" in source and "shade_study_stops.csv" in source


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
    _write_bundle_files(bundle_data, worktree / EXISTING_PREVIEW_DIR, EXISTING_BUNDLE_FILES)
    _write_bundle_files(bundle_data, worktree, EXISTING_ROOT_DATA_FILES)
    staged_paths = [EXISTING_PREVIEW_DIR, *EXISTING_ROOT_DATA_FILES]
    if refresh_legacy_root_runtime:
        _write_bundle_files(bundle_data, worktree, EXISTING_ROOT_RUNTIME_FILES)
        staged_paths.extend(EXISTING_ROOT_RUNTIME_FILES)
        logs.append(
            "Detected a legacy generated root public app and refreshed its active runtime."
        )
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
        raise RuntimeError(diff.output or "Shade-GIS could not inspect the prepared website update.")
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
    _checked(["git", "init"], worktree, logs, runner)
    _checked(["git", "branch", "-M", target.branch], worktree, logs, runner)
    _ensure_commit_identity(worktree, logs, runner)
    _checked(["git", "add", "--", *CREATED_REPOSITORY_FILES], worktree, logs, runner)
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


def verify_website(url: str, attempts: int = 9, interval: float = 10.0) -> tuple[bool, str]:
    public_url = normalize_public_url(url)
    if not public_url:
        return False, "No website address is available yet."
    last_error = "Website did not become available."
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(public_url, headers={"User-Agent": "Shade-GIS publisher/1.0"})
            with urllib.request.urlopen(request, timeout=12) as response:
                if 200 <= response.status < 400:
                    return True, f"Website responded with HTTP {response.status}."
                last_error = f"Website responded with HTTP {response.status}."
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
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
        manifest = validate_deployment_bundle(bundle_data, target)
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
        )
        return PublishResult(
            verified,
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
    except (RuntimeError, zipfile.BadZipFile) as exc:
        logs.append(str(exc))
        return PublishResult(False, message=str(exc), logs=logs)


def unpublish_website(
    target: DeploymentTarget,
    runner: CommandRunner = run_command,
) -> PublishResult:
    logs: list[str] = []
    try:
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
            paths = [EXISTING_PREVIEW_DIR] if target.mode == "existing" else list(CREATED_REPOSITORY_FILES)
            existing = [path for path in paths if (worktree / path).exists()]
            if not existing:
                return PublishResult(True, changed=False, message="The website was already unpublished.", logs=logs)
            _checked(["git", "rm", "-r", "--ignore-unmatch", "--", *existing], worktree, logs, runner)
            _ensure_commit_identity(worktree, logs, runner)
            _checked(["git", "commit", "-m", "Unpublish Shade-GIS website"], worktree, logs, runner)
            _checked(["git", "push", "origin", f"HEAD:{target.branch}"], worktree, logs, runner, 180)
            commit = _checked(["git", "rev-parse", "HEAD"], worktree, logs, runner).stdout.strip()
        return PublishResult(
            True,
            changed=True,
            commit=commit,
            message="The published website files were removed.",
            logs=logs,
        )
    except RuntimeError as exc:
        logs.append(str(exc))
        return PublishResult(False, message=str(exc), logs=logs)
