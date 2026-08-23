"""Build validated, content-addressed website deployment bundles."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from typing import Any

import pandas as pd

from shade_gis.builder_imports import calculate_priority_scores
from shade_gis.deploy.artifacts import (
    deployment_guide,
    deploy_readme,
    deploy_script,
    dotenv_example,
    least_privilege_roles,
    migrate_database_script,
    postgres_vote_schema,
    public_voting_source,
    published_app_source,
    secrets_example,
    slugify_repo_name,
    streamlit_entrypoint_path,
    verify_database_script,
)
from shade_gis.deployment import (
    DEFAULT_DEPLOY_COMMIT_MESSAGE,
    WEBSITE_IDENTITY_FILE,
    github_repository_slug,
    normalize_deploy_commit_message,
)


RUNTIME_REQUIREMENTS = (
    "streamlit>=1.57,<2\n"
    "Authlib>=1.3.2,<2\n"
    "pandas>=2.2,<3\n"
    "pyarrow>=24,<25\n"
    "pydeck>=0.8,<1\n"
    "psycopg[binary]>=3.2,<4\n"
    "psycopg_pool>=3.2,<4\n"
)
STREAMLIT_CONFIG = (
    "[server]\nheadless = true\nenableXsrfProtection = true\nenableStaticServing = true\n\n"
    "[browser]\ngatherUsageStats = false\n"
)
DEPLOY_GITIGNORE = (
    "__pycache__/\n"
    "*.pyc\n"
    "*.sqlite3\n"
    "*.sqlite3-*\n"
    ".env\n"
    ".env.*\n"
    "!.env.example\n"
    ".streamlit/secrets.toml\n"
    "credentials.json\n"
    "*-credentials.json\n"
    "*.secret\n"
    "secret-output*\n"
    "_shade_gis_publish_*/\n"
)


@dataclass(frozen=True)
class DeploymentBundleSpec:
    """All builder state needed to create one immutable deployment bundle."""

    repository: str
    project: dict[str, Any]
    study_id: str
    stops: pd.DataFrame
    raw_labels: pd.DataFrame
    config_json: str
    priority_weights: dict[str, float]
    deploy_mode: str = "existing"
    commit_message: str = DEFAULT_DEPLOY_COMMIT_MESSAGE


def build_deployment_bundle(spec: DeploymentBundleSpec) -> bytes:
    """Create a deployable ZIP without depending on Streamlit session state."""

    if spec.deploy_mode not in {"create", "existing"}:
        raise ValueError("deploy_mode must be 'create' or 'existing'")
    if spec.stops.empty:
        raise ValueError("Import project data before creating a deployment package.")

    commit_message = normalize_deploy_commit_message(spec.commit_message)
    repository = (
        github_repository_slug(spec.repository)
        or spec.repository.strip().removesuffix(".git")
    )
    stops = spec.stops.copy()
    stops["priority_score"] = calculate_priority_scores(stops, spec.priority_weights)

    files: dict[str, bytes] = {
        "app.py": published_app_source().encode("utf-8"),
        "public_voting.py": public_voting_source().encode("utf-8"),
        "shade_study_stops.csv": stops.to_csv(index=False).encode("utf-8"),
        "shade_study_config.json": spec.config_json.encode("utf-8"),
        "requirements.txt": RUNTIME_REQUIREMENTS.encode("utf-8"),
        ".streamlit/config.toml": STREAMLIT_CONFIG.encode("utf-8"),
        ".streamlit/secrets.toml.example": secrets_example().encode("utf-8"),
        ".env.example": dotenv_example().encode("utf-8"),
        "migrations/001_public_voting.sql": postgres_vote_schema().encode("utf-8"),
        "migrations/least_privilege_roles.sql.example": least_privilege_roles().encode("utf-8"),
        "scripts/verify_database.py": verify_database_script().encode("utf-8"),
        "scripts/migrate_database.py": migrate_database_script().encode("utf-8"),
        "DEPLOYMENT.md": deployment_guide().encode("utf-8"),
        ".gitignore": DEPLOY_GITIGNORE.encode("utf-8"),
        "deploy_to_github.ps1": deploy_script(repository, commit_message).encode("utf-8"),
    }
    if not spec.raw_labels.empty:
        files["shade_study_raw_labels.csv"] = spec.raw_labels.to_csv(index=False).encode("utf-8")
    release_hashes = {
        name: hashlib.sha256(content).hexdigest()
        for name, content in sorted(files.items())
    }
    release_json = json.dumps(release_hashes, sort_keys=True, separators=(",", ":"))
    release_sha256 = hashlib.sha256(release_json.encode("utf-8")).hexdigest()
    files[WEBSITE_IDENTITY_FILE] = json.dumps(
        {
            "schema_version": 2,
            "study_id": spec.study_id,
            "repository": repository,
            "dataset_sha256": hashlib.sha256(files["shade_study_stops.csv"]).hexdigest(),
            "release_sha256": release_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    file_hashes = {
        name: hashlib.sha256(content).hexdigest()
        for name, content in sorted(files.items())
    }
    manifest_core = {
        "schema_version": 1,
        "study_id": spec.study_id,
        "project_name": str(spec.project.get("name", "Shade Study")),
        "repository": repository,
        "deploy_mode": spec.deploy_mode,
        "commit_message": commit_message,
        "entrypoint": streamlit_entrypoint_path(spec.deploy_mode),
        "release_sha256": release_sha256,
        "dataset": {
            "file": "shade_study_stops.csv",
            "rows": int(len(stops)),
            "columns": [str(column) for column in stops.columns],
            "sha256": file_hashes["shade_study_stops.csv"],
        },
        "files": file_hashes,
    }
    identity_json = json.dumps(manifest_core, sort_keys=True, separators=(",", ":"))
    bundle_id = hashlib.sha256(identity_json.encode("utf-8")).hexdigest()
    repository_name = repository.rstrip("/").split("/")[-1].replace(".git", "")
    bundle_name = f"{slugify_repo_name(repository_name)}-{bundle_id[:12]}.zip"
    files["README.md"] = deploy_readme(
        repository,
        spec.project,
        spec.deploy_mode,
        bundle_name=bundle_name,
        commit_message=commit_message,
    ).encode("utf-8")
    # README depends on bundle_id, so it is excluded from the identity hash but
    # included in the ownership hashes used for safe unpublishing.
    manifest = {
        **manifest_core,
        "files": {
            **file_hashes,
            "README.md": hashlib.sha256(files["README.md"]).hexdigest(),
        },
        "identity_json": identity_json,
        "bundle_id": bundle_id,
    }
    files["deployment_manifest.json"] = json.dumps(
        manifest,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return buffer.getvalue()
