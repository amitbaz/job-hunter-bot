import base64
from pathlib import Path
from job_hunter.config import load_settings


def test_load_settings_decodes_private_sources(monkeypatch, tmp_path: Path):
    cfg = tmp_path / "search.yml"
    cfg.write_text(
        "timezone: Europe/Berlin\nscheduled_hour: 9\nmax_jobs_per_run: 25\n"
        "thresholds:\n  package: 75\n  possible: 65\nsalary_floor_eur: 90000\n"
        "target_titles: []\npositive_keywords: []\nblocked_title_keywords: []\n"
        "search_queries: []\nats:\n  ashby: []\n  lever: []\n  greenhouse: []\n"
    )
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("CANDIDATE_PROFILE_B64", base64.b64encode(b"profile").decode())
    monkeypatch.setenv("COVER_LETTER_TEMPLATE_B64", base64.b64encode(b"template").decode())
    monkeypatch.setenv("JOB_HUNTER_DRY_RUN", "1")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    settings = load_settings(cfg)
    assert settings.candidate_profile == "profile"
    assert settings.cover_letter_template == "template"
    assert settings.timezone == "Europe/Berlin"
    assert settings.dry_run is True


def test_load_settings_dry_run_env_zero_is_false(monkeypatch, tmp_path: Path):
    cfg = tmp_path / "search.yml"
    cfg.write_text(
        "timezone: Europe/Berlin\nscheduled_hour: 9\nmax_jobs_per_run: 25\n"
        "thresholds:\n  package: 75\n  possible: 65\nsalary_floor_eur: 90000\n"
        "target_titles: []\npositive_keywords: []\nblocked_title_keywords: []\n"
        "search_queries: []\nats:\n  ashby: []\n  lever: []\n  greenhouse: []\n"
    )
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("CANDIDATE_PROFILE_B64", base64.b64encode(b"profile").decode())
    monkeypatch.setenv("COVER_LETTER_TEMPLATE_B64", base64.b64encode(b"template").decode())
    monkeypatch.setenv("JOB_HUNTER_DRY_RUN", "0")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    settings = load_settings(cfg)
    assert settings.dry_run is False


def test_load_settings_requires_telegram_in_non_dry_run(monkeypatch, tmp_path: Path):
    import pytest
    cfg = tmp_path / "search.yml"
    cfg.write_text(
        "timezone: Europe/Berlin\nscheduled_hour: 9\nmax_jobs_per_run: 25\n"
        "thresholds:\n  package: 75\n  possible: 65\nsalary_floor_eur: 90000\n"
        "target_titles: []\npositive_keywords: []\nblocked_title_keywords: []\n"
        "search_queries: []\nats:\n  ashby: []\n  lever: []\n  greenhouse: []\n"
    )
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("CANDIDATE_PROFILE_B64", base64.b64encode(b"profile").decode())
    monkeypatch.setenv("COVER_LETTER_TEMPLATE_B64", base64.b64encode(b"template").decode())
    monkeypatch.delenv("JOB_HUNTER_DRY_RUN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises((ValueError, KeyError)):
        load_settings(cfg)


def test_load_settings_discovery_config(monkeypatch, tmp_path: Path):
    cfg = tmp_path / "search.yml"
    cfg.write_text(
        "timezone: Europe/Berlin\nscheduled_hour: 9\nmax_jobs_per_run: 75\n"
        "thresholds:\n  package: 75\n  possible: 65\nsalary_floor_eur: 90000\n"
        "target_titles: []\npositive_keywords: []\nblocked_title_keywords: []\n"
        "engineering_title_keywords:\n"
        "  - engineer\n"
        "  - developer\n"
        "engineering_title_phrases:\n"
        "  - technical lead\n"
        "  - frontend lead\n"
        "  - software architect\n"
        "blocked_profession_title_phrases:\n"
        "  - product manager\n"
        "  - product designer\n"
        "  - sales engineer\n"
        "  - data engineer\n"
        "max_search_queries_per_run: 4\n"
        "role_families:\n"
        "  - staff product engineer\n"
        "  - senior software engineer frontend\n"
        "search_query_templates:\n"
        "  - '\"{role}\" React TypeScript remote Europe'\n"
        "search_domains:\n"
        "  - jobs.ashbyhq.com\n"
        "search_queries:\n"
        "  - '\"Senior Product Engineer\" remote'\n"
        "ats:\n  ashby: []\n  lever: []\n  greenhouse: []\n"
    )
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("CANDIDATE_PROFILE_B64", base64.b64encode(b"profile").decode())
    monkeypatch.setenv("COVER_LETTER_TEMPLATE_B64", base64.b64encode(b"template").decode())
    monkeypatch.setenv("JOB_HUNTER_DRY_RUN", "1")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    settings = load_settings(cfg)
    assert settings.policy.max_jobs_per_run == 75
    assert settings.policy.engineering_title_keywords == ["engineer", "developer"]
    assert settings.policy.engineering_title_phrases == [
        "technical lead",
        "frontend lead",
        "software architect",
    ]
    assert settings.policy.blocked_profession_title_phrases == [
        "product manager",
        "product designer",
        "sales engineer",
        "data engineer",
    ]
    assert settings.policy.max_search_queries_per_run == 4
    assert settings.policy.role_families == [
        "staff product engineer",
        "senior software engineer frontend",
    ]
    assert settings.policy.search_query_templates == [
        '"{role}" React TypeScript remote Europe'
    ]
    assert settings.policy.search_domains == ["jobs.ashbyhq.com"]
    assert settings.policy.search_queries == ['"Senior Product Engineer" remote']
