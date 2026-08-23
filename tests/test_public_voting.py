from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import published_app
import public_voting
from public_voting import (
    DEFAULT_VOTING_CONFIG,
    DEFAULT_VOTING_DESCRIPTION,
    PUBLIC_COVERAGE_DEFINITIONS,
    PUBLIC_SOURCE_DISPLAY_LABELS,
    PUBLIC_SOURCE_DEFINITIONS,
    VoteRateLimitError,
    community_result,
    coverage_display_labels,
    coverage_taxonomy_help,
    get_existing_vote,
    get_existing_vote_details,
    get_vote_counts,
    normalize_voting_config,
    normalize_vote_sources,
    privacy_preserving_account_id,
    privacy_preserving_network_id,
    privacy_preserving_voter_id,
    save_vote,
    source_display_labels,
    source_taxonomy_help,
    validate_vote_database_setup_url,
)


def test_default_source_question_uses_plain_language_checkbox_copy():
    assert DEFAULT_VOTING_CONFIG["source_question"] == (
        "What creates the shade at this stop? Select all that apply."
    )
    assert PUBLIC_SOURCE_DISPLAY_LABELS == {
        "Natural": "Trees / vegetation",
        "Purpose-built": "Bus shelter / shade structure",
        "Incidental": "Nearby buildings or other structures",
    }
    assert normalize_vote_sources(list(PUBLIC_SOURCE_DISPLAY_LABELS.values())) == [
        "Natural",
        "Purpose-built",
        "Incidental",
    ]


def test_project_taxonomy_labels_replace_visible_voting_copy_without_changing_codes():
    assert source_display_labels(
        [{"code": "Natural", "shade_source": "Vegetation"}]
    )["Natural"] == "Vegetation"
    assert coverage_display_labels(
        [{"code": "Limited Shade", "shade_coverage": "Partial Shade"}]
    )["Limited Shade"] == "Partial Shade"


def test_default_voting_instructions_add_waiting_area_guidance_without_overwriting_custom_copy():
    assert DEFAULT_VOTING_CONFIG["description"] == DEFAULT_VOTING_DESCRIPTION
    assert DEFAULT_VOTING_DESCRIPTION == (
        "Choose the shade coverage that best matches this stop. "
        "Answer based on where a passenger would normally wait for the bus."
    )
    assert normalize_voting_config(
        {
            "description": (
                "Use your current observation of the passenger waiting area. "
                "Choose the shade coverage that best matches this stop."
            )
        }
    )["description"] == DEFAULT_VOTING_CONFIG["description"]
    assert normalize_voting_config(
        {
            "description": (
                "Use your current observation of the passenger waiting area. "
                "Choose the shade coverage that best matches this stop. "
                "Answer based on where a passenger would normally wait for the bus right now."
            )
        }
    )["description"] == DEFAULT_VOTING_CONFIG["description"]
    assert normalize_voting_config(
        {"description": "Use the locally approved observation protocol."}
    )["description"] == "Use the locally approved observation protocol."


def test_shared_stop_panel_renders_voting_for_the_selected_stop(monkeypatch):
    selected_stop = {"stop_id": "1001", "stop_name": "Main Street"}
    captured = {}

    class FakeTab:
        def __init__(self, open_state):
            self.open = open_state

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeStreamlit:
        @staticmethod
        def tabs(labels, **kwargs):
            captured["tabs"] = {"labels": labels, **kwargs}
            return [FakeTab(True), FakeTab(False)]

    monkeypatch.setattr(published_app, "st", FakeStreamlit)

    monkeypatch.setattr(
        published_app,
        "render_stop_detail_workflow",
        lambda stops, visualization, state_prefix, *, show_details, show_selection_summary: (
            captured.update(
                show_details=show_details,
                show_selection_summary=show_selection_summary,
            )
            or selected_stop
        ),
    )

    def capture_voting(stop, study_id, taxonomy, voting, *, app_dir):
        captured.update(
            stop=stop,
            study_id=study_id,
            taxonomy=taxonomy,
            voting=voting,
            app_dir=app_dir,
        )

    monkeypatch.setattr(published_app, "render_voting_panel", capture_voting)
    voting = {"enabled": True}
    taxonomy = [{"name": "No Shade"}]

    result = published_app.render_stop_and_voting_panel(
        pd.DataFrame([selected_stop]),
        {},
        "preview",
        "study-a",
        taxonomy,
        voting,
        app_dir=published_app.APP_DIR,
    )

    assert result == selected_stop
    assert captured["tabs"] == {
        "labels": ["Voting", "Stop details"],
        "default": "Voting",
        "key": "preview_panel_tabs",
        "on_change": "rerun",
    }
    assert captured["show_details"] is False
    assert captured["show_selection_summary"] is False
    assert {key: captured[key] for key in ["stop", "study_id", "taxonomy", "voting", "app_dir"]} == {
        "stop": selected_stop,
        "study_id": "study-a",
        "taxonomy": taxonomy,
        "voting": normalize_voting_config(voting, taxonomy),
        "app_dir": published_app.APP_DIR,
    }


