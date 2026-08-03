from __future__ import annotations

import copy
import hashlib
import hmac
import ipaddress
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import streamlit as st


PUBLIC_COVERAGE_OPTIONS = ["No Shade", "Limited Shade", "Significant Shade"]
PUBLIC_COVERAGE_DEFINITIONS = {
    "No Shade": "No shade visibly reaches the waiting area.",
    "Limited Shade": "Shade visibly reaches part of the waiting area, but does not cover most of it.",
    "Significant Shade": "Shade visibly covers most of the waiting area or seating area.",
}
PUBLIC_SOURCE_OPTIONS = ["Natural", "Purpose-built", "Incidental"]
PUBLIC_SOURCE_DISPLAY_LABELS = {
    "Natural": "Trees / vegetation",
    "Purpose-built": "Bus shelter / shade structure",
    "Incidental": "Nearby buildings or other structures",
}
PUBLIC_SOURCE_DEFINITIONS = {
    "Natural": "Trees, palms, hedges, or other vegetation visibly shade the waiting area.",
    "Purpose-built": (
        "A designated, purpose-built bus shelter, awning, canopy, overhang, or similar passenger shelter "
        "visibly shades the waiting area."
    ),
    "Incidental": "A nearby building or other non-shelter built feature visibly shades the waiting area.",
}
_PUBLIC_COVERAGE_ALIASES = {
    "no shade": "No Shade",
    "limited": "Limited Shade",
    "limited shade": "Limited Shade",
    "limited natural shade": "Limited Shade",
    "significant": "Significant Shade",
    "significant shade": "Significant Shade",
    "significant natural shade": "Significant Shade",
}
_PUBLIC_SOURCE_ALIASES = {
    "natural": "Natural",
    "natural shade": "Natural",
    "tree": "Natural",
    "trees": "Natural",
    "vegetation": "Natural",
    "trees / vegetation": "Natural",
    "purpose-built": "Purpose-built",
    "purpose built": "Purpose-built",
    "purpose-built shade": "Purpose-built",
    "constructed": "Purpose-built",
    "constructed shade": "Purpose-built",
    "intentional built": "Purpose-built",
    "intentional built shade": "Purpose-built",
    "shelter": "Purpose-built",
    "canopy": "Purpose-built",
    "bus shelter / shade structure": "Purpose-built",
    "incidental": "Incidental",
    "incidental shade": "Incidental",
    "manmade": "Incidental",
    "manmade shade": "Incidental",
    "incidental built": "Incidental",
    "incidental built shade": "Incidental",
    "building": "Incidental",
    "nearby buildings or other structures": "Incidental",
}


DEFAULT_VOTING_DESCRIPTION = (
    "Choose the shade coverage that best matches this stop. "
    "Answer based on where a passenger would normally wait for the bus."
)

DEFAULT_VOTING_CONFIG = {
    "enabled": False,
    "title": "Help document this stop",
    "description": DEFAULT_VOTING_DESCRIPTION,
    "question": "What is the current shade coverage at this stop?",
    "options": PUBLIC_COVERAGE_OPTIONS,
    "source_question": "What creates the shade at this stop? Select all that apply.",
    "submit_label": "Submit vote",
    "success_message": "Thank you. Your observation has been recorded.",
    "show_results": True,
    "results_label": "Community result",
    "minimum_votes_for_result": 5,
    "allow_vote_changes": True,
    "abuse_protection_enabled": True,
    "vote_cooldown_seconds": 5,
    "max_new_votes_per_hour": 20,
}

VOTE_DATABASE_URL_ENV = "SHADE_GIS_VOTE_DATABASE_URL"
VOTE_DB_PATH_ENV = "SHADE_GIS_VOTE_DB_PATH"
VOTE_FINGERPRINT_SECRET_ENV = "SHADE_GIS_VOTE_FINGERPRINT_SECRET"
DEFAULT_VOTE_DB_FILENAME = ".shade_gis_votes.sqlite3"


class VoteStorageError(RuntimeError):
    """Raised when the configured voting store cannot be used."""


