from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlparse

import streamlit as st

from public_voting import (
    VoteStorageError,
    check_vote_database_connection,
    confirm_vote_database_read_write,
    initialize_vote_database,
)


DATABASE_URL_KEY = "_transient_vote_database_url"
DATABASE_ACTION_RESULT_KEY = "_transient_vote_database_action_result"
PROVIDER_KEY = "deploy_voting_database_provider"
CSRF_TOKEN_KEY = "_vote_database_setup_csrf"
SETUP_SESSION_KEY = "_vote_database_setup_session"
ALLOW_UNAUTHENTICATED_SETUP_ENV = "STOP_GIS_ALLOW_UNAUTHENTICATED_DATABASE_SETUP"
SETUP_RATE_LIMIT = 5
SETUP_RATE_WINDOW_SECONDS = 300
PROVIDERS = (
    "Set up with Neon",
    "Set up with Supabase",
    "Connect existing PostgreSQL",
    "Configure manually",
    "Local SQLite",
)
_SETUP_ATTEMPTS: dict[str, list[float]] = {}
_SETUP_ATTEMPTS_LOCK = threading.Lock()


def _is_local_builder() -> bool:
    configured_override = os.environ.get(ALLOW_UNAUTHENTICATED_SETUP_ENV) or os.environ.get(
        "SHADE_GIS_ALLOW_UNAUTHENTICATED_DATABASE_SETUP", ""
    )
    if str(configured_override).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True
    try:
        host = urlparse(str(st.context.url)).hostname or ""
    except Exception:
        host = ""
    return host.lower() in {"127.0.0.1", "::1", "localhost"}


def _authenticated_setup_identity() -> str:
    try:
        user = st.user
        if not bool(user.is_logged_in):
            return ""
        stable_value = str(
            getattr(user, "sub", "") or getattr(user, "email", "")
        ).strip()
        if not stable_value:
            return "authenticated"
        return hashlib.sha256(stable_value.encode("utf-8")).hexdigest()
    except Exception:
        return ""


def _setup_actions_allowed() -> bool:
    return _is_local_builder() or bool(_authenticated_setup_identity())


def _setup_rate_key() -> str:
    identity = _authenticated_setup_identity()
    if identity:
        return f"user:{identity}"
    if SETUP_SESSION_KEY not in st.session_state:
        st.session_state[SETUP_SESSION_KEY] = secrets.token_urlsafe(24)
    value = str(st.session_state[SETUP_SESSION_KEY])
    return "session:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _claim_setup_attempt() -> bool:
    now = time.monotonic()
    key = _setup_rate_key()
    with _SETUP_ATTEMPTS_LOCK:
        recent = [
            attempt
            for attempt in _SETUP_ATTEMPTS.get(key, [])
            if now - attempt < SETUP_RATE_WINDOW_SECONDS
        ]
        if len(recent) >= SETUP_RATE_LIMIT:
            _SETUP_ATTEMPTS[key] = recent
            return False
        recent.append(now)
        _SETUP_ATTEMPTS[key] = recent
        return True


def _setup_csrf_token() -> str:
    if CSRF_TOKEN_KEY not in st.session_state:
        st.session_state[CSRF_TOKEN_KEY] = secrets.token_urlsafe(32)
    return str(st.session_state[CSRF_TOKEN_KEY])


def _consume_database_url(action: str, csrf_token: str = "") -> None:
    """Use a connection URL once, retain only a generic result, then clear it."""
    database_url = str(st.session_state.get(DATABASE_URL_KEY) or "").strip()
    try:
        # Streamlit also enforces XSRF protection globally. This nonce binds the
        # sensitive callback to the server-side browser session that rendered it.
        if csrf_token:
            expected_token = str(st.session_state.get(CSRF_TOKEN_KEY) or "")
            if not expected_token or not hmac.compare_digest(csrf_token, expected_token):
                raise VoteStorageError("The setup session expired. Reload the page and try again.")
            if not _setup_actions_allowed():
                raise VoteStorageError("Sign in before testing external database credentials.")
            if not _claim_setup_attempt():
                raise VoteStorageError(
                    "Too many database setup attempts. Wait five minutes and try again."
                )
        if not database_url:
            raise ValueError("Paste a PostgreSQL connection string first.")
        if action in {"test", "verify"}:
            check_vote_database_connection(database_url)
            message = "Connection successful. Stop-GIS did not save the connection string."
            st.session_state["vote_database_connection_verified"] = True
        elif action == "initialize":
            initialize_vote_database(database_url)
            message = (
                "Voting tables and indexes are initialized. "
                "Stop-GIS discarded the connection string."
            )
            st.session_state["vote_database_schema_initialized"] = True
        elif action == "confirm":
            confirm_vote_database_read_write(database_url)
            message = (
                "Runtime read/write access is confirmed and the temporary record was removed."
            )
            st.session_state["vote_database_read_write_confirmed"] = True
        else:
            raise ValueError("Unsupported database setup action.")
        st.session_state[DATABASE_ACTION_RESULT_KEY] = {"ok": True, "message": message}
    except ValueError as exc:
        st.session_state[DATABASE_ACTION_RESULT_KEY] = {"ok": False, "message": str(exc)}
    except VoteStorageError:
        st.session_state[DATABASE_ACTION_RESULT_KEY] = {
            "ok": False,
            "message": (
                "The database action failed. Check the connection string, TLS setting, provider "
                "network rules, role permissions, and setup authorization. No credential was retained."
            ),
        }
    except Exception:
        st.session_state[DATABASE_ACTION_RESULT_KEY] = {
            "ok": False,
            "message": "The database action failed safely. No credential was retained.",
        }
    finally:
        st.session_state[DATABASE_URL_KEY] = ""