def test_voting_config_uses_taxonomy_and_preserves_admin_copy(taxonomy):
    config = normalize_voting_config(
        {
            "enabled": True,
            "title": "Rate this waiting area",
            "options": ["No Shade", "Missing category", "Limited"],
            "minimum_votes_for_result": 500,
        },
        taxonomy,
    )

    assert config["enabled"] is True
    assert config["title"] == "Rate this waiting area"
    assert config["options"] == ["No Shade", "Limited Shade"]
    assert config["minimum_votes_for_result"] == 100
    assert config["abuse_protection_enabled"] is True
    assert config["enforce_network_vote_limit"] is True
    assert config["vote_cooldown_seconds"] == 15
    assert config["max_new_votes_per_hour"] == 10
    assert config["max_new_votes_per_network_per_hour"] == 20
    assert config["max_new_votes_per_stop_per_hour"] == 30


def test_voting_config_clamps_robustness_limits():
    config = normalize_voting_config(
        {
            "abuse_protection_enabled": True,
            "vote_cooldown_seconds": 500,
            "max_new_votes_per_hour": 0,
        }
    )

    assert config["vote_cooldown_seconds"] == 300
    assert config["max_new_votes_per_hour"] == 1


def test_voting_config_hardens_legacy_thresholds_and_string_booleans():
    config = normalize_voting_config(
        {
            "minimum_votes_for_result": 1,
            "minimum_consensus_percent": 0,
            "minimum_consensus_margin": 0,
            "show_detailed_counts": "false",
            "require_authentication": "true",
            "allow_vote_changes": "not-a-boolean",
        }
    )

    assert config["minimum_votes_for_result"] == 5
    assert config["minimum_consensus_percent"] == 51
    assert config["minimum_consensus_margin"] == 1
    assert config["show_detailed_counts"] is False
    assert config["require_authentication"] is True
    assert config["allow_vote_changes"] is False


def test_privacy_preserving_voter_id_is_stable_without_storing_raw_signals(monkeypatch):
    monkeypatch.setenv("SHADE_GIS_TRUST_PROXY_HEADERS", "true")
    headers = {
        "X-Forwarded-For": "203.0.113.42, 10.0.0.2",
        "User-Agent": "ExampleBrowser/1.0",
        "Accept-Language": "en-US",
    }

    first = privacy_preserving_voter_id(headers, "server-secret", "session-a")
    second = privacy_preserving_voter_id(headers, "server-secret", "session-b")
    other_network = privacy_preserving_voter_id(
        {**headers, "X-Forwarded-For": "203.0.113.43"},
        "server-secret",
        "session-c",
    )

    assert first == second
    assert first.startswith("visitor_")
    assert "203.0.113.42" not in first
    assert "ExampleBrowser" not in first
    assert other_network != first
    assert privacy_preserving_voter_id(
        {"X-Forwarded-For": "not-an-ip", "User-Agent": "ExampleBrowser/1.0"},
        "server-secret",
        "session-fallback",
    ) == "session-fallback"


def test_rotating_fingerprint_secret_intentionally_changes_pseudonyms(monkeypatch):
    monkeypatch.setenv("SHADE_GIS_TRUST_PROXY_HEADERS", "true")
    headers = {"X-Forwarded-For": "8.8.8.8", "User-Agent": "ExampleBrowser/1.0"}

    first = privacy_preserving_voter_id(headers, "stable-secret", "fallback")

    assert first == privacy_preserving_voter_id(headers, "stable-secret", "fallback")
    assert first != privacy_preserving_voter_id(headers, "rotated-secret", "fallback")


