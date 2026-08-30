from __future__ import annotations

import html
import hashlib
import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from builder_app import (
    build_github_deploy_bundle,
    deployment_session_freshness_issue,
    load_project_into_session,
    set_page,
)
from platform_store import list_images
from shade_gis.data_quality import evaluate_data_quality
from shade_gis.deploy import deploy_launcher_script, github_new_repo_url, slugify_repo_name
from shade_gis.deployment import (
    STREAMLIT_WORKSPACE_URL,
    DeploymentTarget,
    PublishResult,
    deployment_bundle_manifest,
    deployment_readiness,
    detect_deployment_target,
    github_repository_url,
    normalize_public_url,
    normalize_deploy_commit_message,
    public_url_from_sources,
    publish_website,
    repository_has_published_app,
    repository_metadata,
    unpublish_website,
    verify_website,
)
from shade_gis.pages.voting_deployment import render_voting_deployment_wizard


DEPLOYMENT_RESULT_KEY = "deploy_page_result"
DEPLOYMENT_TARGET_KEY = "deploy_page_result_target"
DEPLOYMENT_SETTINGS_KEY = "deploy_page_show_settings"
DEPLOYMENT_UNPUBLISH_KEY = "deploy_page_confirm_unpublish"
DEPLOYMENT_UNPUBLISHED_KEY = "deploy_page_unpublished"
DEPLOYMENT_STAGE_KEY = "deploy_page_stage"
DEPLOYMENT_SESSION_PROJECT_KEY = "deploy_page_settings_project_id"
STAGES = ("Check project", "Prepare website", "Publish", "Verify website")
BUNDLE_FILE_CATALOG = [
    ("app.py", "Public website"),
    ("public_voting.py", "Optional visitor voting"),
    ("shade_study_stops.csv", "Active imported dataset used by the public website"),
    ("shade_study_raw_labels.csv", "Published label history, when available"),
    ("shade_study_config.json", "Project display settings"),
    ("deployment_manifest.json", "Validated project snapshot and file hashes"),
    ("requirements.txt", "Website runtime"),
    ("migrations/001_public_voting.sql", "Idempotent PostgreSQL voting schema"),
    ("migrations/least_privilege_roles.sql.example", "Recommended owner/runtime role grants"),
    ("scripts/verify_database.py", "Sanitized connection and read/write verification"),
    ("scripts/migrate_database.py", "Versioned migration command"),
    (".streamlit/secrets.toml.example", "Credential placeholders only; never real secrets"),
    (".env.example", "Provider-neutral environment placeholders"),
    ("DEPLOYMENT.md", "No-custody provider and hosting guide"),
    ("README.md", "Manual deployment documentation"),
    ("deploy_to_github.ps1", "Manual publishing fallback"),
]


def deployment_target_key(target: DeploymentTarget) -> str:
    return "|".join(
        [target.repository.strip(), target.branch.strip(), target.mode, target.public_url.strip()]
    )


def bundle_file_count(bundle_data: bytes) -> int:
    if not bundle_data:
        return len(BUNDLE_FILE_CATALOG)
    with zipfile.ZipFile(io.BytesIO(bundle_data)) as bundle:
        return len([name for name in bundle.namelist() if not name.endswith("/")])


@st.cache_data(ttl=60, show_spinner=False)
def cached_repository_metadata(repository: str, repository_url: str, branch: str, root: str) -> dict:
    return repository_metadata(
        DeploymentTarget(
            repository=repository,
            repository_url=repository_url,
            branch=branch,
            root=Path(root) if root else None,
        )
    )


def website_identity_markers(bundle_data: bytes) -> tuple[str, str, str, str]:
    manifest = deployment_bundle_manifest(bundle_data)
    dataset = manifest.get("dataset") if isinstance(manifest.get("dataset"), dict) else {}
    return (
        str(manifest.get("study_id") or ""),
        str(manifest.get("repository") or ""),
        str(dataset.get("sha256") or ""),
        str(manifest.get("release_sha256") or ""),
    )


