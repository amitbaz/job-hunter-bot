from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from job_hunter.gmail_models import (
    AUTO_CONFIDENCE_THRESHOLD,
    SUPPORTED_KINDS,
    ExtractedJob,
    GmailClassification,
    GmailMessage,
)
from job_hunter.normalize import canonicalize_url, normalize_text

if TYPE_CHECKING:
    from job_hunter.gemini import GeminiClient

_VISIBLE_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_KNOWN_PLATFORM_SENDERS = (
    "linkedin.com",
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "workable.com",
)
_CLASSIFICATION_FIELDS = frozenset(
    {
        "kind",
        "confidence",
        "company",
        "role_title",
        "source_job_id",
        "job_urls",
        "jobs",
        "rationale",
    }
)
_JOB_FIELDS = frozenset(
    {
        "source_platform",
        "source_job_id",
        "url",
        "company",
        "title",
        "location",
        "remote",
        "description",
    }
)
_SCHEMA_INSTRUCTION = """Return one JSON object only with keys:
kind, confidence, company, role_title, source_job_id, job_urls, jobs, rationale.
kind must be one of JOB_ALERT, RECRUITER_CONTACT, APPLIED, INTERVIEW, TECHNICAL, OFFER, REJECTED, REVIEW_NEEDED, IRRELEVANT.
confidence must be a number from 0 to 1.
jobs must be an array of objects with source_platform, source_job_id, url, company, title, location, remote, description.
Do not infer facts not present in the email. Keep rationale under 160 characters.
"""


def _is_absolute_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _message_urls(message: GmailMessage) -> list[str]:
    values = [*message.links, *_VISIBLE_URL_PATTERN.findall(message.body)]
    urls: list[str] = []
    for value in values:
        value = value.rstrip(".,;:!?)]}")
        if not _is_absolute_http_url(value):
            continue
        url = canonicalize_url(value)
        if url not in urls:
            urls.append(url)
    return urls