def test_forwarded_client_headers_are_ignored_without_trusted_proxy_opt_in(monkeypatch):
    monkeypatch.delenv("SHADE_GIS_TRUST_PROXY_HEADERS", raising=False)

    assert privacy_preserving_voter_id(
        {"X-Forwarded-For": "8.8.8.8", "User-Agent": "Browser"},
        "secret",
        "session-fallback",
    ) == "session-fallback"


def test_network_identity_ignores_mutable_browser_headers_and_groups_ipv6_prefixes():
    first = privacy_preserving_network_id(
        {"User-Agent": "Browser A"},
        "server-secret",
        client_ip="2001:db8:1234:5678::1",
    )
    changed_browser_and_address = privacy_preserving_network_id(
        {"User-Agent": "Browser B", "Accept-Language": "fr"},
        "server-secret",
        client_ip="2001:db8:1234:5678::ffff",
    )
    other_network = privacy_preserving_network_id(
        {},
        "server-secret",
        client_ip="2001:db8:1234:5679::1",
    )

    assert first == changed_browser_and_address
    assert first.startswith("network_")
    assert other_network != first
    assert "2001:db8" not in first


def test_authenticated_identity_is_stable_and_provider_scoped():
    first = privacy_preserving_account_id(
        {"is_logged_in": True, "sub": "person-123", "iss": "issuer-a"},
        "server-secret",
    )

    assert first.startswith("account_")
    assert first == privacy_preserving_account_id(
        {"is_logged_in": True, "sub": "person-123", "iss": "issuer-a"},
        "server-secret",
    )
    assert first != privacy_preserving_account_id(
        {"is_logged_in": True, "sub": "person-123", "iss": "issuer-b"},
        "server-secret",
    )
    assert privacy_preserving_account_id(
        {"is_logged_in": False, "sub": "person-123"}, "server-secret"
    ) == ""


def test_transient_database_setup_requires_tls_and_blocks_private_hosts(monkeypatch):
    monkeypatch.setattr(
        public_voting.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("203.0.113.20", 5432))],
    )
    # TEST-NET ranges are reserved and must not pass the SSRF boundary.
    with pytest.raises(ValueError, match="Private, loopback, link-local, and reserved"):
        validate_vote_database_setup_url(
            "postgresql://user:secret@example.test/research?sslmode=require"
        )

    monkeypatch.setattr(
        public_voting.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("8.8.8.8", 5432))],
    )
    with pytest.raises(ValueError, match="requires sslmode=require"):
        validate_vote_database_setup_url("postgresql://user:secret@example.test/research")
    assert validate_vote_database_setup_url(
        "postgresql://user:secret@example.test/research?sslmode=verify-full"
    ) == "8.8.8.8"
    with pytest.raises(ValueError, match="appear only once"):
        validate_vote_database_setup_url(
            "postgresql://user:secret@example.test/research?sslmode=require&sslmode=disable"
        )


def test_connection_test_pins_the_validated_address_and_never_returns_credentials(monkeypatch):
    observed = {"statements": []}

    class FakeConnection:
        def execute(self, statement):
            observed["statements"].append(statement)
            return self

        def fetchone(self):
            return (140000,) if self.observed_statement == "SHOW server_version_num" else (1,)

        @property
        def observed_statement(self):
            return observed["statements"][-1]

        def close(self):
            observed["closed"] = True

    monkeypatch.setattr(
        public_voting,
        "validate_vote_database_setup_url",
        lambda value: "8.8.8.8",
    )
    monkeypatch.setattr(
        public_voting,
        "_postgres_connection",
        lambda value, *, hostaddr="": observed.update(url=value, hostaddr=hostaddr)
        or FakeConnection(),
    )

    assert public_voting.check_vote_database_connection(
        "postgresql://user:top-secret@example.test/research?sslmode=require"
    ) is None
    assert observed == {
        "url": "postgresql://user:top-secret@example.test/research?sslmode=require",
        "hostaddr": "8.8.8.8",
        "statements": ["SELECT 1", "SHOW server_version_num"],
        "closed": True,
    }


