from __future__ import annotations
import base64
import os
from pathlib import Path

import yaml

from .models import Settings, SearchPolicy


def load_settings(config_path: Path) -> Settings:
    with open(config_path) as f:
        data = yaml.safe_load(f)

    gemini_api_key = _require_env("GEMINI_API_KEY")
    candidate_profile = base64.b64decode(_require_env("CANDIDATE_PROFILE_B64")).decode("utf-8")
    cover_letter_template = base64.b64decode(_require_env("COVER_LETTER_TEMPLATE_B64")).decode("utf-8")
    dry_run = bool(os.environ.get("JOB_HUNTER_DRY_RUN", ""))

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
        max_jobs_per_run=data.get("max_jobs_per_run", 25),
        search_queries=data.get("search_queries", []),
        ats=data.get("ats", {}),
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


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise ValueError(f"Required environment variable {name!r} is not set")
    return val