def _job_url_platform(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if host.endswith("linkedin.com") and "/jobs/view" in path:
        return "linkedin"
    if host.endswith("greenhouse.io"):
        return "greenhouse"
    if host.endswith("lever.co"):
        return "lever"
    if host.endswith("ashbyhq.com"):
        return "ashby"
    if host.endswith("workable.com"):
        return "workable"
    return None


def _known_jobs(message: GmailMessage) -> list[ExtractedJob]:
    jobs: list[ExtractedJob] = []
    for index, url in enumerate(_message_urls(message)):
        platform = _job_url_platform(url)
        if platform:
            jobs.append(ExtractedJob(source_platform=platform, url=url, index=index))
    return jobs


def _classification(kind: str, message: GmailMessage, rationale: str) -> GmailClassification:
    jobs = _known_jobs(message)
    return GmailClassification(
        kind=kind,
        confidence=1.0,
        job_urls=[job.url for job in jobs],
        jobs=jobs,
        rationale=rationale,
    )


def is_probably_job_related(message: GmailMessage) -> bool:
    text = normalize_text(" ".join([message.sender, message.subject, message.snippet, message.body]))
    strong_terms = (
        "application",
        "interview",
        "recruiter",
        "hiring",
        "job alert",
        "position",
        "technical assessment",
        "coding challenge",
        "offer",
    )
    sender = normalize_text(message.sender)
    return any(term in text for term in strong_terms) or any(domain in sender for domain in _KNOWN_PLATFORM_SENDERS)


def classify_deterministically(message: GmailMessage) -> GmailClassification | None:
    text = normalize_text(" ".join([message.subject, message.snippet, message.body]))
    sender = normalize_text(message.sender)

    if "offer you the position" in text or "pleased to offer" in text:
        return _classification("OFFER", message, "deterministic offer template")
    if "not be moving forward" in text or "we regret to inform" in text:
        return _classification("REJECTED", message, "deterministic rejection template")
    if "technical assessment" in text or "coding challenge" in text:
        return _classification("TECHNICAL", message, "deterministic technical assessment template")
    if "interview invitation" in text or ("choose a time" in text and "interview" in text):
        return _classification("INTERVIEW", message, "deterministic interview template")
    if "thanks for applying" in text or "received your application" in text:
        return _classification("APPLIED", message, "deterministic application receipt template")
    if "job alert" in text or any(domain in sender for domain in _KNOWN_PLATFORM_SENDERS):
        return _classification("JOB_ALERT", message, "deterministic job alert sender or template")
    if "recruiter" in text:
        return _classification("RECRUITER_CONTACT", message, "deterministic recruiter template")
    return None


def source_candidate_key(job: ExtractedJob) -> str:
    if job.url:
        return "url:" + canonicalize_url(job.url)
    if job.source_job_id:
        return f"id:{job.source_platform.lower()}:{job.source_job_id}"
    return "fallback:" + "|".join(
        [normalize_text(job.company), normalize_text(job.title), str(job.index)]
    )


def _review_needed(rationale: str) -> GmailClassification:
    return GmailClassification(kind="REVIEW_NEEDED", confidence=1.0, rationale=rationale)


def _validate_string(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value.strip()


def _validate_optional_string(data: dict, key: str) -> str | None:
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{key} must be a string or null")
    return value.strip() if isinstance(value, str) else None


def _parse_urls(values: object) -> list[str]:
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError("job_urls must be an array of strings")

    urls: list[str] = []
    for value in values:
        if not _is_absolute_http_url(value):
            raise ValueError("job URLs must be absolute HTTP(S) URLs")
        url = canonicalize_url(value)
        if url not in urls:
            urls.append(url)
    return urls


def _parse_jobs(values: object) -> list[ExtractedJob]:
    if not isinstance(values, list):
        raise ValueError("jobs must be an array")

    jobs: list[ExtractedJob] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict) or set(value) != _JOB_FIELDS:
            raise ValueError("job must contain exactly the supported fields")
        source_platform = _validate_string(value, "source_platform")
        if not source_platform:
            raise ValueError("source_platform must be non-empty")
        url = _validate_string(value, "url")
        if url and not _is_absolute_http_url(url):
            raise ValueError("job URL must be an absolute HTTP(S) URL")
        remote = value.get("remote")
        if remote is not None and not isinstance(remote, bool):
            raise ValueError("remote must be a boolean or null")
        jobs.append(
            ExtractedJob(
                source_platform=source_platform,
                source_job_id=_validate_optional_string(value, "source_job_id"),
                url=canonicalize_url(url) if url else "",
                company=_validate_string(value, "company"),
                title=_validate_string(value, "title"),
                location=_validate_string(value, "location"),
                remote=remote,
                description=_validate_string(value, "description"),
                index=index,
            )
        )
    return jobs


def _parse_semantic_classification(raw: str) -> GmailClassification:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid JSON") from exc
    if not isinstance(data, dict) or set(data) != _CLASSIFICATION_FIELDS:
        raise ValueError("response must contain exactly the supported fields")

    kind = _validate_string(data, "kind")
    if kind not in SUPPORTED_KINDS:
        raise ValueError("unsupported kind")
    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("confidence must be a number from 0 to 1")
    rationale = _validate_string(data, "rationale")
    if len(rationale) >= 160:
        raise ValueError("rationale must be under 160 characters")

    classification = GmailClassification(
        kind=kind,
        confidence=float(confidence),
        company=_validate_string(data, "company"),
        role_title=_validate_string(data, "role_title"),
        source_job_id=_validate_optional_string(data, "source_job_id"),
        job_urls=_parse_urls(data.get("job_urls")),
        jobs=_parse_jobs(data.get("jobs")),
        rationale=rationale,
    )
    if classification.kind == "IRRELEVANT" and (
        classification.company
        or classification.role_title
        or classification.source_job_id
        or classification.job_urls
        or classification.jobs
    ):
        raise ValueError("irrelevant response conflicts with extracted job data")
    return classification


def _build_semantic_prompt(message: GmailMessage) -> str:
    email_data = {
        "sender": message.sender,
        "subject": message.subject,
        "snippet": message.snippet,
        "body": message.body[:20_000],
        "links": _message_urls(message),
    }
    return f"{_SCHEMA_INSTRUCTION}\nEmail data:\n{json.dumps(email_data, ensure_ascii=False)}"


def classify_email(message: GmailMessage, gemini: GeminiClient) -> GmailClassification:
    deterministic = classify_deterministically(message)
    if deterministic is not None:
        return deterministic
    if not is_probably_job_related(message):
        return GmailClassification(kind="IRRELEVANT", confidence=1.0, rationale="no deterministic job signal")

    try:
        classification = _parse_semantic_classification(
            gemini.generate_text(_build_semantic_prompt(message), json_mode=True)
        )
    except Exception:
        return _review_needed("semantic classification unavailable or invalid")

    if classification.kind == "REVIEW_NEEDED" or classification.confidence < AUTO_CONFIDENCE_THRESHOLD:
        return _review_needed("semantic classification requires review")
    return classification