@st.cache_data(ttl=45, show_spinner=False)
def cached_website_check(
    url: str, study_id: str, repository: str, dataset_sha256: str, release_sha256: str
) -> tuple[bool, str]:
    return verify_website(
        url,
        attempts=1,
        interval=0,
        expected_markers=(study_id, repository, dataset_sha256, release_sha256),
    )


def render_deploy_styles() -> None:
    st.markdown(
        """
        <style>
        section.main > div.block-container { max-width: 900px; }
        .deploy-intro {
            max-width: 700px; color: #52606d; font-size: 1.04rem;
            margin: -0.25rem 0 1.4rem;
        }
        .deploy-readiness, .deploy-success, .deploy-almost {
            border-radius: 18px; padding: 1.25rem 1.35rem; margin: 0.75rem 0 1rem;
        }
        .deploy-readiness.ready {
            border: 1px solid #86c995; background: linear-gradient(135deg, #f0fdf4, #ffffff);
        }
        .deploy-readiness.blocked {
            border: 1px solid #f0b36a; background: linear-gradient(135deg, #fff7ed, #ffffff);
        }
        .deploy-card-kicker {
            color: #64748b; font-size: .76rem; font-weight: 750;
            letter-spacing: .06em; text-transform: uppercase; margin-bottom: .25rem;
        }
        .deploy-card-title { color: #0f172a; font-size: 1.28rem; font-weight: 780; }
        .deploy-card-copy { color: #475569; margin-top: .3rem; }
        .deploy-success {
            border: 1px solid #59a86b; background: linear-gradient(135deg, #ecfdf3, #ffffff);
            box-shadow: 0 14px 34px rgba(22, 101, 52, .10);
        }
        .deploy-success .deploy-card-title { font-size: 1.55rem; color: #14532d; }
        .deploy-success-url {
            display: block; color: #166534; font-weight: 700; overflow-wrap: anywhere;
            margin-top: .6rem;
        }
        .deploy-almost { border: 1px solid #93c5fd; background: #eff6ff; }
        .deploy-stage-list { display: grid; gap: .5rem; margin: .85rem 0 .5rem; }
        .deploy-stage {
            display: grid; grid-template-columns: 1.8rem 1fr auto; align-items: center;
            gap: .6rem; padding: .72rem .85rem; border-radius: 11px; background: #f8fafc;
        }
        .deploy-stage.active { background: #fff7ed; border: 1px solid #fed7aa; }
        .deploy-stage.complete { background: #f0fdf4; }
        .deploy-stage-icon { color: #94a3b8; font-weight: 800; }
        .deploy-stage.complete .deploy-stage-icon { color: #15803d; }
        .deploy-stage.active .deploy-stage-icon { color: #c2410c; }
        .deploy-stage-state { color: #64748b; font-size: .82rem; }
        .deploy-loading {
            width: .9rem; height: .9rem; display: inline-block; border-radius: 50%;
            border: 2px solid #fdba74; border-top-color: #c2410c;
            animation: deploy-spin .85s linear infinite;
        }
        @keyframes deploy-spin { to { transform: rotate(360deg); } }
        .st-key-deploy_publish button, .st-key-deploy_update button {
            min-height: 3.6rem; border-radius: 13px; font-size: 1.08rem; font-weight: 780;
            box-shadow: 0 10px 24px rgba(190, 24, 24, .16);
        }
        .deploy-estimate { color: #64748b; text-align: center; margin: .5rem 0 1rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_readiness_card(title: str, message: str, ready: bool) -> None:
    card_class = "ready" if ready else "blocked"
    kicker = "Publication check"
    st.markdown(
        f"""
        <div class="deploy-readiness {card_class}">
          <div class="deploy-card-kicker">{kicker}</div>
          <div class="deploy-card-title">{html.escape(title)}</div>
          <div class="deploy-card-copy">{html.escape(message)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def stage_markup(active_stage: str = "", completed: set[str] | None = None) -> str:
    completed = completed or set()
    rows: list[str] = []
    for index, label in enumerate(STAGES, start=1):
        if label in completed:
            css_class, icon, state = "complete", "✓", "Complete"
        elif label == active_stage:
            css_class = "active"
            icon = '<span class="deploy-loading" aria-label="In progress"></span>'
            state = "In progress"
        elif label == "Verify website" and set(STAGES[:3]).issubset(completed):
            css_class, icon, state = "pending", "○", "Optional"
        else:
            css_class, icon, state = "pending", str(index), "Waiting"
        rows.append(
            f'<div class="deploy-stage {css_class}">'
            f'<span class="deploy-stage-icon">{icon}</span>'
            f'<span>{html.escape(label)}</span>'
            f'<span class="deploy-stage-state">{state}</span></div>'
        )
    return '<div class="deploy-stage-list">' + "".join(rows) + "</div>"


def render_stages(active_stage: str = "", completed: set[str] | None = None, target=None) -> None:
    renderer = target or st
    renderer.markdown(stage_markup(active_stage, completed), unsafe_allow_html=True)


def render_copy_link(public_url: str) -> None:
    safe_url = json.dumps(public_url)
    components.html(
        f"""
        <button id="copy-link" style="width:100%;height:42px;border:1px solid #d1d5db;
          border-radius:8px;background:white;font:600 14px sans-serif;cursor:pointer;">
          Copy link
        </button>
        <script>
          const button = document.getElementById('copy-link');
          button.addEventListener('click', async () => {{
            await navigator.clipboard.writeText({safe_url});
            button.textContent = 'Copied';
            setTimeout(() => button.textContent = 'Copy link', 1600);
          }});
        </script>
        """,
        height=50,
    )


def render_success_card(public_url: str) -> None:
    st.markdown(
        f"""
        <div class="deploy-success">
          <div class="deploy-card-kicker">Website published</div>
          <div class="deploy-card-title">Your website is live</div>
          <div class="deploy-card-copy">The latest project is available at:</div>
          <span class="deploy-success-url">{html.escape(public_url)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_repository_success_card(repository: str, changed: bool) -> None:
    title = "Your app files are on GitHub" if changed else "Repository already up to date"
    copy = (
        "Website hosting and verification were skipped. You can add a hosted website later."
        if changed
        else "The generated app already matches GitHub, so no commit or push was needed."
    )
    st.markdown(
        f"""
        <div class="deploy-success">
          <div class="deploy-card-kicker">Repository published</div>
          <div class="deploy-card-title">{html.escape(title)}</div>
          <div class="deploy-card-copy">{html.escape(copy)}</div>
          <span class="deploy-success-url">{html.escape(repository)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _initialize_target_state(detected: DeploymentTarget, project: dict) -> None:
    project_id = str(st.session_state.get("active_project_id") or "")
    if st.session_state.get(DEPLOYMENT_SESSION_PROJECT_KEY) == project_id:
        return

    saved = project.get("deployment") if isinstance(project.get("deployment"), dict) else {}
    saved_repository = str(saved.get("repository", "") or "").strip().strip("/")
    saved_username = str(saved.get("github_username", "") or "").strip().strip("/")
    saved_destination = str(saved.get("destination_repository", "") or "").strip().strip("/")
    if saved_repository and "/" in saved_repository:
        repository_username, repository_name = saved_repository.split("/", 1)
        saved_username = saved_username or repository_username
        saved_destination = saved_destination or repository_name

    st.session_state["deploy_github_username"] = saved_username
    st.session_state["deploy_destination_repository"] = saved_destination
    st.session_state["deploy_branch"] = str(saved.get("branch", "") or detected.branch or "main")
    st.session_state["deploy_commit_message"] = normalize_deploy_commit_message(
        saved.get("commit_message")
    )
    saved_mode = str(saved.get("mode") or "")
    default_mode = "existing" if detected.repository else "create"
    st.session_state["deploy_mode"] = saved_mode if saved_mode in {"existing", "create"} else default_mode
    saved_visibility = str(saved.get("visibility") or "")
    st.session_state["deploy_visibility"] = (
        saved_visibility
        if saved_visibility in {"private", "public"}
        else "public" if project.get("visibility") == "Public" else "private"
    )
    st.session_state["deploy_public_url"] = str(
        saved.get("public_url") or detected.public_url or ""
    )
    st.session_state["deploy_allow_public_target"] = False
    st.session_state[DEPLOYMENT_SESSION_PROJECT_KEY] = project_id


def _remember_target_settings(project: dict, target: DeploymentTarget) -> None:
    username = str(st.session_state.get("deploy_github_username", "") or "").strip().strip("/")
    destination = str(
        st.session_state.get("deploy_destination_repository", "") or ""
    ).strip().strip("/")
    project["deployment"] = {
        "github_username": username,
        "destination_repository": destination,
        "repository": f"{username}/{destination}" if username and destination else "",
        "branch": target.branch,
        "commit_message": target.commit_message,
        "mode": target.mode,
        "visibility": target.visibility,
        "public_url": target.public_url,
    }


def _current_target(detected: DeploymentTarget, project: dict) -> DeploymentTarget:
    mode_label = st.session_state.get("deploy_mode", "existing")
    mode = "create" if mode_label == "create" else "existing"
    username = str(st.session_state.get("deploy_github_username", "")).strip().strip("/")
    destination = str(st.session_state.get("deploy_destination_repository", "")).strip().strip("/")
    repository = f"{username}/{destination}" if username and destination else ""
    repository_url = github_repository_url(repository)
    if mode == "existing" and repository == detected.repository and detected.repository_url:
        repository_url = detected.repository_url
    public_url = normalize_public_url(st.session_state.get("deploy_public_url"))
    target = DeploymentTarget(
        repository=repository,
        repository_url=repository_url,
        branch=str(st.session_state.get("deploy_branch", "main")).strip() or "main",
        root=detected.root,
        mode=mode,
        visibility=str(st.session_state.get("deploy_visibility", "private")),
        public_url=public_url,
        commit_message=normalize_deploy_commit_message(
            st.session_state.get("deploy_commit_message")
        ),
        detected=detected.detected and repository == detected.repository,
        allow_public_target=bool(st.session_state.get("deploy_allow_public_target", False)),
    )
    metadata = {}
    if target.repository:
        metadata = cached_repository_metadata(
            target.repository,
            target.repository_url,
            target.branch,
            str(target.root or ""),
        )
        metadata_url = public_url_from_sources(project, target, metadata)
        if metadata_url:
            st.session_state["deploy_public_url"] = metadata_url
            target = replace(target, public_url=metadata_url)
        default_branch = (metadata.get("defaultBranchRef") or {}).get("name")
        if default_branch and not st.session_state.get("deploy_branch"):
            st.session_state["deploy_branch"] = default_branch
            target = replace(target, branch=default_branch)
        actual_visibility = str(metadata.get("visibility") or "").strip().lower()
        if target.mode == "existing" and actual_visibility in {"private", "public"}:
            target = replace(
                target,
                visibility=actual_visibility,
                visibility_verified=True,
            )
    return target


def _bundle_result_key(target: DeploymentTarget, bundle_data: bytes) -> str:
    try:
        bundle_id = str(deployment_bundle_manifest(bundle_data).get("bundle_id") or "")
    except RuntimeError:
        # Incomplete settings legitimately produce no bundle. Scope any stored
        # failure to the exact malformed/empty bytes without crashing the page.
        bundle_id = "invalid-" + hashlib.sha256(bundle_data).hexdigest()
    return f"{deployment_target_key(target)}|{bundle_id}"


def _store_result(target: DeploymentTarget, result: PublishResult, bundle_data: bytes) -> None:
    st.session_state[DEPLOYMENT_RESULT_KEY] = result
    st.session_state[DEPLOYMENT_TARGET_KEY] = _bundle_result_key(target, bundle_data)


def _stored_result(target: DeploymentTarget, bundle_data: bytes) -> PublishResult | None:
    if st.session_state.get(DEPLOYMENT_TARGET_KEY) != _bundle_result_key(target, bundle_data):
        return None
    result = st.session_state.get(DEPLOYMENT_RESULT_KEY)
    return result if isinstance(result, PublishResult) else None


def _run_publish(bundle_data: bytes, target: DeploymentTarget) -> PublishResult:
    stage_box = st.empty()
    completed: set[str] = set()

    def update(stage: str, _message: str) -> None:
        stage_index = STAGES.index(stage)
        completed.clear()
        completed.update(STAGES[:stage_index])
        render_stages(stage, completed, stage_box)

    result = publish_website(bundle_data, target, progress=update)
    if result.verified:
        render_stages("", set(STAGES), stage_box)
    elif result.needs_host_setup:
        render_stages("", set(STAGES[:3]), stage_box)
    _store_result(target, result, bundle_data)
    st.session_state[DEPLOYMENT_STAGE_KEY] = ""
    return result


def _render_technical_details(target: DeploymentTarget, result: PublishResult | None, bundle_data: bytes) -> None:
    failed = bool(result and not result.success)
    label = "Error details" if failed else "View technical details"
    with st.expander(label, expanded=failed):
        details = {
            "repository": target.repository,
            "repository_url": target.repository_url,
            "branch": target.branch,
            "mode": target.mode,
            "entrypoint": target.entrypoint,
            "commit_message": target.commit_message,
            "public_url": target.public_url,
            "bundle_files": bundle_file_count(bundle_data),
        }
        if result:
            details.update(
                {
                    "commit": result.commit,
                    "changed": result.changed,
                    "verified": result.verified,
                }
            )
        st.json(details)
        if result and result.logs:
            st.markdown("##### Diagnostic output")
            st.code("\n\n".join(result.logs), language="text")


def _render_publish_error(result: PublishResult) -> None:
    st.error(result.message or "Deployment failed before the repository could be updated.")
    if result.logs:
        st.markdown("##### Deployment error details")
        st.code("\n\n".join(result.logs), language="text")


def _render_optional_website_setup(
    target: DeploymentTarget, result: PublishResult, bundle_data: bytes
) -> None:
    st.markdown(
        """
        <div class="deploy-almost">
          <div class="deploy-card-title">Optional website hosting</div>
          <div class="deploy-card-copy">Your repository is published. Connect it to a website host and verify the link, or skip this step for now.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.link_button("Set up hosted website", STREAMLIT_WORKSPACE_URL, type="primary", width="stretch")
    website_url = st.text_input(
        "Website link",
        value=st.session_state.get("deploy_public_url", ""),
        placeholder="https://your-study.streamlit.app",
    )
    action_columns = st.columns(2, gap="small")
    verify_clicked = action_columns[0].button("Verify website", width="stretch")
    skip_clicked = action_columns[1].button("Skip for now", width="stretch")

    if skip_clicked:
        result.needs_host_setup = False
        result.verification_skipped = True
        result.message = "The repository is published. Website verification was skipped."
        _store_result(target, result, bundle_data)
        st.rerun()

    if verify_clicked:
        normalized_url = normalize_public_url(website_url)
        with st.spinner("Checking the website…"):
            verified, message = verify_website(
                normalized_url,
                expected_markers=website_identity_markers(bundle_data),
            )
        if verified:
            st.session_state["deploy_public_url"] = normalized_url
            verified_target = replace(target, public_url=normalized_url)
            result.public_url = normalized_url
            result.verified = True
            result.needs_host_setup = False
            result.verification_skipped = False
            result.message = "The website is published and responding."
            result.logs.append(message)
            _store_result(verified_target, result, bundle_data)
            st.rerun()
        else:
            st.error("The website is not reachable yet. Check the link, or skip verification for now.")


def _render_settings(
    project: dict,
    target: DeploymentTarget,
    bundle_data: bytes,
    bundle_name: str,
) -> None:
    expanded = bool(st.session_state.pop(DEPLOYMENT_SETTINGS_KEY, False))
    with st.expander("Settings", expanded=expanded):
        st.caption("GitHub username and destination repository are required before publishing.")
        st.caption("These settings save automatically with this project.")
        st.selectbox(
            "Publishing destination",
            options=["existing", "create"],
            format_func=lambda value: "Use an existing repository" if value == "existing" else "Create a repository",
            key="deploy_mode",
        )
        st.text_input(
            "GitHub username",
            key="deploy_github_username",
            placeholder="github-user",
            help="Required. Enter the GitHub account or organization that owns the repository.",
        )
        confirmation_needed = (
            target.mode == "create" and target.visibility == "public"
        ) or (
            target.mode == "existing"
            and (target.visibility == "public" or not target.visibility_verified)
        )
        if confirmation_needed:
            st.checkbox(
                "I understand this may publish study data and raw label history to a public repository",
                key="deploy_allow_public_target",
                help="Required each session for public repositories or when existing-repository visibility cannot be verified.",
            )
        st.text_input(
            "Destination repository",
            key="deploy_destination_repository",
            placeholder="shade-study-site",
            help="Required. Enter the repository name only.",
        )
        st.text_input("Default branch", key="deploy_branch")
        st.text_input(
            "Commit message",
            key="deploy_commit_message",
            help="Used for the Git commit created when this website is published.",
        )
        st.selectbox(
            "Repository visibility",
            options=["private", "public"],
            key="deploy_visibility",
            disabled=st.session_state.get("deploy_mode") != "create",
            help=(
                "Visibility can only be selected when Stop-GIS creates a new repository. "
                "For an existing repository, change its visibility in the repository's GitHub settings."
            ),
        )
        st.text_input(
            "Hosted website address",
            key="deploy_public_url",
            placeholder="https://your-study.streamlit.app",
            help="Optional. Add an existing website address so Stop-GIS can verify it after publishing.",
        )
        st.markdown("##### Hosting")
        st.caption(
            f"The automatic package uses Streamlit Community Cloud and `{target.entrypoint}`. "
            "A connected site updates automatically when Stop-GIS publishes repository changes."
        )
        if target.mode == "create":
            st.link_button("Open repository setup", github_new_repo_url(project, target.repository))

        st.markdown("##### Manual fallback")
        st.caption("Use this only when automatic publishing is unavailable.")
        st.download_button(
            "Download website package",
            data=bundle_data,
            file_name=bundle_name,
            mime="application/zip",
            disabled=not bool(bundle_data),
            width="stretch",
        )
        if bundle_data and target.repository:
            st.code(
                deploy_launcher_script(
                    bundle_name,
                    target.repository,
                    target.branch,
                    target.mode,
                    target.visibility,
                    target.commit_message,
                ),
                language="powershell",
            )
        st.markdown("##### Package contents")
        st.dataframe(
            pd.DataFrame(BUNDLE_FILE_CATALOG, columns=["File", "Purpose"]),
            width="stretch",
            hide_index=True,
        )


def render_deploy_page() -> None:
    project = st.session_state["project"]
    stops = st.session_state["stops"]
    detected = detect_deployment_target()
    _initialize_target_state(detected, project)
    target = _current_target(detected, project)
    unpublished_notice = st.session_state.get(DEPLOYMENT_UNPUBLISHED_KEY)
    if unpublished_notice:
        st.session_state["deploy_public_url"] = ""
        target = replace(target, public_url="")
    _remember_target_settings(project, target)

    render_deploy_styles()
    st.title("Publish website")
    st.markdown(
        f'<p class="deploy-intro">Turn <strong>{html.escape(project.get("name", "this stop audit"))}</strong> '
        "into a public website. Stop-GIS prepares the files, publishes them, and checks the result.</p>",
        unsafe_allow_html=True,
    )

    bundle_data = b""
    bundle_error = ""
    freshness_issue = ""
    project_id = str(st.session_state.get("active_project_id") or "")
    images = list_images(project_id) if project_id else pd.DataFrame()
    quality_report = evaluate_data_quality(stops, images)
    if quality_report.publication_ready:
        detected_freshness_issue = deployment_session_freshness_issue()
        if detected_freshness_issue:
            # A stale tab must block publishing to a configured target. When no
            # target exists yet, preserve the normal settings guidance; the
            # self-hosting download simply remains unavailable until refreshed.
            if target.repository:
                freshness_issue = detected_freshness_issue
                bundle_error = detected_freshness_issue
        else:
            try:
                bundle_repository = target.repository or (
                    "self-hosted/"
                    + slugify_repo_name(project.get("name", "shade-study"))
                )
                bundle_data = build_github_deploy_bundle(
                    bundle_repository,
                    target.mode if target.repository else "create",
                    target.commit_message,
                )
            except Exception as exc:  # Builder validation errors are converted into one actionable readiness issue.
                bundle_error = str(exc).splitlines()[0] or "The project could not be prepared for publishing."
    readiness = deployment_readiness(
        stops.empty,
        target,
        bundle_error,
        data_quality_issues=quality_report.total_issues,
    )
    if freshness_issue:
        readiness = replace(
            readiness,
            title="Reload the latest project before publishing",
            message=freshness_issue,
            action_label="Reload saved project",
            action="reload",
        )
    existing_package = repository_has_published_app(target)
    if readiness.ready and existing_package:
        readiness = replace(
            readiness,
            message="Stop-GIS found the existing website and is ready to publish an update.",
        )
    bundle_stem = slugify_repo_name(target.repository.split("/")[-1] or project.get("name", "shade-study"))
    bundle_name = f"{bundle_stem}.zip"
    if bundle_data:
        manifest = deployment_bundle_manifest(bundle_data)
        bundle_name = f"{bundle_stem}-{manifest['bundle_id'][:12]}.zip"
    voting_enabled = bool(
        st.session_state.get("visualization", {}).get("voting", {}).get("enabled", False)
    )
    if voting_enabled:
        render_voting_deployment_wizard(
            bundle_data,
            bundle_name,
            voting_enabled=True,
        )
    # A previous successful deployment must not bypass checks after the active
    # dataset changes. Keep its result for a later clean rerun, but surface the
    # current blocker until the dashboard passes again.
    result = _stored_result(target, bundle_data) if readiness.ready else None
    if result is None and readiness.ready and target.public_url:
        verified, verification_message = cached_website_check(
            target.public_url,
            *website_identity_markers(bundle_data),
        )
        if verified:
            result = PublishResult(
                True,
                public_url=target.public_url,
                verified=True,
                message="An existing published website was detected.",
                logs=[verification_message],
            )
            _store_result(target, result, bundle_data)

    if unpublished_notice:
        st.info(
            "The generated website files were removed. If the old address still appears, remove the app "
            "from the hosting workspace to finish unpublishing."
        )
        st.link_button("Finish unpublishing", STREAMLIT_WORKSPACE_URL, width="stretch")

    if result and result.verified and result.public_url:
        render_success_card(result.public_url)
        action_columns = st.columns(4, gap="small")
        with action_columns[0]:
            st.link_button("Open website", result.public_url, width="stretch")
        with action_columns[1]:
            render_copy_link(result.public_url)
        with action_columns[2]:
            publish_update = st.button("Publish update", width="stretch", key="deploy_update")
        with action_columns[3]:
            unpublish = st.button("Unpublish", width="stretch")

        if publish_update:
            with st.status("Publishing the latest update…", expanded=True) as status:
                updated_result = _run_publish(bundle_data, target)
                if updated_result.verified:
                    status.update(label="Website updated", state="complete", expanded=False)
                    st.rerun()
                else:
                    status.update(label="The update needs attention", state="error", expanded=True)
                    _render_publish_error(updated_result)

        if unpublish:
            st.session_state[DEPLOYMENT_UNPUBLISH_KEY] = True
        if st.session_state.get(DEPLOYMENT_UNPUBLISH_KEY):
            st.warning("Unpublishing removes the generated website files and may take the public site offline.")
            confirm_columns = st.columns(2)
            if confirm_columns[0].button("Keep website", width="stretch"):
                st.session_state.pop(DEPLOYMENT_UNPUBLISH_KEY, None)
                st.rerun()
            if confirm_columns[1].button("Confirm unpublish", type="primary", width="stretch"):
                with st.spinner("Unpublishing website…"):
                    unpublish_result = unpublish_website(target)
                _store_result(target, unpublish_result, bundle_data)
                st.session_state.pop(DEPLOYMENT_UNPUBLISH_KEY, None)
                if unpublish_result.success:
                    st.session_state[DEPLOYMENT_UNPUBLISHED_KEY] = {
                        "public_url": result.public_url,
                        "commit": unpublish_result.commit,
                    }
                    st.session_state["deploy_public_url"] = ""
                    st.session_state.pop(DEPLOYMENT_RESULT_KEY, None)
                    st.session_state.pop(DEPLOYMENT_TARGET_KEY, None)
                    st.rerun()
                else:
                    st.error(unpublish_result.message)
        _render_technical_details(target, result, bundle_data)
        _render_settings(project, target, bundle_data, bundle_name)
        return

    if result and result.verification_skipped:
        render_repository_success_card(target.repository, result.changed)
        action_columns = st.columns(2, gap="small")
        action_columns[0].link_button(
            "Open GitHub repository",
            github_repository_url(target.repository),
            width="stretch",
        )
        if action_columns[1].button("Add a hosted website", width="stretch"):
            result.verification_skipped = False
            result.needs_host_setup = True
            _store_result(target, result, bundle_data)
            st.rerun()
        _render_technical_details(target, result, bundle_data)
        _render_settings(project, target, bundle_data, bundle_name)
        return

    if result and result.needs_host_setup:
        render_stages("", set(STAGES[:3]))
        _render_optional_website_setup(target, result, bundle_data)
        _render_technical_details(target, result, bundle_data)
        _render_settings(project, target, bundle_data, bundle_name)
        return

    render_readiness_card(readiness.title, readiness.message, readiness.ready)
    if not readiness.ready:
        if readiness.action == "data":
            st.button(readiness.action_label, type="primary", width="stretch", on_click=set_page, args=("Data",))
        elif readiness.action == "data_quality":
            st.button(
                readiness.action_label,
                type="primary",
                width="stretch",
                on_click=set_page,
                args=("Data Quality",),
            )
        elif readiness.action == "reload":
            if st.button(readiness.action_label, type="primary", width="stretch"):
                load_project_into_session(st.session_state["active_project_id"])
                st.session_state.pop(DEPLOYMENT_RESULT_KEY, None)
                st.session_state.pop(DEPLOYMENT_TARGET_KEY, None)
                st.rerun()
        else:
            if st.button(readiness.action_label, type="primary", width="stretch"):
                st.session_state[DEPLOYMENT_SETTINGS_KEY] = True
                st.rerun()
    else:
        with st.container(key="deploy_publish"):
            publish_clicked = st.button("Publish app", type="primary", width="stretch")
        st.markdown('<div class="deploy-estimate">This usually takes 1–3 minutes.</div>', unsafe_allow_html=True)
        if publish_clicked:
            st.session_state.pop(DEPLOYMENT_UNPUBLISHED_KEY, None)
            with st.status("Publishing your website…", expanded=True) as status:
                result = _run_publish(bundle_data, target)
                if result.verified:
                    status.update(label="Website published", state="complete", expanded=False)
                    st.rerun()
                elif result.needs_host_setup:
                    status_label = "Repository published" if result.changed else "Repository already up to date"
                    status.update(label=status_label, state="complete", expanded=False)
                else:
                    status.update(label="Publishing stopped", state="error", expanded=True)
                    _render_publish_error(result)

    result = _stored_result(target, bundle_data)
    if result and result.needs_host_setup:
        _render_optional_website_setup(target, result, bundle_data)

    if result:
        _render_technical_details(target, result, bundle_data)
    _render_settings(project, target, bundle_data, bundle_name)
