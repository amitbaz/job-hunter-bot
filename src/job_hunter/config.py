from __future__ import annotations
import base64
import os
from pathlib import Path

import yaml

from .models import (
    DEFAULT_BLOCKED_PROFESSION_TITLE_PHRASES,
    DEFAULT_ENGINEERING_TITLE_KEYWORDS,
    DEFAULT_ENGINEERING_TITLE_PHRASES,
    CompanyWatchSeed,
    Settings,
    SearchPolicy,
)
from .gmail_models import GmailSettings


def load_gmail_settings() -> GmailSettings:
    return GmailSettings(
        client_id=_require_env("GMAIL_CLIENT_ID"),
        client_secret=_require_env("GMAIL_CLIENT_SECRET"),
        refresh_token=_require_env("GMAIL_REFRESH_TOKEN"),
        gemini_api_key=_require_env("GEMINI_API_KEY"),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"),
        db_path=os.environ.get("JOB_HUNTER_DB_PATH", "var/job_hunter.sqlite3"),
    )


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
        specialist_search_domains=data.get("specialist_search_domains", []),
        specialist_query_templates=data.get("specialist_query_templates", []),
        yc_job_pages=data.get("yc_job_pages", []),
        manual_company_watch=_parse_manual_company_watch(
            data.get("manual_company_watch", [])
        ),
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


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise ValueError(f"Required environment variable {name!r} is not set")
    return val


def _parse_manual_company_watch(entries: object) -> list[CompanyWatchSeed]:
    if not isinstance(entries, list):
        raise ValueError("manual_company_watch must be a list")

    seeds: list[CompanyWatchSeed] = []
    for index, entry in enumerate(entries):
        if isinstance(entry, str):
            company_name = entry.strip()
            if not company_name:
                raise ValueError(
                    f"manual_company_watch[{index}].company_name "
                    "must be a non-empty string"
                )
            seeds.append(CompanyWatchSeed(company_name=company_name))
            continue
        if not isinstance(entry, dict):
            raise ValueError(
                f"manual_company_watch[{index}] must be a non-empty string or mapping"
            )

        company_name = entry.get("company_name")
        if not isinstance(company_name, str) or not company_name.strip():
            raise ValueError(
                f"manual_company_watch[{index}].company_name "
                "must be a non-empty string"
            )
        for field in ("careers_url", "ats_provider", "ats_identifier"):
            value = entry.get(field)
            if value is not None and not isinstance(value, str):
                raise ValueError(
                    f"manual_company_watch[{index}].{field} must be a string or null"
                )
        seeds.append(
            CompanyWatchSeed(
                company_name=company_name.strip(),
                careers_url=entry.get("careers_url", "") or "",
                ats_provider=entry.get("ats_provider"),
                ats_identifier=entry.get("ats_identifier"),
            )
        )
    return seeds
