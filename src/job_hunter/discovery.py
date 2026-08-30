from __future__ import annotations

import logging
from dataclasses import dataclass, field

from job_hunter.fetching import enrich_job
from job_hunter.http import HttpClient
from job_hunter.models import Job, SearchPolicy
from job_hunter.normalize import canonicalize_url, normalize_text
from job_hunter.prefilter import prefilter_job
from job_hunter.store import JobStore

logger = logging.getLogger(__name__)

_ATS_HOSTS = ("jobs.ashbyhq.com", "jobs.lever.co", "boards.greenhouse.io")


@dataclass(slots=True)
class DiscoveryStats:
    raw: int = 0
    unique: int = 0
    prefilter_rejected: int = 0
    eligible: int = 0
    per_source: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class DiscoveryResult:
    eligible: list[tuple[int, Job]]
    rediscovered_job_ids: list[int]
    stats: DiscoveryStats


def candidate_url_key(job: Job) -> str | None:
    if not job.url:
        return None
    return canonicalize_url(job.url)


def candidate_identity_key(job: Job) -> str:
    return "|".join(normalize_text(value) for value in (job.company, job.title, job.location))


def _is_ats_url(job: Job) -> bool:
    url = (job.url or "").lower()
    return any(host in url for host in _ATS_HOSTS)


def _richness_key(job: Job) -> tuple[bool, bool, bool, bool, bool]:
    return (
        _is_ats_url(job),
        bool(job.description),
        bool(job.company),
        bool(job.location),
        job.remote is not None,
    )


def _merge_fields(richer: Job, weaker: Job) -> Job:
    if not richer.title and weaker.title:
        richer.title = weaker.title
    if not richer.company and weaker.company:
        richer.company = weaker.company
    if not richer.location and weaker.location:
        richer.location = weaker.location
    if not richer.description and weaker.description:
        richer.description = weaker.description
    if richer.remote is None and weaker.remote is not None:
        richer.remote = weaker.remote
    if not richer.url and weaker.url:
        richer.url = weaker.url
    if not richer.source_job_id and weaker.source_job_id:
        richer.source_job_id = weaker.source_job_id
    return richer


def _dedupe(jobs: list[Job]) -> list[Job]:
    """
    Collapse in-run duplicates. Two jobs are the same candidate when their
    canonical URLs match, or (absent a matching URL) when their normalized
    company/title/location triple matches exactly. Union-find lets a job
    bridge both keys (e.g. an aggregator record with only a title/company
    match to one record, and a shared canonical URL with another).
    """
    n = len(jobs)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    url_seen: dict[str, int] = {}
    identity_seen: dict[str, int] = {}

    for i, job in enumerate(jobs):
        url_key = candidate_url_key(job)
        if url_key:
            if url_key in url_seen:
                union(i, url_seen[url_key])
            else:
                url_seen[url_key] = i

        identity_key = candidate_identity_key(job)
        if identity_key != "||":
            if identity_key in identity_seen:
                union(i, identity_seen[identity_key])
            else:
                identity_seen[identity_key] = i

    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    ordered_roots = sorted(clusters, key=lambda root: min(clusters[root]))

    merged: list[Job] = []
    for root in ordered_roots:
        cluster_jobs = sorted(
            (jobs[i] for i in clusters[root]), key=_richness_key, reverse=True
        )
        winner = cluster_jobs[0]
        for weaker in cluster_jobs[1:]:
            winner = _merge_fields(winner, weaker)
        merged.append(winner)

    return merged


def collect_candidates(
    sources: list,
    store: JobStore,
    http: HttpClient,
    policy: SearchPolicy,
) -> DiscoveryResult:
    stats = DiscoveryStats()
    raw_jobs: list[Job] = []

    for source in sources:
        try:
            jobs = source.discover()
        except Exception:
            logger.exception("source discovery failed: %r", source)
            continue

        for job in jobs:
            stats.raw += 1
            stats.per_source[job.source] = stats.per_source.get(job.source, 0) + 1
            raw_jobs.append(job)

    unique_jobs = _dedupe(raw_jobs)
    stats.unique = len(unique_jobs)

    eligible: list[tuple[int, Job]] = []
    rediscovered_job_ids: list[int] = []

    for job in unique_jobs:
        if job.url and not job.description:
            enrich_job(job, http)

        job_id, _is_new, _description_changed = store.upsert_job(job)

        if not store.needs_evaluation(job_id):
            rediscovered_job_ids.append(job_id)
            continue

        prefilter_result = prefilter_job(job, policy)
        if not prefilter_result.should_evaluate:
            stats.prefilter_rejected += 1
            continue

        eligible.append((job_id, job))

    stats.eligible = len(eligible)

    return DiscoveryResult(
        eligible=eligible,
        rediscovered_job_ids=rediscovered_job_ids,
        stats=stats,
    )
