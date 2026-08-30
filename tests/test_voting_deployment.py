from __future__ import annotations

from pathlib import Path

from stop_gis.pages import voting_deployment


def test_transient_database_action_clears_connection_string(monkeypatch):
    secret_url = "postgresql://user:secret@example.test/research?sslmode=require"
    session_state = {voting_deployment.DATABASE_URL_KEY: secret_url}
    observed = []
    monkeypatch.setattr(voting_deployment.st, "session_state", session_state)
    monkeypatch.setattr(
        voting_deployment,
        "check_vote_database_connection",
        lambda value: observed.append(value),
    )

    voting_deployment._consume_database_url("test")

    assert observed == [secret_url]
    assert session_state[voting_deployment.DATABASE_URL_KEY] == ""
    result = session_state[voting_deployment.DATABASE_ACTION_RESULT_KEY]
    assert result["ok"] is True
    assert secret_url not in result["message"]
    assert "secret" not in result["message"].lower()


def test_transient_database_action_clears_connection_string_on_failure(monkeypatch):
    secret_url = "postgresql://user:secret@example.test/research?sslmode=require"
    session_state = {voting_deployment.DATABASE_URL_KEY: secret_url}
    monkeypatch.setattr(voting_deployment.st, "session_state", session_state)
    monkeypatch.setattr(
        voting_deployment,
        "initialize_vote_database",
        lambda value: (_ for _ in ()).throw(RuntimeError(value)),
    )

    voting_deployment._consume_database_url("initialize")

    assert session_state[voting_deployment.DATABASE_URL_KEY] == ""
    result = session_state[voting_deployment.DATABASE_ACTION_RESULT_KEY]
    assert result == {
        "ok": False,
        "message": "The database action failed safely. No credential was retained.",
    }
    assert secret_url not in result["message"]


def test_read_write_action_uses_transient_url_and_records_only_progress(monkeypatch):
    secret_url = "postgresql://runtime:secret@example.test/research?sslmode=require"
    session_state = {voting_deployment.DATABASE_URL_KEY: secret_url}
    observed = []
    monkeypatch.setattr(voting_deployment.st, "session_state", session_state)
    monkeypatch.setattr(
        voting_deployment,
        "confirm_vote_database_read_write",
        lambda value: observed.append(value),
    )

    voting_deployment._consume_database_url("confirm")

    assert observed == [secret_url]
    assert session_state[voting_deployment.DATABASE_URL_KEY] == ""
    assert session_state["vote_database_read_write_confirmed"] is True
    assert secret_url not in repr(session_state)


def test_setup_callback_rejects_invalid_csrf_without_using_url(monkeypatch):
    secret_url = "postgresql://runtime:secret@example.test/research?sslmode=require"
    session_state = {
        voting_deployment.DATABASE_URL_KEY: secret_url,
        voting_deployment.CSRF_TOKEN_KEY: "expected",
    }
    observed = []
    monkeypatch.setattr(voting_deployment.st, "session_state", session_state)
    monkeypatch.setattr(
        voting_deployment,
        "check_vote_database_connection",
        lambda value: observed.append(value),
    )

    voting_deployment._consume_database_url("verify", "wrong")

    assert observed == []
    assert session_state[voting_deployment.DATABASE_URL_KEY] == ""
    assert session_state[voting_deployment.DATABASE_ACTION_RESULT_KEY]["ok"] is False


def test_setup_rate_limit_is_server_side_and_bounded(monkeypatch):
    session_state = {voting_deployment.SETUP_SESSION_KEY: "session-a"}
    monkeypatch.setattr(voting_deployment.st, "session_state", session_state)
    monkeypatch.setattr(voting_deployment, "_authenticated_setup_identity", lambda: "user-hash")
    voting_deployment._SETUP_ATTEMPTS.clear()

    assert all(
        voting_deployment._claim_setup_attempt()
        for _ in range(voting_deployment.SETUP_RATE_LIMIT)
    )
    assert voting_deployment._claim_setup_attempt() is False


def test_browser_secret_generator_never_binds_secret_to_streamlit_state():
    source = Path("stop_gis/pages/voting_deployment.py").read_text(encoding="utf-8")

    assert "crypto.getRandomValues(bytes)" in source
    assert "new Uint8Array(32)" in source
    assert "st.session_state" not in source.split("def render_browser_secret_generator", 1)[1].split(
        "def render_voting_deployment_wizard", 1
    )[0]
