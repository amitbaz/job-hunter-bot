from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import (
    DEFAULT_BLOCKED_PROFESSION_TITLE_PHRASES,
    DEFAULT_ENGINEERING_TITLE_KEYWORDS,
    DEFAULT_ENGINEERING_TITLE_PHRASES,
    SearchPolicy,
    Settings,
)


@dataclass(slots=True, frozen=True)
class WebhookSettings:
    telegram_bot_token: str
    telegram_webhook_secret: str
    github_repository: str
    github_state_token: str
    github_state_artifact_name: str = "job-hunter-state"
    github_state_cache_dir: str = "/tmp/job-hunter-state"


def load_settings(config_path: Path) -> Settings:
    with open(config_path) as f:
        data = yaml.safe_load(f)

    gemini_api_key = _require_env("GEMINI_API_KEY")
    candidate_profile = base64.b64decode(_require_env("CANDIDATE_PROFILE_B64")).decode("utf-8")
    cover_letter_template = base64.b64decode(_require_env("COVER_LETTER_TEMPLATE_B64")).decode("utf-8")
    dry_run = os.environ.get("JOB_HUNTER_DRY_RUN", "").strip().lower() in ("1", "true", "yes")

    if not dry_run:
        telegram_bot_token = _require_env("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = _require_env("TELEGRAM_CHAT_ID")
    else:
        telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    policy = SearchPolicy(
        target_titles=data.get("target_titles", []),
        positive_keywords=data.get("positive_keywords", []),
        blocked_title_keywords=data.get("blocked_title_keywords", []),
        salary_floor_eur=data.get("salary_floor_eur", 90000),
        thresholds=data.get("thresholds", {}),
        max_jobs_per_run=data.get("max_jobs_per_run", 35),
        source_minimum_per_run=data.get("source_minimum_per_run", 2),
        source_max_share=data.get("source_max_share", 0.5),
        search_queries=data.get("search_queries", []),
        ats=data.get("ats", {}),
        role_families=data.get("role_families", []),
        search_query_templates=data.get("search_query_templates", []),
        search_domains=data.get("search_domains", []),
        max_search_queries_per_run=data.get("max_search_queries_per_run", 30),
        engineering_title_keywords=list(
            data.get("engineering_title_keywords", DEFAULT_ENGINEERING_TITLE_KEYWORDS)
        ),
        engineering_title_phrases=list(
            data.get("engineering_title_phrases", DEFAULT_ENGINEERING_TITLE_PHRASES)
        ),
        blocked_profession_title_phrases=list(
            data.get(
                "blocked_profession_title_phrases",
                DEFAULT_BLOCKED_PROFESSION_TITLE_PHRASES,
            )
        ),
    )

    return Settings(
        gemini_api_key=gemini_api_key,
        candidate_profile=candidate_profile,
        cover_letter_template=cover_letter_template,
        timezone=data.get("timezone", "Europe/Berlin"),
        scheduled_hour=data.get("scheduled_hour", 9),
        policy=policy,
        dry_run=dry_run,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"),
        db_path=os.environ.get("JOB_HUNTER_DB_PATH", "var/job_hunter.sqlite3"),
    )


def load_webhook_settings() -> WebhookSettings:
    return WebhookSettings(
        telegram_bot_token=_require_env("TELEGRAM_BOT_TOKEN"),
        telegram_webhook_secret=_require_env("TELEGRAM_WEBHOOK_SECRET"),
        github_repository=_require_env("GITHUB_REPOSITORY"),
        github_state_token=_require_env("GITHUB_STATE_TOKEN"),
        github_state_artifact_name=os.environ.get(
            "GITHUB_STATE_ARTIFACT_NAME", "job-hunter-state"
        ),
        github_state_cache_dir=os.environ.get(
            "GITHUB_STATE_CACHE_DIR", "/tmp/job-hunter-state"
        ),
    )


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise ValueError(f"Required environment variable {name!r} is not set")
    return val
