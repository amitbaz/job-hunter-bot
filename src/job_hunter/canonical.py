"""Resolve public job-listing URLs to higher-confidence employer postings."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from job_hunter.fetching import extract_job_page_links
from job_hunter.job_identity import (
    locations_compatible,
    normalize_company_name,
    normalize_job_title,
)
from job_hunter.models import AtsReference, CanonicalResolution, Job

if TYPE_CHECKING:
    from job_hunter.http import HttpClient


def parse_supported_ats_url(url: str) -> AtsReference | None:
    """Return the supported ATS reference encoded by a public job URL, if any."""
    parsed = urlparse(url)
    host = parsed.hostname.lower() if parsed.hostname else ""
    parts = [part for part in parsed.path.split("/") if part]

    if host == "jobs.lever.co" and len(parts) >= 2:
        return AtsReference(provider="lever", board=parts[0], job_id=parts[1])
    if host == "jobs.ashbyhq.com" and len(parts) >= 2:
        return AtsReference(provider="ashby", board=parts[0], job_id=parts[1])
    if host == "boards.greenhouse.io" and len(parts) >= 3 and parts[1] == "jobs":
        return AtsReference(provider="greenhouse", board=parts[0], job_id=parts[2])
    return None


class CanonicalResolver:
    """Resolve a job to a public canonical URL without blocking the pipeline on errors."""

    def __init__(
        self,
        http: HttpClient,
        search_candidates: Callable[[Job], list[Job]],
        watch_target: Callable[[str], AtsReference | None],
    ) -> None:
        self._http = http
        self._search_candidates = search_candidates
        self._watch_target = watch_target

    def resolve(self, job: Job) -> CanonicalResolution | None:
        """Return the first resolution meeting the documented confidence threshold."""
        direct_ats = parse_supported_ats_url(job.url)
        if direct_ats is not None:
            return CanonicalResolution(
                url=job.url,
                ats=direct_ats,
                confidence=1.0,
                method="direct",
            )

        if not job.url:
            return None

        try:
            response = self._http.get(job.url)
            response.raise_for_status()
        except Exception:
            return None

        redirected_ats = parse_supported_ats_url(response.url)
        if redirected_ats is not None:
            return CanonicalResolution(
                url=response.url,
                ats=redirected_ats,
                confidence=0.98,
                method="redirect",
            )

        for url in extract_job_page_links(response.text, response.url):
            ats = parse_supported_ats_url(url)
            if ats is not None:
                return CanonicalResolution(
                    url=url,
                    ats=ats,
                    confidence=0.95,
                    method="embedded",
                )

        try:
            candidates = self._search_candidates(job)
            watch_ats = self._watch_target(job.company)
        except Exception:
            return None

        if watch_ats is not None:
            for candidate in candidates:
                ats = parse_supported_ats_url(candidate.url)
                if _same_ats_board(ats, watch_ats) and _titles_match(job, candidate):
                    return CanonicalResolution(
                        url=candidate.url,
                        ats=ats,
                        confidence=0.92,
                        method="watch_target",
                    )

        for candidate in candidates:
            if _same_company(job, candidate) and _titles_match(job, candidate) and locations_compatible(
                job.location, candidate.location
            ):
                return CanonicalResolution(
                    url=candidate.url,
                    ats=parse_supported_ats_url(candidate.url),
                    confidence=0.90,
                    method="targeted_search",
                )
        return None


def _same_company(left: Job, right: Job) -> bool:
    return bool(normalize_company_name(left.company)) and (
        normalize_company_name(left.company) == normalize_company_name(right.company)
    )


def _titles_match(left: Job, right: Job) -> bool:
    return bool(normalize_job_title(left.title)) and (
        normalize_job_title(left.title) == normalize_job_title(right.title)
    )


def _same_ats_board(left: AtsReference | None, right: AtsReference) -> bool:
    return left is not None and (left.provider, left.board) == (right.provider, right.board)