def test_configured_postgres_failure_never_falls_back_to_sqlite(monkeypatch, db_path):
    sqlite_called = []
    monkeypatch.setattr(
        public_voting,
        "_postgres_pooled_connection",
        lambda value, hostaddr: (_ for _ in ()).throw(public_voting.VoteStorageError("unavailable")),
    )
    monkeypatch.setattr(public_voting, "validate_vote_database_setup_url", lambda _value: "8.8.8.8")
    monkeypatch.setattr(
        public_voting,
        "_sqlite_connection",
        lambda value: sqlite_called.append(value),
    )

    with pytest.raises(public_voting.VoteStorageError, match="unavailable"):
        public_voting._connect(
            "postgresql://user:password@example.test/research?sslmode=require",
            db_path.with_name("fallback.sqlite3"),
        )

    assert sqlite_called == []


def test_postgres_runtime_schema_check_never_executes_ddl():
    statements = []

    class Result:
        @staticmethod
        def fetchone():
            return (str(public_voting.VOTE_SCHEMA_VERSION),)

    class Connection:
        @staticmethod
        def execute(statement, parameters=()):
            statements.append((statement, parameters))
            return Result()

    public_voting._ensure_vote_table(Connection(), "postgres")

    assert len(statements) == 1
    assert statements[0][1] == ("schema_version",)
    assert statements[0][0].lstrip().upper().startswith("SELECT")


def test_schema_migration_refuses_destructive_downgrade(db_path):
    connection = sqlite3.connect(db_path.with_name("newer.sqlite3"))
    connection.execute(
        "CREATE TABLE shade_vote_settings (setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO shade_vote_settings (setting_key, setting_value) VALUES (?, ?)",
        ("schema_version", str(public_voting.VOTE_SCHEMA_VERSION + 1)),
    )
    connection.commit()

    with pytest.raises(public_voting.VoteStorageError, match="newer than this app"):
        public_voting._migrate_vote_schema(connection, "sqlite")

    assert connection.execute(
        "SELECT setting_value FROM shade_vote_settings WHERE setting_key = ?",
        ("schema_version",),
    ).fetchone() == (str(public_voting.VOTE_SCHEMA_VERSION + 1),)
    connection.close()


def test_voting_coverage_choices_never_include_source_or_review_categories():
    mixed_taxonomy = [
        {"name": "Limited Natural Shade"},
        {"name": "Intentional Built Shade"},
        {"name": "Needs Review"},
    ]

    config = normalize_voting_config(None, mixed_taxonomy)

    assert config["options"] == ["No Shade", "Limited Shade", "Significant Shade"]


def test_source_heading_tooltip_explains_all_source_categories():
    all_sources_help = source_taxonomy_help()
    for source, definition in PUBLIC_SOURCE_DEFINITIONS.items():
        assert f"**{source}:** {definition}" in all_sources_help


def test_source_heading_tooltip_uses_project_taxonomy_definitions():
    source_help = source_taxonomy_help(
        [{"shade_source": "Natural", "operational_definition": "Configured natural definition."}]
    )

    assert "**Natural:** Configured natural definition." in source_help
    assert f"**Purpose-built:** {PUBLIC_SOURCE_DEFINITIONS['Purpose-built']}" in source_help


def test_coverage_question_tooltip_explains_configured_taxonomy_choices():
    taxonomy = [
        {"name": "No Shade", "description": "Configured no-shade definition."},
        {"name": "Limited Shade", "description": "Configured limited-shade definition."},
        {"name": "Significant Shade", "description": "Configured significant-shade definition."},
        {"name": "Needs Review", "description": "Not a public coverage choice."},
    ]

    guide = coverage_taxonomy_help(list(PUBLIC_COVERAGE_DEFINITIONS), taxonomy)

    for choice in PUBLIC_COVERAGE_DEFINITIONS:
        slug = choice.lower().replace(" ", "-")
        assert f"**{choice}:** Configured {slug} definition." in guide
    assert "Needs Review" not in guide