class VoteRateLimitError(VoteStorageError):
    """Raised when a voter submits observations faster than the configured limits."""

    def __init__(self, message: str, *, retry_after_seconds: int = 0) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(0, int(retry_after_seconds))


def normalize_vote_sources(value: Any) -> list[str]:
    raw_values = value if isinstance(value, (list, tuple, set)) else str(value or "").replace("|", ";").split(";")
    sources: list[str] = []
    for raw_value in raw_values:
        source = _PUBLIC_SOURCE_ALIASES.get(str(raw_value).strip().lower(), "")
        if source and source not in sources:
            sources.append(source)
    return sources


def taxonomy_help_text(options: list[str], definitions: dict[str, str]) -> str:
    return "\n\n".join(
        f"**{option}:** {definitions[option]}"
        for option in options
        if option in definitions and definitions[option]
    )


def source_taxonomy_help(taxonomy: list[dict[str, Any]] | None = None) -> str:
    definitions = copy.deepcopy(PUBLIC_SOURCE_DEFINITIONS)
    for category in taxonomy or []:
        if not isinstance(category, dict):
            continue
        canonical = _PUBLIC_SOURCE_ALIASES.get(
            str(category.get("code") or category.get("shade_source") or category.get("name") or "").strip().lower(),
            "",
        )
        description = str(
            category.get("operational_definition") or category.get("description") or ""
        ).strip()
        if canonical and description:
            definitions[canonical] = description
    return taxonomy_help_text(PUBLIC_SOURCE_OPTIONS, definitions)


def source_display_labels(taxonomy: list[dict[str, Any]] | None = None) -> dict[str, str]:
    labels = copy.deepcopy(PUBLIC_SOURCE_DISPLAY_LABELS)
    for category in taxonomy or []:
        if not isinstance(category, dict):
            continue
        canonical = _PUBLIC_SOURCE_ALIASES.get(str(category.get("code") or "").strip().lower(), "")
        display_label = str(category.get("shade_source") or "").strip()
        if canonical and display_label:
            labels[canonical] = display_label
    return labels


def coverage_display_labels(taxonomy: list[dict[str, Any]] | None = None) -> dict[str, str]:
    labels = {option: option for option in PUBLIC_COVERAGE_OPTIONS}
    for category in taxonomy or []:
        if not isinstance(category, dict):
            continue
        canonical = _PUBLIC_COVERAGE_ALIASES.get(str(category.get("code") or "").strip().lower(), "")
        display_label = str(category.get("shade_coverage") or "").strip()
        if canonical and display_label:
            labels[canonical] = display_label
    return labels


def coverage_taxonomy_help(
    options: list[str],
    taxonomy: list[dict[str, Any]] | None = None,
) -> str:
    definitions = copy.deepcopy(PUBLIC_COVERAGE_DEFINITIONS)
    for category in taxonomy or []:
        if not isinstance(category, dict):
            continue
        raw_name = category.get("code") or category.get("shade_coverage") or category.get("name")
        canonical = _PUBLIC_COVERAGE_ALIASES.get(str(raw_name or "").strip().lower(), "")
        description = str(
            category.get("operational_definition") or category.get("description") or ""
        ).strip()
        if canonical and description:
            definitions[canonical] = description
    return taxonomy_help_text(options, definitions)


