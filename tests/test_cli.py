import sqlite3
from pathlib import Path

from job_hunter.gmail_models import GmailSettings, GmailSyncSummary
from job_hunter.models import GeminiQuotaSettings, RunSummary, SearchPolicy, Settings
from job_hunter.store import JobStore
from job_hunter import cli


def _settings(tmp_path, **overrides):
    policy = SearchPolicy(
        target_titles=[],
        positive_keywords=[],
        blocked_title_keywords=[],
        salary_floor_eur=90000,
        thresholds={"package": 75, "possible": 65},
    )
    defaults = dict(
        gemini_api_key="key",
        candidate_profile="profile",
        cover_letter_template="template",
        timezone="Europe/Berlin",
        scheduled_hour=9,
        policy=policy,
        gemini_quota=GeminiQuotaSettings(rpm=10, tpm=250000, rpd=500),
        dry_run=True,
        db_path=str(tmp_path / "var" / "state.sqlite3"),
    )
    defaults.update(overrides)
    return Settings(**defaults)


def test_run_scheduled_skips_outside_target_hour(monkeypatch, tmp_path, caplog):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda path: settings)
    monkeypatch.setattr(cli, "should_run_scheduled", lambda now, tz, hour: False)

    called = []
    monkeypatch.setattr(cli, "run_pipeline", lambda s: called.append(s) or RunSummary())

    with caplog.at_level("INFO"):
        exit_code = cli.main(["run", "--scheduled", "--config", "config/search.yml"])

    assert exit_code == 0
    assert called == []
    assert any("skipped" in record.message.lower() for record in caplog.records)


def test_run_scheduled_proceeds_at_target_hour(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda path: settings)
    monkeypatch.setattr(cli, "should_run_scheduled", lambda now, tz, hour: True)

    called = []
    monkeypatch.setattr(cli, "run_pipeline", lambda s: called.append(s) or RunSummary(ready_to_apply=1))

    exit_code = cli.main(["run", "--scheduled"])

    assert exit_code == 0
    assert called == [settings]


def test_run_manual_always_proceeds_without_time_guard(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda path: settings)

    def _boom(*args, **kwargs):
        raise AssertionError("should_run_scheduled must not be called for manual runs")

    monkeypatch.setattr(cli, "should_run_scheduled", _boom)

    called = []
    monkeypatch.setattr(cli, "run_pipeline", lambda s: called.append(s) or RunSummary())

    exit_code = cli.main(["run"])

    assert exit_code == 0
    assert called == [settings]


def test_run_creates_db_and_output_parent_directories(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda path: settings)
    monkeypatch.setattr(cli, "run_pipeline", lambda s: RunSummary())

    assert not Path(settings.db_path).parent.exists()

    cli.main(["run"])

    assert Path(settings.db_path).parent.exists()
    assert (Path(settings.db_path).parent / "cover_letters").exists()


def test_run_unhandled_exception_returns_nonzero(monkeypatch, tmp_path):
    def _raise(path):
        raise RuntimeError("config broke")

    monkeypatch.setattr(cli, "load_settings", _raise)

    exit_code = cli.main(["run"])

    assert exit_code == 1


def test_parser_accepts_sync_gmail_dry_run():
    args = cli.build_parser().parse_args(["sync-gmail", "--dry-run"])

    assert args.command == "sync-gmail"
    assert args.dry_run is True
    assert args.force_backfill is False


def test_parser_accepts_sync_gmail_force_backfill():
    args = cli.build_parser().parse_args(["sync-gmail", "--force-backfill"])

    assert args.command == "sync-gmail"
    assert args.dry_run is False
    assert args.force_backfill is True


def _gmail_settings(tmp_path):
    return GmailSettings(
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
        gemini_api_key="gemini",
        gemini_quota=GeminiQuotaSettings(rpm=10, tpm=250000, rpd=500),
        db_path=str(tmp_path / "gmail.sqlite3"),
    )


def _patch_gmail_sync_dependencies(monkeypatch, tmp_path, run):
    class SyncService:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def sync(self, now, *, dry_run, force_backfill):
            return run(now=now, dry_run=dry_run, force_backfill=force_backfill)

    monkeypatch.setattr(cli, "load_gmail_settings", lambda: _gmail_settings(tmp_path), raising=False)
    monkeypatch.setattr(cli, "HttpClient", object, raising=False)
    monkeypatch.setattr(cli, "GoogleOAuthTokenProvider", lambda settings: object(), raising=False)
    monkeypatch.setattr(cli, "GmailClient", lambda http, token_provider: object(), raising=False)
    monkeypatch.setattr(cli, "GeminiClient", lambda api_key, model, http: object(), raising=False)
    monkeypatch.setattr(cli, "JobStore", lambda path: object(), raising=False)
    monkeypatch.setattr(cli, "GmailSyncService", SyncService, raising=False)


def test_sync_gmail_does_not_load_candidate_profile_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli,
        "load_settings",
        lambda path: (_ for _ in ()).throw(AssertionError("must not load candidate profile settings")),
    )
    _patch_gmail_sync_dependencies(
        monkeypatch, tmp_path, lambda **kwargs: GmailSyncSummary()
    )

    assert cli.main(["sync-gmail"]) == 0


