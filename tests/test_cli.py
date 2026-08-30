from pathlib import Path

from job_hunter.models import RunSummary, SearchPolicy, Settings
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
