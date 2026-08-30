from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(slots=True)
class Job:
    source: str
    title: str
    company: str = ""
    location: str = ""
    url: str = ""
    description: str = ""
    source_job_id: str | None = None
    remote: bool | None = None


@dataclass(slots=True)
class Evaluation:
    job_id: int
    total_score: int
    scores: dict
    decision: str
    hard_blockers: list
    strengths: list
    gaps: list
    salary_note: str
    location_note: str
    rationale: str
    model: str
    status: str = "ok"


@dataclass(slots=True)
class Material:
    job_id: int
    cover_letter_text: str


@dataclass(slots=True)
class PrefilterResult:
    should_evaluate: bool
    hard_blocker: bool
    reason: str


@dataclass(slots=True)
class SearchPolicy:
    target_titles: list
    positive_keywords: list
    blocked_title_keywords: list
    salary_floor_eur: int
    thresholds: dict
    max_jobs_per_run: int = 25
    search_queries: list = field(default_factory=list)
    ats: dict = field(default_factory=dict)


@dataclass(slots=True)
class Settings:
    gemini_api_key: str
    candidate_profile: str
    cover_letter_template: str
    timezone: str
    scheduled_hour: int
    policy: SearchPolicy
    dry_run: bool = False
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    gemini_model: str = "gemini-2.5-flash-lite"
    db_path: str = "var/job_hunter.sqlite3"


@dataclass(slots=True)
class DigestItem:
    job_id: int
    company: str
    title: str
    score: int
    decision: str
    url: str
    hard_blockers: list


@dataclass(slots=True)
class RunSummary:
    ready_to_apply: int = 0
    possible_matches: int = 0
    skipped: int = 0
    errors: int = 0