def normalize_voting_config(
    voting: dict[str, Any] | None,
    taxonomy: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized = copy.deepcopy(DEFAULT_VOTING_CONFIG)
    if isinstance(voting, dict):
        normalized.update(voting)
    if str(normalized.get("description") or "").strip() in {
        (
            "Use your current observation of the passenger waiting area. "
            "Choose the shade coverage that best matches this stop."
        ),
        (
            "Use your current observation of the passenger waiting area. "
            "Choose the shade coverage that best matches this stop. "
            "Answer based on where a passenger would normally wait for the bus right now."
        ),
    }:
        normalized["description"] = DEFAULT_VOTING_CONFIG["description"]

    configured_options = []
    for option in normalized.get("options", []):
        canonical = _PUBLIC_COVERAGE_ALIASES.get(str(option).strip().lower(), "")
        if canonical and canonical not in configured_options:
            configured_options.append(canonical)
    normalized["options"] = configured_options or list(PUBLIC_COVERAGE_OPTIONS)
    normalized["source_question"] = str(normalized.get("source_question") or DEFAULT_VOTING_CONFIG["source_question"])

    try:
        minimum_votes = int(normalized.get("minimum_votes_for_result", 5))
    except (TypeError, ValueError):
        minimum_votes = 5
    normalized["minimum_votes_for_result"] = max(1, min(100, minimum_votes))
    normalized["enabled"] = bool(normalized.get("enabled", False))
    normalized["show_results"] = bool(normalized.get("show_results", True))
    normalized["allow_vote_changes"] = bool(normalized.get("allow_vote_changes", True))
    normalized["abuse_protection_enabled"] = bool(normalized.get("abuse_protection_enabled", True))
    try:
        cooldown = int(normalized.get("vote_cooldown_seconds", 5))
    except (TypeError, ValueError):
        cooldown = 5
    normalized["vote_cooldown_seconds"] = max(0, min(60, cooldown))
    try:
        hourly_limit = int(normalized.get("max_new_votes_per_hour", 20))
    except (TypeError, ValueError):
        hourly_limit = 20
    normalized["max_new_votes_per_hour"] = max(1, min(100, hourly_limit))
    return normalized


def community_result(counts: dict[str, int], minimum_votes: int) -> dict[str, Any]:
    clean_counts = {str(label): max(0, int(count)) for label, count in counts.items()}
    total = sum(clean_counts.values())
    leaders: list[str] = []
    if clean_counts:
        highest = max(clean_counts.values())
        if highest > 0:
            leaders = [label for label, count in clean_counts.items() if count == highest]

    if total < max(1, int(minimum_votes)):
        status = "pending"
        label = "More votes needed"
    elif len(leaders) != 1:
        status = "tied"
        label = "Tied"
    else:
        status = "consensus"
        label = leaders[0]
    return {"status": status, "label": label, "total": total, "counts": clean_counts}


def _secret_or_environment(name: str) -> str:
    environment_value = str(os.environ.get(name, "")).strip()
    if environment_value:
        return environment_value
    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def configured_vote_database_url() -> str:
    return _secret_or_environment(VOTE_DATABASE_URL_ENV)


def configured_vote_db_path(app_dir: Path | None = None) -> Path:
    configured = _secret_or_environment(VOTE_DB_PATH_ENV)
    if configured:
        return Path(configured).expanduser()
    return (app_dir or Path(__file__).resolve().parent) / DEFAULT_VOTE_DB_FILENAME


def vote_store_label(database_url: str | None = None) -> str:
    return "PostgreSQL" if (database_url or configured_vote_database_url()).strip() else "local SQLite"


def _postgres_connection(database_url: str):
    scheme = urlparse(database_url).scheme.lower()
    if scheme not in {"postgres", "postgresql"}:
        raise VoteStorageError(
            f"{VOTE_DATABASE_URL_ENV} must use a postgres:// or postgresql:// connection URL."
        )
    try:
        import psycopg
    except ImportError as exc:
        raise VoteStorageError("PostgreSQL voting requires the psycopg package from requirements.txt.") from exc
    try:
        return psycopg.connect(database_url)
    except Exception as exc:
        raise VoteStorageError("The configured PostgreSQL voting database could not be reached.") from exc


def _sqlite_connection(path: Path):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=30)
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection
    except (OSError, sqlite3.Error) as exc:
        raise VoteStorageError(f"The local voting database is not writable: {path}") from exc


def _connect(database_url: str | None = None, sqlite_path: Path | None = None):
    resolved_url = (database_url if database_url is not None else configured_vote_database_url()).strip()
    if resolved_url:
        return _postgres_connection(resolved_url), "postgres"
    return _sqlite_connection(sqlite_path or configured_vote_db_path()), "sqlite"


