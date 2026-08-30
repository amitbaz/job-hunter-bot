from __future__ import annotations
from dataclasses import dataclass, field

DEFAULT_ENGINEERING_TITLE_KEYWORDS = [
    "engineer",
    "developer",
]

DEFAULT_ENGINEERING_TITLE_PHRASES = [
    "technical lead",
    "frontend lead",
    "front-end lead",
    "software lead",
    "engineering lead",
    "software architect",
    "frontend architect",
    "front-end architect",
    "web architect",
]

DEFAULT_BLOCKED_PROFESSION_TITLE_PHRASES = [
    "product manager",
    "platform product manager",
    "technical product manager",
    "product designer",
    "ux designer",
    "ui designer",
    "product marketing manager",
    "program manager",
    "project manager",
    "customer success manager",
    "solutions consultant",
    "sales engineer",
    "solutions engineer",
    "support engineer",
    "data engineer",
    "machine learning engineer",
    "ml engineer",
    "data scientist",
    "ml researcher",
    "machine learning researcher",
    "ios engineer",
    "android engineer",
    "mobile engineer",
    "embedded engineer",
]


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
    reason_code: str = ""


@dataclass(slots=True)
class SearchPolicy:
    target_titles: list
    positive_keywords: list
    blocked_title_keywords: list
    salary_floor_eur: int
    thresholds: dict
    max_jobs_per_run: int = 35
    source_minimum_per_run: int = 2
    source_max_share: float = 0.5
    search_queries: list = field(default_factory=list)
    ats: dict = field(default_factory=dict)
    role_families: list[str] = field(default_factory=list)
    search_query_templates: list[str] = field(default_factory=list)
    search_domains: list[str] = field(default_factory=list)
    max_search_queries_per_run: int = 30
    engineering_title_keywords: list[str] = field(
        default_factory=lambda: list(DEFAULT_ENGINEERING_TITLE_KEYWORDS)
    )
    engineering_title_phrases: list[str] = field(
        default_factory=lambda: list(DEFAULT_ENGINEERING_TITLE_PHRASES)
    )
    blocked_profession_title_phrases: list[str] = field(
        default_factory=lambda: list(DEFAULT_BLOCKED_PROFESSION_TITLE_PHRASES)
    )


@dataclass(slots=True)
class CandidatePreferences:
    preferred_roles: list[str]
    preferred_seniority: list[str]
    must_have_signals: list[str]
    nice_to_have_signals: list[str]
    preferred_locations: list[str]
    avoid_signals: list[str]
    summary: str


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
    gemini_model: str = "gemini-3.6-flash"
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
