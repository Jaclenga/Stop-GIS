from __future__ import annotations

import copy
import hashlib
import hmac
import ipaddress
import os
import socket
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

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
    "show_detailed_counts": False,
    "results_label": "Community result",
    "minimum_votes_for_result": 10,
    "minimum_consensus_percent": 67,
    "minimum_consensus_margin": 2,
    "allow_vote_changes": False,
    "require_authentication": False,
    "abuse_protection_enabled": True,
    "enforce_network_vote_limit": True,
    "vote_cooldown_seconds": 15,
    "max_new_votes_per_hour": 10,
    "max_new_votes_per_network_per_hour": 20,
    "max_new_votes_per_stop_per_hour": 30,
}

VOTE_DATABASE_URL_ENV = "STOP_GIS_VOTE_DATABASE_URL"
VOTE_DB_PATH_ENV = "STOP_GIS_VOTE_DB_PATH"
VOTE_FINGERPRINT_SECRET_ENV = "STOP_GIS_VOTE_FINGERPRINT_SECRET"
ALLOW_PRIVATE_DATABASE_HOSTS_ENV = "STOP_GIS_ALLOW_PRIVATE_DATABASE_HOSTS"
TRUST_PROXY_HEADERS_ENV = "STOP_GIS_TRUST_PROXY_HEADERS"
DEFAULT_VOTE_DB_FILENAME = ".stop_gis_votes.sqlite3"
LEGACY_VOTE_DB_FILENAME = ".shade_gis_votes.sqlite3"
POSTGRES_CONNECT_TIMEOUT_SECONDS = 5
POSTGRES_STATEMENT_TIMEOUT_MS = 5_000
POSTGRES_LOCK_TIMEOUT_MS = 5_000
POSTGRES_POOL_MAX_SIZE = 5
POSTGRES_MIN_VERSION = 140_000
VOTE_SCHEMA_VERSION = 1
_MIGRATION_LOCK_ID = 6_426_939_476_836_841_183
_POSTGRES_POOLS: dict[str, Any] = {}
_POSTGRES_POOLS_LOCK = threading.Lock()


class VoteStorageError(RuntimeError):
    """Raised when the configured voting store cannot be used."""


class VoteRateLimitError(VoteStorageError):
    """Raised when a voter submits observations faster than the configured limits."""

    def __init__(self, message: str, *, retry_after_seconds: int = 0) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(0, int(retry_after_seconds))


@dataclass(frozen=True)
class VoterIdentity:
    """Server-derived identifiers used for vote uniqueness and abuse limits."""

    voter_id: str
    network_id: str = ""
    authenticated: bool = False


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


def _config_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default


def _config_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


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

    normalized["minimum_votes_for_result"] = _config_int(
        normalized.get("minimum_votes_for_result"), 10, 5, 100
    )
    normalized["minimum_consensus_percent"] = _config_int(
        normalized.get("minimum_consensus_percent"), 67, 51, 100
    )
    normalized["minimum_consensus_margin"] = _config_int(
        normalized.get("minimum_consensus_margin"), 2, 1, 25
    )
    for key, default in {
        "enabled": False,
        "show_results": True,
        "show_detailed_counts": False,
        "allow_vote_changes": False,
        "require_authentication": False,
        "abuse_protection_enabled": True,
        "enforce_network_vote_limit": True,
    }.items():
        normalized[key] = _config_bool(normalized.get(key), default)
    normalized["vote_cooldown_seconds"] = _config_int(
        normalized.get("vote_cooldown_seconds"), 15, 0, 300
    )
    normalized["max_new_votes_per_hour"] = _config_int(
        normalized.get("max_new_votes_per_hour"), 10, 1, 100
    )
    normalized["max_new_votes_per_network_per_hour"] = _config_int(
        normalized.get("max_new_votes_per_network_per_hour"), 20, 1, 200
    )
    normalized["max_new_votes_per_stop_per_hour"] = _config_int(
        normalized.get("max_new_votes_per_stop_per_hour"), 30, 5, 500
    )
    return normalized


