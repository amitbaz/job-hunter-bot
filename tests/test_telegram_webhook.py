from pathlib import Path

from job_hunter.config import WebhookSettings
from job_hunter.github_state import ArtifactStateSnapshot
from job_hunter.models import NavigationCard, NavigationSession
from job_hunter.navigation_store import create_navigation_session
from job_hunter.store import JobStore
from job_hunter.telegram_webhook import create_app


class FakeStateLoader:
    def __init__(self, snapshot=None, error=None):
        self.snapshot = snapshot
        self.error = error
        self.calls = 0

    def load_latest(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.snapshot


class FakeTelegram:
    def __init__(self):
        self.edits = []
        self.answers = []

    def edit_job_card(self, *, chat_id, message_id, text, keyboard):
        self.edits.append((str(chat_id), str(message_id), text, keyboard))
        return True

    def answer_callback(self, callback_id, text=None, show_alert=False):
        self.answers.append((callback_id, text, show_alert))
        return True


def _settings():
    return WebhookSettings(
        telegram_bot_token="bot-token",
        telegram_webhook_secret="webhook-secret",
        github_repository="amitbaz/job-hunter-bot",
        github_state_token="github-token",
        github_state_artifact_name="job-hunter-state",
        github_state_cache_dir="/tmp/job-hunter-state",
    )


def _snapshot_with_session(tmp_path: Path):
    db = tmp_path / "job_hunter.sqlite3"
    with JobStore(db) as store:
        create_navigation_session(
            store,
            NavigationSession(
                session_id="session1",
                cards=[
                    NavigationCard(1, "Senior A", "Acme", "Berlin", 91, "https://example.test/a"),
                    NavigationCard(2, "Senior B", "Beta", "Remote", 88, "https://example.test/b"),
                ],
                telegram_message_id="99",
                created_at="2026-09-01T00:00:00+00:00",
                expires_at="2099-01-01T00:00:00+00:00",
            ),
        )
    return ArtifactStateSnapshot(artifact_id=7, path=db, created_at="2026-09-01T00:00:00Z")


def _callback_update(data="n|session1|1"):
    return {
        "update_id": 10,
        "callback_query": {
            "id": "cb-1",
            "data": data,
            "message": {"message_id": 99, "chat": {"id": 123}},
        },
    }


def test_health_endpoint_does_not_require_secret(tmp_path):
    app = create_app(
        settings=_settings(),
        state_loader=FakeStateLoader(),
        telegram=FakeTelegram(),
    )
    response = app.test_client().get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_webhook_rejects_wrong_secret_before_loading_state(tmp_path):
    loader = FakeStateLoader(_snapshot_with_session(tmp_path))
    telegram = FakeTelegram()
    app = create_app(settings=_settings(), state_loader=loader, telegram=telegram)

    response = app.test_client().post(
        "/telegram/webhook",
        json=_callback_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )

    assert response.status_code == 403
    assert loader.calls == 0
    assert telegram.edits == []


def test_webhook_navigation_loads_latest_artifact_and_edits_card(tmp_path):
    loader = FakeStateLoader(_snapshot_with_session(tmp_path))
    telegram = FakeTelegram()
    app = create_app(settings=_settings(), state_loader=loader, telegram=telegram)

    response = app.test_client().post(
        "/telegram/webhook",
        json=_callback_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"},
    )

    assert response.status_code == 200
    assert loader.calls == 1
    assert telegram.edits[0][0:2] == ("123", "99")
    assert "Company: Beta" in telegram.edits[0][2]
    assert telegram.answers[-1] == ("cb-1", None, False)


def test_apply_callback_does_not_need_artifact_state():
    loader = FakeStateLoader(error=AssertionError("state should not load"))
    telegram = FakeTelegram()
    app = create_app(settings=_settings(), state_loader=loader, telegram=telegram)

    response = app.test_client().post(
        "/telegram/webhook",
        json=_callback_update("a|session1|0"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"},
    )

    assert response.status_code == 200
    assert loader.calls == 0
    assert telegram.answers[-1][1] == "Apply functionality coming soon."


def test_webhook_missing_session_reports_syncing(tmp_path):
    db = tmp_path / "job_hunter.sqlite3"
    with JobStore(db):
        pass
    loader = FakeStateLoader(ArtifactStateSnapshot(1, db, "2026-09-01T00:00:00Z"))
    telegram = FakeTelegram()
    app = create_app(settings=_settings(), state_loader=loader, telegram=telegram)

    response = app.test_client().post(
        "/telegram/webhook",
        json=_callback_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"},
    )

    assert response.status_code == 200
    assert telegram.answers[-1][1] == "Job list is still syncing. Try again shortly."


def test_webhook_artifact_failure_is_acknowledged():
    loader = FakeStateLoader(error=RuntimeError("github unavailable"))
    telegram = FakeTelegram()
    app = create_app(settings=_settings(), state_loader=loader, telegram=telegram)

    response = app.test_client().post(
        "/telegram/webhook",
        json=_callback_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"},
    )

    assert response.status_code == 200
    assert telegram.answers[-1][1] == "Could not load this job list right now."


def test_webhook_ignores_non_callback_updates():
    loader = FakeStateLoader(error=AssertionError("state should not load"))
    telegram = FakeTelegram()
    app = create_app(settings=_settings(), state_loader=loader, telegram=telegram)

    response = app.test_client().post(
        "/telegram/webhook",
        json={"update_id": 11, "message": {"text": "hello"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"},
    )

    assert response.status_code == 200
    assert loader.calls == 0
