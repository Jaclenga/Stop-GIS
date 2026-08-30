from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import Any

from stop_gis.deploy.service import (
    DEFAULT_DEPLOY_COMMIT_MESSAGE,
    normalize_deploy_commit_message,
)


APP_DIR = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = Path(__file__).with_name("templates")


def _render_template(name: str, replacements: dict[str, str]) -> str:
    template = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
    required_tokens = set(re.findall(r"@@[A-Z_]+@@", template))
    missing_tokens = sorted(required_tokens - replacements.keys())
    if missing_tokens:
        raise ValueError(f"Missing deployment template values: {', '.join(missing_tokens)}")
    for token in required_tokens:
        template = template.replace(token, replacements[token])
    return template


def slugify_repo_name(value: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", str(value or "").strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip(".-_")
    return slug or "shade-study-app"


def github_new_repo_url(project: dict[str, Any], repo_name: str) -> str:
    params = {
        "name": slugify_repo_name(repo_name),
        "description": f"{project.get('name', 'Stop audit')} Streamlit app",
        "visibility": "public" if project.get("visibility") == "Public" else "private",
    }
    return "https://github.com/new?" + urllib.parse.urlencode(params)


PUBLISHED_APP_SOURCE_PATH = APP_DIR / "published_app.py"
PUBLIC_VOTING_SOURCE_PATH = APP_DIR / "public_voting.py"
EXISTING_REPO_PREVIEW_DIR = "preview_app"
SHARED_RUNTIME_SOURCE_PATHS = {
    "stop_gis/__init__.py": APP_DIR / "stop_gis" / "__init__.py",
    "stop_gis/assessment_modes.py": APP_DIR / "stop_gis" / "assessment_modes.py",
    "stop_gis/public_app.py": APP_DIR / "stop_gis" / "public_app.py",
    "stop_gis/public_voting.py": APP_DIR / "stop_gis" / "public_voting.py",
}


def published_app_source() -> str:
    return PUBLISHED_APP_SOURCE_PATH.read_text(encoding="utf-8")


def public_voting_source() -> str:
    return PUBLIC_VOTING_SOURCE_PATH.read_text(encoding="utf-8")


def shared_runtime_sources() -> dict[str, bytes]:
    """Return the canonical shared modules required by a published app."""
    return {
        relative_path: source_path.read_bytes()
        for relative_path, source_path in SHARED_RUNTIME_SOURCE_PATHS.items()
    }


def postgres_vote_schema() -> str:
    return (TEMPLATES_DIR / "postgres_vote_schema.sql").read_text(encoding="utf-8")


def secrets_example() -> str:
    return (TEMPLATES_DIR / "secrets.toml.example").read_text(encoding="utf-8")


def deployment_guide() -> str:
    return (TEMPLATES_DIR / "DEPLOYMENT.md").read_text(encoding="utf-8")


def least_privilege_roles() -> str:
    return (TEMPLATES_DIR / "least_privilege_roles.sql.example").read_text(encoding="utf-8")


def verify_database_script() -> str:
    return (TEMPLATES_DIR / "verify_database.py").read_text(encoding="utf-8")


def migrate_database_script() -> str:
    return (TEMPLATES_DIR / "migrate_database.py").read_text(encoding="utf-8")


def dotenv_example() -> str:
    return (TEMPLATES_DIR / ".env.example").read_text(encoding="utf-8")


def powershell_literal(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def streamlit_entrypoint_path(deploy_mode: str) -> str:
    if deploy_mode == "create":
        return "app.py"
    if deploy_mode == "existing":
        return f"{EXISTING_REPO_PREVIEW_DIR}/app.py"
    raise ValueError("deploy_mode must be 'create' or 'existing'")


def deploy_launcher_script(
    bundle_name: str,
    repo_name: str,
    branch: str = "main",
    deploy_mode: str = "existing",
    visibility: str = "private",
    commit_message: str = DEFAULT_DEPLOY_COMMIT_MESSAGE,
) -> str:
    if deploy_mode not in {"create", "existing"}:
        raise ValueError("deploy_mode must be 'create' or 'existing'")
    if visibility not in {"public", "private"}:
        raise ValueError("visibility must be 'public' or 'private'")

    if deploy_mode == "existing":
        repository_verification = """        gh repo view $RepositoryName --json nameWithOwner
        if ($LASTEXITCODE -ne 0) {
            throw "GitHub repository '$RepositoryName' was not found or is not accessible."
        }
"""
        publish_command = """        & $DeployScript.FullName `
            -Mode existing `
            -RepositoryName $RepositoryName `
            -Branch $Branch `
            -CommitMessage $CommitMessage
"""
    else:
        repository_verification = ""
        allow_public = " -AllowPublicTarget" if visibility == "public" else ""
        publish_command = f"""        & $DeployScript.FullName `
            -Mode create `
            -RepositoryName $RepositoryName `
            -Branch $Branch `
            -CommitMessage $CommitMessage `
            -Visibility $Visibility{allow_public}
"""

    return _render_template(
        "launcher.ps1",
        {
            "@@BUNDLE_NAME_LITERAL@@": powershell_literal(bundle_name),
            "@@REPOSITORY_NAME_LITERAL@@": powershell_literal(repo_name),
            "@@BRANCH_LITERAL@@": powershell_literal(branch),
            "@@COMMIT_MESSAGE_LITERAL@@": powershell_literal(
                normalize_deploy_commit_message(commit_message)
            ),
            "@@VISIBILITY_LITERAL@@": powershell_literal(visibility),
            "@@REPOSITORY_VERIFICATION@@": repository_verification,
            "@@PUBLISH_COMMAND@@": publish_command,
        },
    )


def deploy_readme(
    repo_name: str,
    project: dict[str, Any],
    deploy_mode: str = "existing",
    bundle_name: str = "",
    commit_message: str = DEFAULT_DEPLOY_COMMIT_MESSAGE,
) -> str:
    folder_name = slugify_repo_name(repo_name.rstrip("/").split("/")[-1].replace(".git", ""))
    resolved_bundle_name = bundle_name or f"{folder_name}.zip"
    commit_message = normalize_deploy_commit_message(commit_message)
    main_file_path = streamlit_entrypoint_path(deploy_mode)

    if deploy_mode == "create":
        publish_intro = "create the target repository used for this bundle"
        published_layout = (
            "The new repository contains only this public preview app and its runtime files. "
            "It does not contain the Stop-GIS builder."
        )
    else:
        publish_intro = "publish into the target repository used for this bundle"
        published_layout = (
            f"The helper installs the public preview under `{EXISTING_REPO_PREVIEW_DIR}/` and protects a "
            "repository-root Stop-GIS builder. If the root app is an older generated public runtime, the "
            "helper upgrades that active runtime in place. It also refreshes the generated "
            "`shade_study_stops.csv`, `shade_study_raw_labels.csv`, and `shade_study_config.json` snapshots "
            "at the repository root."
        )

    launcher_script = deploy_launcher_script(
        resolved_bundle_name,
        repo_name,
        deploy_mode=deploy_mode,
        commit_message=commit_message,
    )
    return _render_template(
        "README.md",
        {
            "@@APP_NAME@@": str(project.get("name", "Stop Audit")),
            "@@PUBLISHED_LAYOUT@@": published_layout,
            "@@BUNDLE_NAME@@": resolved_bundle_name,
            "@@PUBLISH_INTRO@@": publish_intro,
            "@@LAUNCHER_SCRIPT@@": launcher_script,
            "@@REPOSITORY_NAME@@": repo_name,
            "@@COMMIT_MESSAGE_LITERAL@@": powershell_literal(commit_message),
            "@@MAIN_FILE_PATH@@": main_file_path,
        },
    )


def deploy_script(
    repo_name: str,
    commit_message: str = DEFAULT_DEPLOY_COMMIT_MESSAGE,
) -> str:
    return _render_template(
        "deploy_to_github.ps1",
        {
            "@@REPOSITORY_NAME_LITERAL@@": powershell_literal(repo_name),
            "@@COMMIT_MESSAGE_LITERAL@@": powershell_literal(
                normalize_deploy_commit_message(commit_message)
            ),
            "@@PREVIEW_DIRECTORY@@": EXISTING_REPO_PREVIEW_DIR,
        },
    )