def _ensure_vote_table(connection: Any, dialect: str) -> None:
    if dialect == "postgres":
        statement = """
            CREATE TABLE IF NOT EXISTS shade_votes (
                id BIGSERIAL PRIMARY KEY,
                study_id TEXT NOT NULL,
                stop_id TEXT NOT NULL,
                voter_id TEXT NOT NULL,
                coverage_status TEXT NOT NULL,
                shade_sources TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                UNIQUE (study_id, stop_id, voter_id)
            )
        """
    else:
        statement = """
            CREATE TABLE IF NOT EXISTS shade_votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                study_id TEXT NOT NULL,
                stop_id TEXT NOT NULL,
                voter_id TEXT NOT NULL,
                coverage_status TEXT NOT NULL,
                shade_sources TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (study_id, stop_id, voter_id)
            )
        """
    connection.execute(statement)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS shade_vote_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS shade_votes_rate_limit_idx "
        "ON shade_votes (study_id, voter_id, created_at)"
    )
    if dialect == "postgres":
        connection.execute("ALTER TABLE shade_votes ADD COLUMN IF NOT EXISTS shade_sources TEXT NOT NULL DEFAULT ''")
    else:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(shade_votes)").fetchall()}
        if "shade_sources" not in columns:
            connection.execute("ALTER TABLE shade_votes ADD COLUMN shade_sources TEXT NOT NULL DEFAULT ''")
    connection.commit()


def _fingerprint_secret(connection: Any, dialect: str) -> str:
    configured = _secret_or_environment(VOTE_FINGERPRINT_SECRET_ENV)
    if configured:
        return configured
    placeholder = "%s" if dialect == "postgres" else "?"
    generated = uuid.uuid4().hex + uuid.uuid4().hex
    connection.execute(
        f"""
        INSERT INTO shade_vote_settings (setting_key, setting_value)
        VALUES ({placeholder}, {placeholder})
        ON CONFLICT (setting_key) DO NOTHING
        """,
        ("fingerprint_secret", generated),
    )
    row = connection.execute(
        f"SELECT setting_value FROM shade_vote_settings WHERE setting_key = {placeholder}",
        ("fingerprint_secret",),
    ).fetchone()
    connection.commit()
    if not row or not str(row[0]).strip():
        raise VoteStorageError("The voter privacy key could not be initialized.")
    return str(row[0]).strip()


def privacy_preserving_voter_id(
    headers: Any,
    secret: str,
    fallback_voter_id: str,
) -> str:
    """Return a stable pseudonym without persisting an IP address or browser headers."""
    normalized_headers = {
        str(key).strip().lower(): str(value).strip()
        for key, value in dict(headers or {}).items()
        if str(value).strip()
    }
    raw_ip = (
        normalized_headers.get("cf-connecting-ip")
        or normalized_headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        or normalized_headers.get("x-real-ip")
    )
    try:
        client_ip = ipaddress.ip_address(raw_ip).compressed
    except ValueError:
        return fallback_voter_id
    user_agent = normalized_headers.get("user-agent", "")[:512]
    accept_language = normalized_headers.get("accept-language", "")[:128]
    if not user_agent:
        return fallback_voter_id
    payload = "\n".join((client_ip, user_agent, accept_language)).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"visitor_{digest}"


def _request_voter_id(
    *,
    database_url: str | None = None,
    sqlite_path: Path | None = None,
) -> str:
    fallback = _browser_voter_id()
    try:
        headers = st.context.headers
    except Exception:
        return fallback
    connection, dialect = _connect(database_url, sqlite_path)
    try:
        _ensure_vote_table(connection, dialect)
        return privacy_preserving_voter_id(headers, _fingerprint_secret(connection, dialect), fallback)
    finally:
        connection.close()


def _as_utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_existing_vote(
    study_id: str,
    stop_id: str,
    voter_id: str,
    *,
    database_url: str | None = None,
    sqlite_path: Path | None = None,
) -> str | None:
    vote = get_existing_vote_details(
        study_id,
        stop_id,
        voter_id,
        database_url=database_url,
        sqlite_path=sqlite_path,
    )
    return str(vote["coverage_status"]) if vote else None