def community_result(
    counts: dict[str, int],
    minimum_votes: int,
    minimum_consensus_percent: int = 51,
    minimum_consensus_margin: int = 1,
) -> dict[str, Any]:
    clean_counts = {str(label): max(0, int(count)) for label, count in counts.items()}
    total = sum(clean_counts.values())
    leaders: list[str] = []
    if clean_counts:
        highest = max(clean_counts.values())
        if highest > 0:
            leaders = [label for label, count in clean_counts.items() if count == highest]

    sorted_counts = sorted(clean_counts.values(), reverse=True)
    leading_count = sorted_counts[0] if sorted_counts else 0
    runner_up_count = sorted_counts[1] if len(sorted_counts) > 1 else 0
    leading_percent = (100 * leading_count / total) if total else 0.0
    margin = leading_count - runner_up_count

    if total < max(1, int(minimum_votes)):
        status = "pending"
        label = "More votes needed"
    elif len(leaders) != 1:
        status = "tied"
        label = "Tied"
    elif (
        leading_percent < max(51, min(100, int(minimum_consensus_percent)))
        or margin < max(1, int(minimum_consensus_margin))
    ):
        status = "contested"
        label = "No clear consensus"
    else:
        status = "consensus"
        label = leaders[0]
    return {
        "status": status,
        "label": label,
        "total": total,
        "counts": clean_counts,
        "leading_percent": leading_percent,
        "margin": margin,
    }


def _secret_or_environment(name: str) -> str:
    legacy_name = name.replace("STOP_GIS_", "SHADE_GIS_", 1)
    environment_value = str(os.environ.get(name) or os.environ.get(legacy_name, "")).strip()
    if environment_value:
        return environment_value
    try:
        return str(st.secrets.get(name) or st.secrets.get(legacy_name, "")).strip()
    except Exception:
        return ""


def _environment_value(name: str) -> str:
    legacy_name = name.replace("STOP_GIS_", "SHADE_GIS_", 1)
    return str(os.environ.get(name) or os.environ.get(legacy_name, ""))


def configured_vote_database_url() -> str:
    return _secret_or_environment(VOTE_DATABASE_URL_ENV)


def configured_vote_db_path(app_dir: Path | None = None) -> Path:
    configured = _secret_or_environment(VOTE_DB_PATH_ENV)
    if configured:
        return Path(configured).expanduser()
    directory = app_dir or Path(__file__).resolve().parent
    legacy = directory / LEGACY_VOTE_DB_FILENAME
    preferred = directory / DEFAULT_VOTE_DB_FILENAME
    return legacy if legacy.exists() and not preferred.exists() else preferred


def vote_store_label(database_url: str | None = None) -> str:
    return "PostgreSQL" if (database_url or configured_vote_database_url()).strip() else "local SQLite"


def _postgres_connection(database_url: str, *, hostaddr: str = ""):
    scheme = urlparse(database_url).scheme.lower()
    if scheme not in {"postgres", "postgresql"}:
        raise VoteStorageError(
            f"{VOTE_DATABASE_URL_ENV} must use a postgres:// or postgresql:// connection URL."
        )
    try:
        import psycopg
    except ImportError as exc:
        raise VoteStorageError("PostgreSQL voting requires the psycopg package from requirements.txt.") from exc
    connect_kwargs: dict[str, Any] = {
        "connect_timeout": POSTGRES_CONNECT_TIMEOUT_SECONDS,
        "options": (
            f"-c statement_timeout={POSTGRES_STATEMENT_TIMEOUT_MS} "
            f"-c lock_timeout={POSTGRES_LOCK_TIMEOUT_MS}"
        ),
    }
    if hostaddr:
        connect_kwargs["hostaddr"] = hostaddr
    for attempt in range(3):
        try:
            return psycopg.connect(database_url, **connect_kwargs)
        except psycopg.OperationalError as exc:
            if attempt == 2:
                raise VoteStorageError(
                    "The configured PostgreSQL voting database could not be reached."
                ) from exc
            time.sleep(0.1 * (2**attempt))
        except Exception as exc:
            raise VoteStorageError(
                "The configured PostgreSQL voting database could not be reached."
            ) from exc
    raise VoteStorageError("The configured PostgreSQL voting database could not be reached.")


class _PooledPostgresConnection:
    def __init__(self, pool: Any) -> None:
        self._pool = pool
        try:
            self._connection = pool.getconn(timeout=POSTGRES_CONNECT_TIMEOUT_SECONDS)
        except Exception as exc:
            raise VoteStorageError(
                "The configured PostgreSQL voting database could not be reached."
            ) from exc

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def close(self) -> None:
        if self._connection is not None:
            connection, self._connection = self._connection, None
            self._pool.putconn(connection)