def test_sqlite_vote_store_upserts_one_vote_per_browser_session(db_path):
    assert save_vote(
        "study-a",
        "1001",
        "browser-a",
        "No Shade",
        shade_sources=["Natural"],
        database_url="",
        sqlite_path=db_path,
    )
    assert save_vote(
        "study-a",
        "1001",
        "browser-b",
        "Limited",
        shade_sources=["Natural", "Incidental Built"],
        database_url="",
        sqlite_path=db_path,
    )
    assert save_vote(
        "study-a",
        "1001",
        "browser-a",
        "Significant",
        shade_sources=["Constructed"],
        database_url="",
        sqlite_path=db_path,
    )

    assert get_existing_vote(
        "study-a", "1001", "browser-a", database_url="", sqlite_path=db_path
    ) == "Significant Shade"
    assert get_existing_vote_details(
        "study-a", "1001", "browser-a", database_url="", sqlite_path=db_path
    ) == {"coverage_status": "Significant Shade", "shade_sources": ["Purpose-built"]}
    assert get_existing_vote_details(
        "study-a", "1001", "browser-b", database_url="", sqlite_path=db_path
    ) == {"coverage_status": "Limited Shade", "shade_sources": ["Natural", "Incidental"]}
    assert get_vote_counts(
        "study-a",
        "1001",
        ["No Shade", "Limited Shade", "Significant Shade"],
        database_url="",
        sqlite_path=db_path,
    ) == {"No Shade": 0, "Limited Shade": 1, "Significant Shade": 1}