def _render_provider_instructions(provider: str) -> bool:
    """Render setup guidance and return whether PostgreSQL actions apply."""
    if provider == "Set up with Neon":
        st.markdown(
            "1. Create a project in your Neon account.\n"
            "2. Open **Connect**, choose the database and owner role, and copy the connection string.\n"
            "3. Ensure the URL includes `sslmode=require`."
        )
        st.link_button("Open Neon", "https://console.neon.tech/")
        return True
    if provider == "Set up with Supabase":
        st.markdown(
            "1. Create a project in your Supabase account.\n"
            "2. Open **Connect** and copy the direct PostgreSQL URL for initialization.\n"
            "3. Use a session pooler for the deployed app when appropriate, and always require TLS."
        )
        st.link_button("Open Supabase", "https://supabase.com/dashboard/projects")
        return True
    if provider == "Connect existing PostgreSQL":
        st.markdown(
            "Use a dedicated PostgreSQL 14+ database. Remote URLs must use `sslmode=require`, "
            "`verify-ca`, or `verify-full`. Use separate owner and runtime roles when possible."
        )
        return True
    if provider == "Configure manually":
        st.markdown(
            "Run the generated migration with an owner role, then configure a separate "
            "least-privilege runtime role on the application host."
        )
        return True
    st.info(
        "No setup is required. SQLite is appropriate for local testing, but ephemeral hosts can "
        "lose the file and multiple app instances will not share votes."
    )
    return False


def _render_progress_checklist(uses_postgres: bool) -> None:
    st.markdown("### Setup progress")
    steps = [
        (True, "Deployment option selected"),
        (
            bool(st.session_state.get("vote_database_created")) or not uses_postgres,
            "Database created in your provider account",
        ),
        (
            bool(st.session_state.get("vote_database_connection_verified")) or not uses_postgres,
            "Database connection verified",
        ),
        (
            bool(st.session_state.get("vote_database_schema_initialized")) or not uses_postgres,
            "Schema initialized",
        ),
        (
            bool(st.session_state.get("vote_database_read_write_confirmed")) or not uses_postgres,
            "Runtime read/write access confirmed",
        ),
        (
            bool(st.session_state.get("vote_fingerprint_secret_copied")),
            "Fingerprint secret generated and copied",
        ),
        (
            bool(st.session_state.get("vote_host_configured")),
            "Application host configured with secrets",
        ),
    ]
    for complete, label in steps:
        st.markdown(f"{'[x]' if complete else '[ ]'} {label}")


def render_browser_secret_generator() -> None:
    """Generate a secret entirely inside the browser iframe."""
    st.iframe(
        """
        <div style="font:14px system-ui,sans-serif;color:#1f2937">
          <div style="font-weight:650;margin-bottom:8px">Fingerprint secret</div>
          <div id="secret" style="min-height:20px;padding:10px;border:1px solid #d1d5db;
               border-radius:8px;background:#f8fafc;overflow-wrap:anywhere;font-family:monospace">
            Generate a secret, then copy it before leaving this page.
          </div>
          <div style="display:flex;gap:8px;margin-top:9px">
            <button id="generate" style="padding:8px 12px">Generate locally</button>
            <button id="copy" style="padding:8px 12px" disabled>Copy secret</button>
          </div>
          <div style="margin-top:8px;color:#64748b;font-size:12px">
            Generated with crypto.getRandomValues. The value never leaves this browser frame.
          </div>
        </div>
        <script>
          const secret = document.getElementById('secret');
          const copy = document.getElementById('copy');
          document.getElementById('generate').addEventListener('click', () => {
            const bytes = new Uint8Array(32);
            crypto.getRandomValues(bytes);
            secret.textContent = Array.from(bytes)
              .map(byte => byte.toString(16).padStart(2, '0')).join('');
            copy.disabled = false;
          });
          copy.addEventListener('click', async () => {
            await navigator.clipboard.writeText(secret.textContent);
            copy.textContent = 'Copied';
            setTimeout(() => copy.textContent = 'Copy secret', 1500);
          });
        </script>
        """,
        height=175,
        width="stretch",
    )