def _postgres_pooled_connection(database_url: str, hostaddr: str):
    scheme = urlparse(database_url).scheme.lower()
    if scheme not in {"postgres", "postgresql"}:
        raise VoteStorageError(
            f"{VOTE_DATABASE_URL_ENV} must use a postgres:// or postgresql:// connection URL."
        )
    try:
        from psycopg_pool import ConnectionPool
    except ImportError as exc:
        raise VoteStorageError(
            "PostgreSQL connection pooling requires psycopg_pool from requirements.txt."
        ) from exc

    pool_key = hashlib.sha256(f"{database_url}\n{hostaddr}".encode("utf-8")).hexdigest()
    with _POSTGRES_POOLS_LOCK:
        pool = _POSTGRES_POOLS.get(pool_key)
        if pool is None:
            try:
                pool = ConnectionPool(
                    conninfo=database_url,
                    min_size=0,
                    max_size=POSTGRES_POOL_MAX_SIZE,
                    timeout=POSTGRES_CONNECT_TIMEOUT_SECONDS,
                    reconnect_timeout=POSTGRES_CONNECT_TIMEOUT_SECONDS,
                    kwargs={
                        "hostaddr": hostaddr,
                        "connect_timeout": POSTGRES_CONNECT_TIMEOUT_SECONDS,
                        "options": (
                            f"-c statement_timeout={POSTGRES_STATEMENT_TIMEOUT_MS} "
                            f"-c lock_timeout={POSTGRES_LOCK_TIMEOUT_MS}"
                        ),
                    },
                    open=True,
                )
            except Exception as exc:
                raise VoteStorageError(
                    "The configured PostgreSQL voting database pool could not be started."
                ) from exc
            _POSTGRES_POOLS[pool_key] = pool
    return _PooledPostgresConnection(pool)


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
        try:
            hostaddr = validate_vote_database_setup_url(resolved_url)
        except ValueError as exc:
            raise VoteStorageError(str(exc)) from exc
        return _postgres_pooled_connection(resolved_url, hostaddr), "postgres"
    return _sqlite_connection(sqlite_path or configured_vote_db_path()), "sqlite"


def validate_vote_database_setup_url(database_url: str) -> str:
    """Validate a transient setup URL and return a DNS-pinned public address.

    The returned address is passed to psycopg as ``hostaddr`` so validation and
    connection do not perform separate DNS resolutions. Private targets are
    rejected unless a self-hosted builder explicitly opts in.
    """
    value = str(database_url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"postgres", "postgresql"}:
        raise ValueError("Use a postgres:// or postgresql:// connection URL.")
    if not parsed.hostname or not parsed.username or not parsed.path.strip("/"):
        raise ValueError("The PostgreSQL URL must include a user, host, and database name.")

    allow_private = _config_bool(_environment_value(ALLOW_PRIVATE_DATABASE_HOSTS_ENV), False)
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    security_parameters = {
        "sslmode",
        "sslcert",
        "sslkey",
        "sslrootcert",
        "sslcrl",
        "sslcrldir",
        "sslnegotiation",
    }
    duplicate_security_parameters = {
        key.lower()
        for key, _value in query_pairs
        if key.lower() in security_parameters
        and sum(1 for candidate, _ in query_pairs if candidate.lower() == key.lower()) > 1
    }
    if duplicate_security_parameters:
        raise ValueError("PostgreSQL security parameters may appear only once in the connection URL.")
    sslmode = str(dict(query_pairs).get("sslmode", "")).strip().lower()
    if not allow_private and sslmode not in {"require", "verify-ca", "verify-full"}:
        raise ValueError("Remote PostgreSQL setup requires sslmode=require or stronger.")

    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                parsed.hostname,
                parsed.port or 5432,
                type=socket.SOCK_STREAM,
            )
        }
    except (OSError, socket.gaierror) as exc:
        raise ValueError("The PostgreSQL hostname could not be resolved.") from exc
    if not addresses:
        raise ValueError("The PostgreSQL hostname did not resolve to an address.")

    public_addresses: list[str] = []
    for address in sorted(addresses):
        try:
            parsed_address = ipaddress.ip_address(address)
        except ValueError:
            continue
        if allow_private or parsed_address.is_global:
            public_addresses.append(parsed_address.compressed)
    if not public_addresses:
        raise ValueError(
            "Private, loopback, link-local, and reserved database hosts are blocked. "
            f"Self-hosted builders may explicitly set {ALLOW_PRIVATE_DATABASE_HOSTS_ENV}=true."
        )
    return public_addresses[0]