def get_existing_vote_details(
    study_id: str,
    stop_id: str,
    voter_id: str,
    *,
    database_url: str | None = None,
    sqlite_path: Path | None = None,
) -> dict[str, Any] | None:
    connection, dialect = _connect(database_url, sqlite_path)
    placeholder = "%s" if dialect == "postgres" else "?"
    try:
        _ensure_vote_table(connection, dialect)
        row = connection.execute(
            f"SELECT coverage_status, shade_sources FROM shade_votes WHERE study_id = {placeholder} AND stop_id = {placeholder} AND voter_id = {placeholder}",
            (study_id, stop_id, voter_id),
        ).fetchone()
        if not row:
            return None
        return {
            "coverage_status": _PUBLIC_COVERAGE_ALIASES.get(str(row[0]).strip().lower(), str(row[0])),
            "shade_sources": normalize_vote_sources(row[1]),
        }
    except Exception as exc:
        if isinstance(exc, VoteStorageError):
            raise
        raise VoteStorageError("The existing vote could not be read.") from exc
    finally:
        connection.close()


def save_vote(
    study_id: str,
    stop_id: str,
    voter_id: str,
    coverage_status: str,
    *,
    shade_sources: list[str] | str | None = None,
    allow_vote_changes: bool = True,
    cooldown_seconds: int = 0,
    max_new_votes_per_hour: int | None = None,
    database_url: str | None = None,
    sqlite_path: Path | None = None,
    now: datetime | None = None,
) -> bool:
    values = [str(value).strip() for value in (study_id, stop_id, voter_id, coverage_status)]
    if not all(values):
        raise ValueError("study_id, stop_id, voter_id, and coverage_status are required")
    study_id, stop_id, voter_id, coverage_status = values
    coverage_status = _PUBLIC_COVERAGE_ALIASES.get(coverage_status.lower(), "")
    if not coverage_status:
        raise ValueError(f"coverage_status must be one of: {', '.join(PUBLIC_COVERAGE_OPTIONS)}")
    normalized_sources = [] if coverage_status == "No Shade" else normalize_vote_sources(shade_sources)
    serialized_sources = "; ".join(normalized_sources)
    timestamp_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    timestamp = timestamp_dt.isoformat()
    connection, dialect = _connect(database_url, sqlite_path)
    placeholder = "%s" if dialect == "postgres" else "?"
    try:
        _ensure_vote_table(connection, dialect)
        existing_row = connection.execute(
            f"SELECT 1 FROM shade_votes WHERE study_id = {placeholder} AND stop_id = {placeholder} AND voter_id = {placeholder}",
            (study_id, stop_id, voter_id),
        ).fetchone()
        cooldown_seconds = max(0, min(60, int(cooldown_seconds or 0)))
        if cooldown_seconds:
            latest_row = connection.execute(
                f"SELECT MAX(updated_at) FROM shade_votes WHERE study_id = {placeholder} AND voter_id = {placeholder}",
                (study_id, voter_id),
            ).fetchone()
            latest_vote_at = _as_utc_datetime(latest_row[0]) if latest_row and latest_row[0] else None
            if latest_vote_at:
                elapsed = (timestamp_dt - latest_vote_at).total_seconds()
                if elapsed < cooldown_seconds:
                    retry_after = max(1, int(cooldown_seconds - elapsed + 0.999))
                    raise VoteRateLimitError(
                        f"Please wait {retry_after} second{'s' if retry_after != 1 else ''} before submitting another vote.",
                        retry_after_seconds=retry_after,
                    )
        if max_new_votes_per_hour is not None and not existing_row:
            hourly_limit = max(1, min(100, int(max_new_votes_per_hour)))
            cutoff = (timestamp_dt - timedelta(hours=1)).isoformat()
            recent_row = connection.execute(
                f"SELECT COUNT(*) FROM shade_votes WHERE study_id = {placeholder} AND voter_id = {placeholder} AND created_at >= {placeholder}",
                (study_id, voter_id, cutoff),
            ).fetchone()
            if recent_row and int(recent_row[0]) >= hourly_limit:
                raise VoteRateLimitError(
                    "This visitor has reached the hourly voting limit. Please try again later.",
                    retry_after_seconds=3600,
                )
        if allow_vote_changes:
            statement = f"""
                INSERT INTO shade_votes
                    (study_id, stop_id, voter_id, coverage_status, shade_sources, created_at, updated_at)
                VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                ON CONFLICT (study_id, stop_id, voter_id) DO UPDATE SET
                    coverage_status = excluded.coverage_status,
                    shade_sources = excluded.shade_sources,
                    updated_at = excluded.updated_at
            """
        else:
            statement = f"""
                INSERT INTO shade_votes
                    (study_id, stop_id, voter_id, coverage_status, shade_sources, created_at, updated_at)
                VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                ON CONFLICT (study_id, stop_id, voter_id) DO NOTHING
            """
        cursor = connection.execute(
            statement,
            (study_id, stop_id, voter_id, coverage_status, serialized_sources, timestamp, timestamp),
        )
        connection.commit()
        return cursor.rowcount != 0
    except Exception as exc:
        connection.rollback()
        if isinstance(exc, VoteStorageError):
            raise
        raise VoteStorageError("The vote could not be saved.") from exc
    finally:
        connection.close()