def render_voting_deployment_wizard(
    bundle_data: bytes,
    bundle_name: str,
    *,
    voting_enabled: bool,
) -> None:
    with st.expander("Persistent voting storage", expanded=voting_enabled):
        st.markdown("### 1. Choose storage")
        st.caption(
            "Stop-GIS helps configure infrastructure in your own provider account. It never owns "
            "or centrally stores your research database."
        )
        provider = st.radio("Deploy your voting app", PROVIDERS, key=PROVIDER_KEY)

        st.markdown("### 2. Set up the provider")
        uses_postgres = _render_provider_instructions(provider)
        if uses_postgres:
            st.checkbox(
                "I created the database in my provider account",
                key="vote_database_created",
            )
            st.warning(
                "The URL is sent over HTTPS only to the running Stop-GIS server for the selected "
                "action. It is never written to the project or bundle and is cleared immediately."
            )
            if not _setup_actions_allowed():
                st.error(
                    "External database testing is available on localhost or to signed-in builder "
                    "users. This prevents the setup endpoint from becoming an unauthenticated network proxy."
                )
                if st.button("Sign in for database setup"):
                    try:
                        st.login()
                    except Exception:
                        st.info(
                            "Configure Streamlit OIDC or run the builder locally to use transient setup actions."
                        )
            else:
                csrf_token = _setup_csrf_token()
                with st.form("transient_vote_database_setup", clear_on_submit=True):
                    st.text_input(
                        "PostgreSQL connection string",
                        type="password",
                        key=DATABASE_URL_KEY,
                        placeholder="postgresql://user:password@host/database?sslmode=require",
                        autocomplete="off",
                    )
                    action_columns = st.columns(3)
                    action_columns[0].form_submit_button(
                        "Verify database",
                        on_click=_consume_database_url,
                        args=("verify", csrf_token),
                        width="stretch",
                    )
                    action_columns[1].form_submit_button(
                        "Initialize schema",
                        on_click=_consume_database_url,
                        args=("initialize", csrf_token),
                        type="primary",
                        width="stretch",
                    )
                    action_columns[2].form_submit_button(
                        "Confirm read/write",
                        on_click=_consume_database_url,
                        args=("confirm", csrf_token),
                        width="stretch",
                    )
                st.caption(
                    "Use an owner-role URL only for initialization. Paste the least-privilege "
                    "runtime-role URL again for read/write confirmation."
                )
            result: dict[str, Any] | None = st.session_state.pop(
                DATABASE_ACTION_RESULT_KEY, None
            )
            if result:
                (st.success if result.get("ok") else st.error)(
                    str(result.get("message") or "")
                )

        st.markdown("### 3. Generate a deployment secret")
        render_browser_secret_generator()
        st.warning(
            "Copy this value now and keep it stable. Losing or rotating it changes anonymous voter "
            "pseudonyms and can affect duplicate-vote protection."
        )
        st.checkbox(
            "I generated and securely copied the fingerprint secret",
            key="vote_fingerprint_secret_copied",
        )

        st.markdown("### 4. Configure your application host")
        st.caption(
            "Add the real database URL and fingerprint secret directly to Streamlit, Render, "
            "Railway, Fly.io, or your chosen host's secret manager."
        )
        st.download_button(
            "Download self-hosting package",
            data=bundle_data,
            file_name=bundle_name,
            mime="application/zip",
            disabled=not bool(bundle_data),
            width="stretch",
        )
        st.caption(
            "The package contains migration and verification scripts, `DEPLOYMENT.md`, and secret "
            "placeholders. It never contains the connection string or generated secret."
        )
        st.checkbox(
            "I configured both secrets directly in the application host",
            key="vote_host_configured",
        )
        _render_progress_checklist(uses_postgres)