def check_vote_database_connection(database_url: str) -> None:
    """Verify connectivity and the supported PostgreSQL version."""
    hostaddr = validate_vote_database_setup_url(database_url)
    connection = _postgres_connection(database_url, hostaddr=hostaddr)
    try:
        row = connection.execute("SELECT 1").fetchone()
        if not row or int(row[0]) != 1:
            raise VoteStorageError("The PostgreSQL connection test did not return a valid response.")
        _require_postgres_version(connection)
    except VoteStorageError:
        raise
    except Exception as exc:
        raise VoteStorageError("The PostgreSQL connection test failed.") from exc
    finally:
        connection.close()


def initialize_vote_database(database_url: str) -> None:
    """Run locked, idempotent, versioned voting-schema migrations."""
    hostaddr = validate_vote_database_setup_url(database_url)
    connection = _postgres_connection(database_url, hostaddr=hostaddr)
    try:
        _require_postgres_version(connection)
        _migrate_vote_schema(connection, "postgres")
    except Exception as exc:
        connection.rollback()
        if isinstance(exc, VoteStorageError):
            raise
        raise VoteStorageError("The voting database could not be initialized.") from exc
    finally:
        connection.close()


def confirm_vote_database_read_write(database_url: str) -> None:
    """Verify runtime-role CRUD access and always remove the probe record."""
    hostaddr = validate_vote_database_setup_url(database_url)
    connection = _postgres_connection(database_url, hostaddr=hostaddr)
    placeholder = "%s"
    probe = f"setup-probe-{uuid.uuid4().hex}"
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        _require_postgres_version(connection)
        _require_vote_schema(connection, "postgres")
        connection.execute(
            f"""
            INSERT INTO shade_votes
                (study_id, stop_id, voter_id, network_id, coverage_status, shade_sources, created_at, updated_at)
            VALUES ({placeholder}, {placeholder}, {placeholder}, '', 'No Shade', '', {placeholder}, {placeholder})
            """,
            (probe, probe, probe, timestamp, timestamp),
        )
        row = connection.execute(
            f"SELECT coverage_status FROM shade_votes WHERE study_id = {placeholder} AND stop_id = {placeholder} AND voter_id = {placeholder}",
            (probe, probe, probe),
        ).fetchone()
        if not row or str(row[0]) != "No Shade":
            raise VoteStorageError("The temporary voting record could not be read back.")
        connection.execute(
            f"UPDATE shade_votes SET coverage_status = 'Limited Shade', updated_at = {placeholder} "
            f"WHERE study_id = {placeholder} AND stop_id = {placeholder} AND voter_id = {placeholder}",
            (timestamp, probe, probe, probe),
        )
        updated_row = connection.execute(
            f"SELECT coverage_status FROM shade_votes WHERE study_id = {placeholder} "
            f"AND stop_id = {placeholder} AND voter_id = {placeholder}",
            (probe, probe, probe),
        ).fetchone()
        if not updated_row or str(updated_row[0]) != "Limited Shade":
            raise VoteStorageError("The temporary voting record could not be updated.")
        connection.execute(
            f"DELETE FROM shade_votes WHERE study_id = {placeholder} AND stop_id = {placeholder} AND voter_id = {placeholder}",
            (probe, probe, probe),
        )
        connection.commit()
    except Exception as exc:
        connection.rollback()
        if isinstance(exc, VoteStorageError):
            raise
        raise VoteStorageError(
            "The runtime database role could not insert, read, update, and delete a test vote."
        ) from exc
    finally:
        try:
            connection.execute(
                f"DELETE FROM shade_votes WHERE study_id = {placeholder} AND stop_id = {placeholder} AND voter_id = {placeholder}",
                (probe, probe, probe),
            )
            connection.commit()
        except Exception:
            connection.rollback()
        connection.close()


