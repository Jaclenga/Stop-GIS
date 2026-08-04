"""Deployment package generation and publishing helpers."""

from shade_gis.deploy.artifacts import (
    deployment_guide,
    deploy_launcher_script,
    deploy_readme,
    deploy_script,
    dotenv_example,
    github_new_repo_url,
    least_privilege_roles,
    migrate_database_script,
    powershell_literal,
    postgres_vote_schema,
    public_voting_source,
    published_app_source,
    secrets_example,
    slugify_repo_name,
    streamlit_entrypoint_path,
    verify_database_script,
)
from shade_gis.deploy.bundle import DeploymentBundleSpec, build_deployment_bundle

__all__ = [
    "deployment_guide",
    "deploy_launcher_script",
    "deploy_readme",
    "deploy_script",
    "dotenv_example",
    "github_new_repo_url",
    "least_privilege_roles",
    "migrate_database_script",
    "powershell_literal",
    "postgres_vote_schema",
    "public_voting_source",
    "published_app_source",
    "secrets_example",
    "slugify_repo_name",
    "streamlit_entrypoint_path",
    "verify_database_script",
    "DeploymentBundleSpec",
    "build_deployment_bundle",
]
