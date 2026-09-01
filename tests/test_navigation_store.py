from job_hunter.models import NavigationCard, NavigationSession
from job_hunter.navigation_store import (
    attach_navigation_message_id,
    create_navigation_session,
    get_navigation_session,
    prune_navigation_sessions,
)
from job_hunter.store import JobStore


def test_navigation_session_round_trip(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    session = NavigationSession(
        session_id="session-1",
        cards=[NavigationCard(1, "Senior FE", "Acme", "Berlin", 91, "https://example.test/1")],
        telegram_message_id=None,
        created_at="2026-08-31T12:00:00+00:00",
        expires_at="2026-09-30T12:00:00+00:00",
    )
    create_navigation_session(store, session)
    assert attach_navigation_message_id(store, "session-1", "42") is True

    loaded = get_navigation_session(store, "session-1")
    assert loaded is not None
    assert loaded.telegram_message_id == "42"
    assert loaded.cards[0].location == "Berlin"


def test_prune_navigation_sessions_deletes_expired_only(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    expired = NavigationSession(
        session_id="expired",
        cards=[],
        telegram_message_id=None,
        created_at="2026-08-01T00:00:00+00:00",
        expires_at="2026-08-31T00:00:00+00:00",
    )
    active = NavigationSession(
        session_id="active",
        cards=[],
        telegram_message_id=None,
        created_at="2026-08-31T00:00:00+00:00",
        expires_at="2026-09-30T00:00:00+00:00",
    )
    create_navigation_session(store, expired)
    create_navigation_session(store, active)

    assert prune_navigation_sessions(store, "2026-09-01T00:00:00+00:00") == 1
    assert get_navigation_session(store, "expired") is None
    assert get_navigation_session(store, "active") is not None


def test_missing_navigation_table_reads_as_no_session(tmp_path):
    with JobStore(tmp_path / "state.sqlite3") as store:
        assert get_navigation_session(store, "missing") is None