def _require_postgres_version(connection: Any) -> int:
    try:
        row = connection.execute("SHOW server_version_num").fetchone()
        version = int(row[0]) if row else 0
    except Exception as exc:
        raise VoteStorageError("The PostgreSQL server version could not be verified.") from exc
    if version < POSTGRES_MIN_VERSION:
        raise VoteStorageError("Stop-GIS voting requires PostgreSQL 14 or newer.")
    return version


def _schema_version(connection: Any, dialect: str) -> int | None:
    placeholder = "%s" if dialect == "postgres" else "?"
    row = connection.execute(
        f"SELECT setting_value FROM shade_vote_settings WHERE setting_key = {placeholder}",
        ("schema_version",),
    ).fetchone()
    if not row:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError) as exc:
        raise VoteStorageError("The voting schema version is invalid.") from exc


def _require_vote_schema(connection: Any, dialect: str) -> None:
    try:
        version = _schema_version(connection, dialect)
    except VoteStorageError:
        raise
    except Exception as exc:
        raise VoteStorageError(
            "The PostgreSQL voting schema is not initialized. Run the generated migration command."
        ) from exc
    if version is None:
        raise VoteStorageError(
            "The PostgreSQL voting schema is not initialized. Run the generated migration command."
        )
    if version > VOTE_SCHEMA_VERSION:
        raise VoteStorageError(
            "The database schema is newer than this app. Upgrade the generated app; automatic downgrade is refused."
        )
    if version < VOTE_SCHEMA_VERSION:
        raise VoteStorageError(
            "The database schema requires migration before voting can continue."
        )