def test_sync_gmail_returns_nonzero_on_fatal_auth_error(monkeypatch, tmp_path):
    _patch_gmail_sync_dependencies(
        monkeypatch,
        tmp_path,
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("Gmail authorization failed")),
    )

    assert cli.main(["sync-gmail"]) == 1


def test_sync_gmail_returns_zero_when_service_completes_with_message_errors(
    monkeypatch, tmp_path, caplog
):
    _patch_gmail_sync_dependencies(
        monkeypatch, tmp_path, lambda **kwargs: GmailSyncSummary(errors=2)
    )

    assert cli.main(["sync-gmail"]) == 0
    assert any("will retry" in record.message for record in caplog.records)


def test_sync_gmail_dry_run_does_not_create_missing_database_path(
    monkeypatch, tmp_path
):
    settings = _gmail_settings(tmp_path)
    settings = GmailSettings(
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        refresh_token=settings.refresh_token,
        gemini_api_key=settings.gemini_api_key,
        gemini_quota=settings.gemini_quota,
        db_path=str(tmp_path / "missing" / "state.sqlite3"),
    )

    class InspectingService:
        def __init__(self, *, store, **kwargs):
            self.store = store

        def sync(self, now, *, dry_run, force_backfill):
            assert dry_run is True
            self.store.save_gmail_sync_state(
                account_id="dry-run@example.com",
                history_id="100",
                last_successful_sync_at=now.isoformat(),
                backfill_completed_at=now.isoformat(),
            )
            return GmailSyncSummary()

    monkeypatch.setattr(cli, "load_gmail_settings", lambda: settings)
    monkeypatch.setattr(cli, "HttpClient", object)
    monkeypatch.setattr(cli, "GoogleOAuthTokenProvider", lambda value: object())
    monkeypatch.setattr(cli, "GmailClient", lambda http, token_provider: object())
    monkeypatch.setattr(cli, "GeminiClient", lambda api_key, model, http: object())
    monkeypatch.setattr(cli, "GmailSyncService", InspectingService)

    assert cli.main(["sync-gmail", "--dry-run"]) == 0
    assert not Path(settings.db_path).parent.exists()
    assert not Path(settings.db_path).exists()


def test_sync_gmail_dry_run_opens_existing_database_read_only(
    monkeypatch, tmp_path
):
    settings = _gmail_settings(tmp_path)
    store = JobStore(settings.db_path)
    store.save_gmail_sync_state(
        account_id="candidate@example.com",
        history_id="before",
        last_successful_sync_at="2026-08-30T12:00:00+00:00",
        backfill_completed_at="2026-08-30T12:00:00+00:00",
    )
    store.close()

    with sqlite3.connect(settings.db_path) as connection:
        schema_before = connection.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        rows_before = connection.execute(
            "SELECT * FROM gmail_sync_state ORDER BY account_id"
        ).fetchall()

    class InspectingService:
        write_was_blocked = False

        def __init__(self, *, store, **kwargs):
            self.store = store

        def sync(self, now, *, dry_run, force_backfill):
            assert dry_run is True
            try:
                self.store.save_gmail_sync_state(
                    account_id="candidate@example.com",
                    history_id="after",
                    last_successful_sync_at=now.isoformat(),
                    backfill_completed_at=now.isoformat(),
                )
            except sqlite3.OperationalError:
                type(self).write_was_blocked = True
            return GmailSyncSummary()

    monkeypatch.setattr(cli, "load_gmail_settings", lambda: settings)
    monkeypatch.setattr(cli, "HttpClient", object)
    monkeypatch.setattr(cli, "GoogleOAuthTokenProvider", lambda value: object())
    monkeypatch.setattr(cli, "GmailClient", lambda http, token_provider: object())
    monkeypatch.setattr(cli, "GeminiClient", lambda api_key, model, http: object())
    monkeypatch.setattr(cli, "GmailSyncService", InspectingService)

    assert cli.main(["sync-gmail", "--dry-run"]) == 0
    assert InspectingService.write_was_blocked is True
    with sqlite3.connect(settings.db_path) as connection:
        assert connection.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall() == schema_before
        assert connection.execute(
            "SELECT * FROM gmail_sync_state ORDER BY account_id"
        ).fetchall() == rows_before


def test_sync_gmail_dry_run_reads_legacy_database_without_gmail_schema(
    monkeypatch, tmp_path
):
    settings = _gmail_settings(tmp_path)
    with sqlite3.connect(settings.db_path) as connection:
        connection.execute(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL
            )
            """
        )
        schema_before = connection.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()

    class InspectingService:
        def __init__(self, *, store, **kwargs):
            self.store = store

        def sync(self, now, *, dry_run, force_backfill):
            assert dry_run is True
            assert self.store.get_gmail_sync_state("candidate@example.com") is None
            return GmailSyncSummary()

    monkeypatch.setattr(cli, "load_gmail_settings", lambda: settings)
    monkeypatch.setattr(cli, "HttpClient", object)
    monkeypatch.setattr(cli, "GoogleOAuthTokenProvider", lambda value: object())
    monkeypatch.setattr(cli, "GmailClient", lambda http, token_provider: object())
    monkeypatch.setattr(cli, "GeminiClient", lambda api_key, model, http: object())
    monkeypatch.setattr(cli, "GmailSyncService", InspectingService)

    assert cli.main(["sync-gmail", "--dry-run"]) == 0
    with sqlite3.connect(settings.db_path) as connection:
        assert connection.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall() == schema_before