def test_no_shade_vote_clears_sources_and_migrates_an_existing_sqlite_store(db_path):
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE shade_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            study_id TEXT NOT NULL,
            stop_id TEXT NOT NULL,
            voter_id TEXT NOT NULL,
            coverage_status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (study_id, stop_id, voter_id)
        )
        """
    )
    connection.commit()
    connection.close()

    assert save_vote(
        "study-a",
        "1001",
        "browser-a",
        "No Shade",
        shade_sources=["Natural", "Purpose-built"],
        database_url="",
        sqlite_path=db_path,
    )

    assert get_existing_vote_details(
        "study-a", "1001", "browser-a", database_url="", sqlite_path=db_path
    ) == {"coverage_status": "No Shade", "shade_sources": []}


def test_vote_changes_can_be_disabled_and_studies_are_isolated(db_path):
    assert save_vote(
        "study-a",
        "1001",
        "browser-a",
        "No Shade",
        allow_vote_changes=False,
        database_url="",
        sqlite_path=db_path,
    )
    assert not save_vote(
        "study-a",
        "1001",
        "browser-a",
        "Limited",
        allow_vote_changes=False,
        database_url="",
        sqlite_path=db_path,
    )
    assert save_vote(
        "study-b",
        "1001",
        "browser-a",
        "Limited",
        allow_vote_changes=False,
        database_url="",
        sqlite_path=db_path,
    )

    assert get_existing_vote(
        "study-a", "1001", "browser-a", database_url="", sqlite_path=db_path
    ) == "No Shade"
    assert get_vote_counts(
        "study-b", "1001", ["No Shade", "Limited Shade"], database_url="", sqlite_path=db_path
    ) == {"No Shade": 0, "Limited Shade": 1}


def test_vote_store_enforces_cooldown_and_hourly_new_stop_limit(db_path):
    started = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    assert save_vote(
        "study-a",
        "1001",
        "visitor-a",
        "No Shade",
        cooldown_seconds=5,
        max_new_votes_per_hour=2,
        database_url="",
        sqlite_path=db_path,
        now=started,
    )

    with pytest.raises(VoteRateLimitError, match="Please wait 3 seconds") as cooldown_error:
        save_vote(
            "study-a",
            "1002",
            "visitor-a",
            "Limited Shade",
            cooldown_seconds=5,
            max_new_votes_per_hour=2,
            database_url="",
            sqlite_path=db_path,
            now=started + timedelta(seconds=2),
        )
    assert cooldown_error.value.retry_after_seconds == 3

    assert save_vote(
        "study-a",
        "1002",
        "visitor-a",
        "Limited Shade",
        cooldown_seconds=5,
        max_new_votes_per_hour=2,
        database_url="",
        sqlite_path=db_path,
        now=started + timedelta(seconds=5),
    )
    with pytest.raises(VoteRateLimitError, match="hourly voting limit"):
        save_vote(
            "study-a",
            "1003",
            "visitor-a",
            "Significant Shade",
            max_new_votes_per_hour=2,
            database_url="",
            sqlite_path=db_path,
            now=started + timedelta(seconds=10),
        )

    assert save_vote(
        "study-a",
        "1001",
        "visitor-a",
        "Limited Shade",
        max_new_votes_per_hour=2,
        database_url="",
        sqlite_path=db_path,
        now=started + timedelta(seconds=10),
    )


def test_network_limit_blocks_browser_rotation_for_the_same_stop(db_path):
    assert save_vote(
        "study-a",
        "1001",
        "visitor-browser-a",
        "No Shade",
        network_id="network-a",
        enforce_network_vote_limit=True,
        database_url="",
        sqlite_path=db_path,
    )
    assert not save_vote(
        "study-a",
        "1001",
        "visitor-browser-b",
        "Significant Shade",
        network_id="network-a",
        enforce_network_vote_limit=True,
        database_url="",
        sqlite_path=db_path,
    )
    assert get_vote_counts(
        "study-a",
        "1001",
        ["No Shade", "Limited Shade", "Significant Shade"],
        database_url="",
        sqlite_path=db_path,
    ) == {"No Shade": 1, "Limited Shade": 0, "Significant Shade": 0}


def test_network_and_stop_velocity_limits_survive_identity_rotation(db_path):
    started = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    for index in range(2):
        assert save_vote(
            "study-a",
            f"network-stop-{index}",
            f"visitor-{index}",
            "No Shade",
            network_id="network-a",
            enforce_network_vote_limit=True,
            max_new_votes_per_network_per_hour=2,
            database_url="",
            sqlite_path=db_path,
            now=started + timedelta(minutes=index),
        )
    with pytest.raises(VoteRateLimitError, match="network has reached"):
        save_vote(
            "study-a",
            "network-stop-3",
            "rotated-visitor",
            "No Shade",
            network_id="network-a",
            enforce_network_vote_limit=True,
            max_new_votes_per_network_per_hour=2,
            database_url="",
            sqlite_path=db_path,
            now=started + timedelta(minutes=3),
        )

    for index in range(5):
        assert save_vote(
            "study-a",
            "popular-stop",
            f"global-visitor-{index}",
            "Limited Shade",
            max_new_votes_per_stop_per_hour=5,
            database_url="",
            sqlite_path=db_path,
            now=started + timedelta(minutes=index),
        )
    with pytest.raises(VoteRateLimitError, match="unusually quickly"):
        save_vote(
            "study-a",
            "popular-stop",
            "global-visitor-6",
            "Limited Shade",
            max_new_votes_per_stop_per_hour=5,
            database_url="",
            sqlite_path=db_path,
            now=started + timedelta(minutes=6),
        )


def test_parallel_network_duplicates_are_serialized(db_path):
    # Initialize/migrate the store before racing independent connections.
    assert save_vote(
        "setup-study", "setup-stop", "setup-voter", "No Shade",
        database_url="", sqlite_path=db_path,
    )

    def submit(voter_id: str) -> bool:
        return save_vote(
            "study-a",
            "1001",
            voter_id,
            "No Shade",
            network_id="network-a",
            enforce_network_vote_limit=True,
            database_url="",
            sqlite_path=db_path,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, ["visitor-a", "visitor-b"]))

    assert sorted(results) == [False, True]
    assert sum(get_vote_counts(
        "study-a", "1001", ["No Shade"], database_url="", sqlite_path=db_path
    ).values()) == 1


def test_community_result_requires_threshold_and_unique_leader():
    assert community_result({"No Shade": 2, "Limited Shade": 1}, 5)["status"] == "pending"
    assert community_result({"No Shade": 3, "Limited Shade": 3}, 5)["status"] == "tied"
    result = community_result({"No Shade": 4, "Limited Shade": 2}, 5)
    assert result["status"] == "consensus"
    assert result["label"] == "No Shade"
    assert result["total"] == 6


def test_community_result_requires_a_meaningful_share_and_margin():
    contested = community_result(
        {"No Shade": 4, "Limited Shade": 2},
        5,
        minimum_consensus_percent=67,
        minimum_consensus_margin=2,
    )
    consensus = community_result(
        {"No Shade": 7, "Limited Shade": 3},
        5,
        minimum_consensus_percent=67,
        minimum_consensus_margin=2,
    )

    assert contested["status"] == "contested"
    assert contested["label"] == "No clear consensus"
    assert consensus["status"] == "consensus"