def _migrate_vote_schema(connection: Any, dialect: str) -> None:
    if dialect == "postgres":
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_ID,))
    if dialect == "postgres":
        statement = """
            CREATE TABLE IF NOT EXISTS shade_votes (
                id BIGSERIAL PRIMARY KEY,
                study_id TEXT NOT NULL,
                stop_id TEXT NOT NULL,
                voter_id TEXT NOT NULL,
                network_id TEXT NOT NULL DEFAULT '',
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
                network_id TEXT NOT NULL DEFAULT '',
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
    existing_version = _schema_version(connection, dialect)
    if existing_version is not None and existing_version > VOTE_SCHEMA_VERSION:
        connection.rollback()
        raise VoteStorageError(
            "The database schema is newer than this app. Upgrade the generated app; automatic downgrade is refused."
        )
    if dialect == "postgres":
        connection.execute("ALTER TABLE shade_votes ADD COLUMN IF NOT EXISTS shade_sources TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE shade_votes ADD COLUMN IF NOT EXISTS network_id TEXT NOT NULL DEFAULT ''")
    else:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(shade_votes)").fetchall()}
        if "shade_sources" not in columns:
            connection.execute("ALTER TABLE shade_votes ADD COLUMN shade_sources TEXT NOT NULL DEFAULT ''")
        if "network_id" not in columns:
            connection.execute("ALTER TABLE shade_votes ADD COLUMN network_id TEXT NOT NULL DEFAULT ''")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS shade_votes_rate_limit_idx "
        "ON shade_votes (study_id, voter_id, created_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS shade_votes_network_rate_limit_idx "
        "ON shade_votes (study_id, network_id, created_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS shade_votes_stop_rate_limit_idx "
        "ON shade_votes (study_id, stop_id, created_at)"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS shade_votes_network_unique_idx "
        "ON shade_votes (study_id, stop_id, network_id) WHERE network_id <> ''"
    )
    placeholder = "%s" if dialect == "postgres" else "?"
    connection.execute(
        f"""
        INSERT INTO shade_vote_settings (setting_key, setting_value)
        VALUES ({placeholder}, {placeholder})
        ON CONFLICT (setting_key) DO UPDATE SET setting_value = excluded.setting_value
        """,
        ("schema_version", str(VOTE_SCHEMA_VERSION)),
    )
    connection.commit()


def _ensure_vote_table(connection: Any, dialect: str) -> None:
    if dialect == "postgres":
        _require_vote_schema(connection, dialect)
    else:
        _migrate_vote_schema(connection, dialect)


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
    *,
    client_ip: str | None = None,
) -> str:
    """Return a stable pseudonym without persisting an IP address or browser headers."""
    normalized_headers = {
        str(key).strip().lower(): str(value).strip()
        for key, value in dict(headers or {}).items()
        if str(value).strip()
    }
    raw_ip = str(client_ip or "").strip()
    if not raw_ip and _config_bool(_environment_value(TRUST_PROXY_HEADERS_ENV), False):
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


def privacy_preserving_network_id(
    headers: Any,
    secret: str,
    *,
    client_ip: str | None = None,
) -> str:
    """Return a keyed network pseudonym independent of mutable browser headers."""
    normalized_headers = {
        str(key).strip().lower(): str(value).strip()
        for key, value in dict(headers or {}).items()
        if str(value).strip()
    }
    raw_ip = str(client_ip or "").strip()
    if not raw_ip and _config_bool(_environment_value(TRUST_PROXY_HEADERS_ENV), False):
        raw_ip = (
            normalized_headers.get("cf-connecting-ip")
            or normalized_headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
            or normalized_headers.get("x-real-ip")
        )
    try:
        parsed_ip = ipaddress.ip_address(raw_ip)
    except ValueError:
        return ""
    # IPv6 privacy addresses commonly rotate within a /64. Treat that prefix as
    # one network so rotation does not create unlimited voting identities.
    network_scope = (
        str(ipaddress.ip_network(f"{parsed_ip}/64", strict=False))
        if parsed_ip.version == 6
        else parsed_ip.compressed
    )
    digest = hmac.new(
        secret.encode("utf-8"),
        f"network\n{network_scope}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"network_{digest}"


def privacy_preserving_account_id(user_info: Any, secret: str) -> str:
    """Return a stable keyed ID for an authenticated OIDC subject."""
    normalized = {str(key): value for key, value in dict(user_info or {}).items()}
    if not _config_bool(normalized.get("is_logged_in"), False):
        return ""
    subject = str(normalized.get("sub") or normalized.get("email") or "").strip()
    if not subject:
        return ""
    issuer = str(normalized.get("iss") or normalized.get("provider") or "default").strip()
    digest = hmac.new(
        secret.encode("utf-8"),
        f"account\n{issuer}\n{subject}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"account_{digest}"


def _request_voter_identity(
    *,
    database_url: str | None = None,
    sqlite_path: Path | None = None,
) -> VoterIdentity:
    fallback = _browser_voter_id()
    try:
        headers = st.context.headers
    except Exception:
        headers = {}
    try:
        client_ip = st.context.ip_address
    except Exception:
        client_ip = None
    try:
        user_info = st.user.to_dict()
    except Exception:
        user_info = {}

    connection, dialect = _connect(database_url, sqlite_path)
    try:
        _ensure_vote_table(connection, dialect)
        secret = _fingerprint_secret(connection, dialect)
        account_id = privacy_preserving_account_id(user_info, secret)
        if account_id:
            return VoterIdentity(account_id, authenticated=True)
        return VoterIdentity(
            privacy_preserving_voter_id(
                headers,
                secret,
                fallback,
                client_ip=client_ip,
            ),
            privacy_preserving_network_id(headers, secret, client_ip=client_ip),
            authenticated=False,
        )
    finally:
        connection.close()


def _request_voter_id(
    *,
    database_url: str | None = None,
    sqlite_path: Path | None = None,
) -> str:
    return _request_voter_identity(
        database_url=database_url,
        sqlite_path=sqlite_path,
    ).voter_id


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


def _advisory_lock_id(value: str) -> int:
    return int.from_bytes(
        hashlib.sha256(value.encode("utf-8")).digest()[:8],
        byteorder="big",
        signed=True,
    )


def _begin_vote_transaction(
    connection: Any,
    dialect: str,
    lock_scopes: list[str],
) -> None:
    """Serialize all rate checks and the following write for the relevant scopes."""
    if dialect == "sqlite":
        connection.execute("BEGIN IMMEDIATE")
        return
    for lock_id in sorted({_advisory_lock_id(scope) for scope in lock_scopes}):
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock_id,))


def save_vote(
    study_id: str,
    stop_id: str,
    voter_id: str,
    coverage_status: str,
    *,
    shade_sources: list[str] | str | None = None,
    allow_vote_changes: bool = True,
    network_id: str = "",
    enforce_network_vote_limit: bool = False,
    cooldown_seconds: int = 0,
    max_new_votes_per_hour: int | None = None,
    max_new_votes_per_network_per_hour: int | None = None,
    max_new_votes_per_stop_per_hour: int | None = None,
    database_url: str | None = None,
    sqlite_path: Path | None = None,
    now: datetime | None = None,
) -> bool:
    values = [str(value).strip() for value in (study_id, stop_id, voter_id, coverage_status)]
    if not all(values):
        raise ValueError("study_id, stop_id, voter_id, and coverage_status are required")
    study_id, stop_id, voter_id, coverage_status = values
    if len(study_id) > 256 or len(stop_id) > 256 or len(voter_id) > 256:
        raise ValueError("study_id, stop_id, and voter_id must not exceed 256 characters")
    network_id = str(network_id or "").strip()
    if len(network_id) > 256:
        raise ValueError("network_id must not exceed 256 characters")
    if not enforce_network_vote_limit:
        network_id = ""
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
        lock_scopes = [f"voter:{study_id}:{voter_id}"]
        if network_id:
            lock_scopes.append(f"network:{study_id}:{network_id}")
        if max_new_votes_per_stop_per_hour is not None:
            lock_scopes.append(f"stop:{study_id}:{stop_id}")
        _begin_vote_transaction(connection, dialect, lock_scopes)
        existing_row = connection.execute(
            f"SELECT 1 FROM shade_votes WHERE study_id = {placeholder} AND stop_id = {placeholder} AND voter_id = {placeholder}",
            (study_id, stop_id, voter_id),
        ).fetchone()
        if existing_row and not allow_vote_changes:
            connection.rollback()
            return False
        if network_id and not existing_row:
            network_vote = connection.execute(
                f"SELECT 1 FROM shade_votes WHERE study_id = {placeholder} AND stop_id = {placeholder} AND network_id = {placeholder}",
                (study_id, stop_id, network_id),
            ).fetchone()
            if network_vote:
                connection.rollback()
                return False
        cooldown_seconds = max(0, min(300, int(cooldown_seconds or 0)))
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
        if max_new_votes_per_network_per_hour is not None and network_id and not existing_row:
            network_hourly_limit = max(1, min(200, int(max_new_votes_per_network_per_hour)))
            cutoff = (timestamp_dt - timedelta(hours=1)).isoformat()
            recent_network_row = connection.execute(
                f"SELECT COUNT(*) FROM shade_votes WHERE study_id = {placeholder} AND network_id = {placeholder} AND created_at >= {placeholder}",
                (study_id, network_id, cutoff),
            ).fetchone()
            if recent_network_row and int(recent_network_row[0]) >= network_hourly_limit:
                raise VoteRateLimitError(
                    "This network has reached the hourly voting limit. Please try again later.",
                    retry_after_seconds=3600,
                )
        if max_new_votes_per_stop_per_hour is not None and not existing_row:
            stop_hourly_limit = max(5, min(500, int(max_new_votes_per_stop_per_hour)))
            cutoff = (timestamp_dt - timedelta(hours=1)).isoformat()
            recent_stop_row = connection.execute(
                f"SELECT COUNT(*) FROM shade_votes WHERE study_id = {placeholder} AND stop_id = {placeholder} AND created_at >= {placeholder}",
                (study_id, stop_id, cutoff),
            ).fetchone()
            if recent_stop_row and int(recent_stop_row[0]) >= stop_hourly_limit:
                raise VoteRateLimitError(
                    "This stop is receiving votes unusually quickly. Please try again later.",
                    retry_after_seconds=3600,
                )
        if allow_vote_changes:
            statement = f"""
                INSERT INTO shade_votes
                    (study_id, stop_id, voter_id, network_id, coverage_status, shade_sources, created_at, updated_at)
                VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                ON CONFLICT (study_id, stop_id, voter_id) DO UPDATE SET
                    network_id = excluded.network_id,
                    coverage_status = excluded.coverage_status,
                    shade_sources = excluded.shade_sources,
                    updated_at = excluded.updated_at
            """
        else:
            statement = f"""
                INSERT INTO shade_votes
                    (study_id, stop_id, voter_id, network_id, coverage_status, shade_sources, created_at, updated_at)
                VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                ON CONFLICT (study_id, stop_id, voter_id) DO NOTHING
            """
        cursor = connection.execute(
            statement,
            (
                study_id,
                stop_id,
                voter_id,
                network_id,
                coverage_status,
                serialized_sources,
                timestamp,
                timestamp,
            ),
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
        identity = VoterIdentity("")
        existing_vote = None
        if not preview:
            database_url = configured_vote_database_url()
            sqlite_path = configured_vote_db_path(app_dir)
            st.caption("Your response is stored for this study.")
            identity = (
                _request_voter_identity(database_url=database_url, sqlite_path=sqlite_path)
                if config["abuse_protection_enabled"] or config["require_authentication"]
                else VoterIdentity(_browser_voter_id())
            )
            if config["require_authentication"] and not identity.authenticated:
                st.info("Sign in with an approved account before voting.")
                if st.button("Sign in to vote", key=f"public_vote_login_{key_token}"):
                    try:
                        st.login()
                    except Exception:
                        st.error(
                            "Authentication is not configured for this deployment. "
                            "The app administrator must configure Streamlit OIDC secrets."
                        )
                return
            voter_id = identity.voter_id
            existing_vote = get_existing_vote_details(
                study_id,
                stop_id,
                voter_id,
                database_url=database_url,
                sqlite_path=sqlite_path,
            )
        existing_coverage = str(existing_vote["coverage_status"]) if existing_vote else ""
        existing_sources = existing_vote["shade_sources"] if existing_vote else []
        # Do not preselect a response for a new voter. A default choice biases the
        # study and makes an accidental one-click submission possible.
        default_index = (
            options.index(existing_coverage)
            if existing_coverage in options
            else (0 if preview else None)
        )
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
        coverage_selected = selected_status in options
        changes_disabled = preview or bool(
            existing_vote and not config["allow_vote_changes"]
        )
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
        if not coverage_selected:
            st.caption("Choose a shade coverage option to continue.")
        elif selected_status == "No Shade":
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
        sources_required = bool(
            coverage_selected
            and selected_status != "No Shade"
            and not selected_sources
            and not changes_disabled
        )
        if sources_required:
            st.caption("Select at least one shade source to submit this response.")
        submitted = st.button(
            str(config["submit_label"]),
            key=f"public_vote_submit_{key_token}",
            type="primary",
            disabled=changes_disabled or not coverage_selected or sources_required,
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
                network_id=identity.network_id,
                enforce_network_vote_limit=(
                    config["abuse_protection_enabled"]
                    and config["enforce_network_vote_limit"]
                    and not identity.authenticated
                ),
                cooldown_seconds=(
                    config["vote_cooldown_seconds"] if config["abuse_protection_enabled"] else 0
                ),
                max_new_votes_per_hour=(
                    config["max_new_votes_per_hour"] if config["abuse_protection_enabled"] else None
                ),
                max_new_votes_per_network_per_hour=(
                    config["max_new_votes_per_network_per_hour"]
                    if config["abuse_protection_enabled"]
                    and config["enforce_network_vote_limit"]
                    and not identity.authenticated
                    else None
                ),
                max_new_votes_per_stop_per_hour=(
                    config["max_new_votes_per_stop_per_hour"]
                    if config["abuse_protection_enabled"]
                    else None
                ),
                database_url=database_url,
                sqlite_path=sqlite_path,
            )
            if saved:
                st.success(str(config["success_message"]))
            else:
                st.info("A vote associated with this visitor or network has already been recorded for this stop.")
        elif changes_disabled and not preview:
            st.caption("A vote associated with this visitor has already been recorded for this stop.")

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
                result = community_result(
                    counts,
                    config["minimum_votes_for_result"],
                    config["minimum_consensus_percent"],
                    config["minimum_consensus_margin"],
                )
                st.markdown(f"**{config['results_label']}: {result['label']}**")
                if config["show_detailed_counts"] and result["status"] != "pending":
                    st.caption(" | ".join(f"{label}: {counts[label]}" for label in options))
                if result["status"] == "pending":
                    st.caption(
                        f"Totals remain hidden until at least {config['minimum_votes_for_result']} "
                        "verified votes have been recorded."
                    )
                elif result["status"] == "contested":
                    st.caption(
                        "The leading choice has not met the configured consensus share and margin."
                    )
    except VoteRateLimitError as exc:
        st.warning(str(exc))
    except (VoteStorageError, OSError, sqlite3.Error):
        st.error("Voting is temporarily unavailable. Your response was not submitted.")
        st.button("Try again", key=f"public_vote_retry_{key_token}", on_click=st.rerun)
