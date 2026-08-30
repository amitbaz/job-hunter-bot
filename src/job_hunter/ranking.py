from __future__ import annotations

from job_hunter.models import Job, SearchPolicy
from job_hunter.normalize import normalize_text

_ATS_HOSTS = ("jobs.ashbyhq.com", "jobs.lever.co", "boards.greenhouse.io")

_CAREER_SIGNALS = (
    "ownership",
    "architecture",
    "mentorship",
    "roadmap",
    "strategy",
    "leadership",
    "end-to-end",
    "cross-functional",
    "autonomy",
    "greenfield",
)

_REGION_WORDS = frozenset({
    "europe", "emea", "germany", "netherlands", "spain", "portugal",
    "poland", "france", "italy", "austria", "ireland", "switzerland", "uk",
})


def source_quality(job: Job) -> int:
    url = (job.url or "").lower()
    if any(host in url for host in _ATS_HOSTS):
        return 10
    if job.source in {"ashby", "lever", "greenhouse"}:
        return 10
    if job.source in {"remoteok", "remotive", "weworkremotely", "arbeitnow"}:
        return 7
    if job.source == "hackernews":
        return 5
    return 3


def _title_fit(title: str, policy: SearchPolicy) -> int:
    normalized_title = normalize_text(title or "")
    if any(kw in normalized_title for kw in policy.blocked_title_keywords):
        return 0
    if not policy.target_titles:
        return 0
    title_words = set(normalized_title.split())
    best_ratio = 0.0
    for target in policy.target_titles:
        normalized_target = normalize_text(target)
        if normalized_target == normalized_title:
            return 40
        target_words = set(normalized_target.split())
        if not target_words:
            continue
        overlap = len(title_words & target_words) / len(target_words)
        best_ratio = max(best_ratio, overlap)
    return round(40 * best_ratio)


def _strength_evidence(description: str, policy: SearchPolicy) -> int:
    keywords = policy.positive_keywords or []
    if not keywords:
        return 0
    normalized_description = normalize_text(description or "")
    matched = {kw for kw in keywords if normalize_text(kw) in normalized_description}
    return round(25 * len(matched) / len(keywords))


def _career_direction_evidence(description: str) -> int:
    normalized_description = normalize_text(description or "")
    matched = {signal for signal in _CAREER_SIGNALS if signal in normalized_description}
    return round(15 * len(matched) / len(_CAREER_SIGNALS))


def _location_evidence(job: Job) -> int:
    if not job.remote:
        return 0
    location_words = set(normalize_text(job.location or "").split())
    if not location_words:
        return 3
    if location_words & _REGION_WORDS:
        return 10
    return 5


def priority_score(job: Job, policy: SearchPolicy) -> int:
    total = (
        _title_fit(job.title, policy)
        + _strength_evidence(job.description, policy)
        + _career_direction_evidence(job.description)
        + _location_evidence(job)
        + source_quality(job)
    )
    return max(0, min(100, total))


def rank_jobs(jobs: list[tuple[int, Job]], policy: SearchPolicy) -> list[tuple[int, Job, int]]:
    scored = [(job_id, job, priority_score(job, policy)) for job_id, job in jobs]
    return sorted(
        scored,
        key=lambda item: (
            -item[2],
            (item[1].company or "").lower(),
            (item[1].title or "").lower(),
            item[0],
        ),
    )