def get_vote_counts(
    study_id: str,
    stop_id: str,
    options: list[str],
    *,
    database_url: str | None = None,
    sqlite_path: Path | None = None,
) -> dict[str, int]:
    counts = {str(option): 0 for option in options}
    connection, dialect = _connect(database_url, sqlite_path)
    placeholder = "%s" if dialect == "postgres" else "?"
    try:
        _ensure_vote_table(connection, dialect)
        rows = connection.execute(
            f"SELECT coverage_status, COUNT(*) FROM shade_votes WHERE study_id = {placeholder} AND stop_id = {placeholder} GROUP BY coverage_status",
            (study_id, stop_id),
        ).fetchall()
        for status, count in rows:
            status = _PUBLIC_COVERAGE_ALIASES.get(str(status).strip().lower(), str(status))
            if status in counts:
                counts[status] += int(count)
        return counts
    except Exception as exc:
        if isinstance(exc, VoteStorageError):
            raise
        raise VoteStorageError("Community vote totals could not be read.") from exc
    finally:
        connection.close()


def _browser_voter_id() -> str:
    key = "_shade_gis_browser_voter_id"
    if key not in st.session_state:
        st.session_state[key] = uuid.uuid4().hex
    return str(st.session_state[key])


def render_voting_panel(
    selected_stop: Any,
    study_id: str,
    taxonomy: list[dict[str, Any]],
    voting: dict[str, Any] | None,
    *,
    app_dir: Path | None = None,
    preview: bool = False,
) -> None:
    config = normalize_voting_config(voting, taxonomy)
    if (not config["enabled"] and not preview) or selected_stop is None:
        return

    stop_id = str(selected_stop.get("stop_id", "")).strip()
    if not stop_id and not preview:
        st.warning("This stop has no ID, so voting is unavailable.")
        return
    if not stop_id:
        stop_id = "preview"
    options = config["options"]
    if not options:
        st.warning("Voting is enabled, but the project has no public coverage options.")
        return

    st.markdown(f"#### {config['title']}")
    if config.get("description"):
        st.markdown(str(config["description"]))

    key_token = hashlib.sha256(
        f"{study_id}:{stop_id}:{'preview' if preview else 'live'}".encode("utf-8")
    ).hexdigest()[:16]
    try:
        database_url = None
        sqlite_path = None
        voter_id = ""
        existing_vote = None
        if not preview:
            database_url = configured_vote_database_url()
            sqlite_path = configured_vote_db_path(app_dir)
            voter_id = (
                _request_voter_id(database_url=database_url, sqlite_path=sqlite_path)
                if config["abuse_protection_enabled"]
                else _browser_voter_id()
            )
            existing_vote = get_existing_vote_details(
                study_id,
                stop_id,
                voter_id,
                database_url=database_url,
                sqlite_path=sqlite_path,
            )
        existing_coverage = str(existing_vote["coverage_status"]) if existing_vote else ""
        existing_sources = existing_vote["shade_sources"] if existing_vote else []
        default_index = options.index(existing_coverage) if existing_coverage in options else 0
        st.markdown(
            f"**{config['question']}**",
            help=coverage_taxonomy_help(options, taxonomy),
        )
        selected_status = st.radio(
            str(config["question"]),
            options,
            index=default_index,
            key=f"public_vote_choice_{key_token}",
            disabled=preview,
            label_visibility="collapsed",
            format_func=lambda option: coverage_display_labels(
                config.get("shade_coverage_taxonomy")
            ).get(option, option),
        )
        changes_disabled = preview or bool(existing_vote and not config["allow_vote_changes"])
        source_keys = {
            source: f"public_vote_source_{key_token}_{source.lower()}" for source in PUBLIC_SOURCE_OPTIONS
        }
        if selected_status == "No Shade" and not preview:
            for source_key in source_keys.values():
                st.session_state[source_key] = False
        st.divider()
        st.markdown(
            f"**{config['source_question']}**",
            help=source_taxonomy_help(config.get("shade_source_taxonomy")),
        )
        selected_sources = []
        if selected_status == "No Shade":
            st.caption("No shade source is needed when **No Shade** is selected.")
        else:
            for source in PUBLIC_SOURCE_OPTIONS:
                checkbox_args: dict[str, Any] = {
                    "key": source_keys[source],
                    "disabled": changes_disabled,
                }
                if source_keys[source] not in st.session_state:
                    checkbox_args["value"] = source in existing_sources
                if st.checkbox(
                    source_display_labels(config.get("shade_source_taxonomy")).get(source, source),
                    **checkbox_args,
                ):
                    selected_sources.append(source)
        submitted = st.button(
            str(config["submit_label"]),
            key=f"public_vote_submit_{key_token}",
            type="primary",
            disabled=changes_disabled,
            width="stretch",
        )
        if submitted and not preview:
            saved = save_vote(
                study_id,
                stop_id,
                voter_id,
                selected_status,
                shade_sources=selected_sources,
                allow_vote_changes=config["allow_vote_changes"],
                cooldown_seconds=(
                    config["vote_cooldown_seconds"] if config["abuse_protection_enabled"] else 0
                ),
                max_new_votes_per_hour=(
                    config["max_new_votes_per_hour"] if config["abuse_protection_enabled"] else None
                ),
                database_url=database_url,
                sqlite_path=sqlite_path,
            )
            if saved:
                st.success(str(config["success_message"]))
            else:
                st.info("A vote from this browser session has already been recorded for this stop.")
        elif changes_disabled and not preview:
            st.caption("A vote from this browser session has already been recorded for this stop.")

        if config["show_results"]:
            if preview:
                st.markdown(f"**{config['results_label']}: More votes needed**")
                st.caption("Vote totals appear here in the deployed app.")
            else:
                counts = get_vote_counts(
                    study_id,
                    stop_id,
                    options,
                    database_url=database_url,
                    sqlite_path=sqlite_path,
                )
                result = community_result(counts, config["minimum_votes_for_result"])
                st.markdown(f"**{config['results_label']}: {result['label']}**")
                st.caption(" | ".join(f"{label}: {counts[label]}" for label in options))
                if result["status"] == "pending":
                    remaining = config["minimum_votes_for_result"] - result["total"]
                    st.caption(f"{remaining} more vote{'s' if remaining != 1 else ''} needed before a result is reported.")
    except VoteRateLimitError as exc:
        st.warning(str(exc))
    except (VoteStorageError, OSError, sqlite3.Error) as exc:
        st.error(f"Voting storage is unavailable. {exc}")
