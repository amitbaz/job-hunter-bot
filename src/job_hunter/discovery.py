from __future__ import annotations

import logging
from dataclasses import dataclass, field

from job_hunter.canonical import CanonicalResolver
from job_hunter.fetching import enrich_job
from job_hunter.http import HttpClient
from job_hunter.job_identity import job_fallback_identity
from job_hunter.models import Job, SearchPolicy
from job_hunter.normalize import canonicalize_url
from job_hunter.prefilter import prefilter_job
from job_hunter.store import JobStore

logger = logging.getLogger(__name__)

_ATS_HOSTS = ("jobs.ashbyhq.com", "jobs.lever.co", "boards.greenhouse.io")


@dataclass(slots=True)
class DiscoveryStats:
    raw: int = 0
    unique: int = 0
    canonical_resolved: int = 0
    canonical_unresolved: int = 0
    cross_source_duplicates: int = 0
    prefilter_rejected: int = 0
    profession_rejected: int = 0
    eligible: int = 0
    per_source: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class DiscoveryResult:
    eligible: list[tuple[int, Job]]
    rediscovered_job_ids: list[int]
    stats: DiscoveryStats


def candidate_url_key(job: Job) -> str | None:
    url = job.canonical_url or job.url
    if not url:
        return None
    return canonicalize_url(url)


def candidate_identity_key(job: Job) -> str:
    return job_fallback_identity(job.company, job.title, job.location) or ""


def candidate_ats_key(job: Job) -> tuple[str, str, str] | None:
    if not job.ats_provider or not job.ats_board or not job.ats_job_id:
        return None
    return (job.ats_provider, job.ats_board, job.ats_job_id)


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
    if not richer.original_url and weaker.original_url:
        richer.original_url = weaker.original_url
    if not richer.canonical_url and weaker.canonical_url:
        richer.canonical_url = weaker.canonical_url
    if not richer.ats_provider and weaker.ats_provider:
        richer.ats_provider = weaker.ats_provider
    if not richer.ats_board and weaker.ats_board:
        richer.ats_board = weaker.ats_board
    if not richer.ats_job_id and weaker.ats_job_id:
        richer.ats_job_id = weaker.ats_job_id
    return richer


def _dedupe(jobs: list[Job]) -> list[Job]:
    """
    Collapse in-run duplicates. Two jobs are the same candidate when their
    canonical URLs, ATS identities, or exact source-independent fallback
    identities match. Union-find lets one record bridge multiple strong keys.
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
    ats_seen: dict[tuple[str, str, str], int] = {}
    identity_seen: dict[str, int] = {}

    for i, job in enumerate(jobs):
        url_key = candidate_url_key(job)
        if url_key:
            if url_key in url_seen:
                union(i, url_seen[url_key])
            else:
                url_seen[url_key] = i

        ats_key = candidate_ats_key(job)
        if ats_key:
            if ats_key in ats_seen:
                union(i, ats_seen[ats_key])
            else:
                ats_seen[ats_key] = i

        identity_key = candidate_identity_key(job)
        if identity_key:
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
    resolver: CanonicalResolver | None = None,
) -> DiscoveryResult:
    """Discover, canonicalize, deduplicate, persist, and prefilter jobs.

    Source failures and unresolved canonical lookups remain non-fatal. When no
    resolver is supplied, candidates retain their source URLs and canonical
    counters stay at zero.
    """
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
            if job.url:
                job.original_url = job.original_url or job.url
                if resolver is not None:
                    resolution = resolver.resolve(job)
                    if resolution is None:
                        stats.canonical_unresolved += 1
                    else:
                        stats.canonical_resolved += 1
                        job.canonical_url = resolution.url
                        job.url = resolution.url
                        if resolution.ats is not None:
                            job.ats_provider = resolution.ats.provider
                            job.ats_board = resolution.ats.board
                            job.ats_job_id = resolution.ats.job_id
            raw_jobs.append(job)

    # Persist every source copy before collapsing the run so provenance is
    # retained even when only one representative continues to evaluation.
    for job in raw_jobs:
        store.upsert_logical_job(job)

    unique_jobs = _dedupe(raw_jobs)
    stats.unique = len(unique_jobs)
    stats.cross_source_duplicates = stats.raw - stats.unique

    eligible: list[tuple[int, Job]] = []
    rediscovered_job_ids: list[int] = []

    for job in unique_jobs:
        if job.url and not job.description:
            enrich_job(job, http)

        job_id, _is_new, _description_changed = store.upsert_logical_job(job)

        if not store.needs_evaluation(job_id):
            rediscovered_job_ids.append(job_id)
            continue

        prefilter_result = prefilter_job(job, policy)
        if not prefilter_result.should_evaluate:
            if prefilter_result.reason_code == "off_target_profession":
                stats.profession_rejected += 1
            else:
                stats.prefilter_rejected += 1
            continue

        eligible.append((job_id, job))

    stats.eligible = len(eligible)

    return DiscoveryResult(
        eligible=eligible,
        rediscovered_job_ids=rediscovered_job_ids,
        stats=stats,
    )
