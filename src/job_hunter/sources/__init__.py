from __future__ import annotations

import os
from datetime import date

from job_hunter.circuit_breaker import CircuitBreaker
from job_hunter.discovery_queries import generate_search_queries
from job_hunter.models import Settings
from job_hunter.search_backend import build_search_backend

from .arbeitnow import ArbeitnowSource
from .ashby import AshbySource
from .base import JobSource
from .company_watch import CompanyWatchSource
from .duckduckgo import DuckDuckGoSource
from .greenhouse import GreenhouseSource
from .gmail_staged import GmailStagedSource
from .hackernews import HackerNewsHiringSource
from .himalayas import HimalayasSource
from .jobicy import JobicySource
from .lever import LeverSource
from .remotive import RemotiveSource
from .remoteok import RemoteOKSource
from .targeted_search import TargetedSearchSource
from .weworkremotely import WeWorkRemotelySource
from .yc import YCSource

__all__ = [
    "JobSource",
    "RemotiveSource",
    "ArbeitnowSource",
    "JobicySource",
    "HimalayasSource",
    "GmailStagedSource",
    "CompanyWatchSource",
    "DuckDuckGoSource",
    "TargetedSearchSource",
    "AshbySource",
    "LeverSource",
    "GreenhouseSource",
    "RemoteOKSource",
    "WeWorkRemotelySource",
    "HackerNewsHiringSource",
    "YCSource",
    "build_sources",
]


def build_sources(
    settings: Settings,
    http,
    search_breaker: CircuitBreaker | None = None,
    query_date: date | None = None,
) -> list[JobSource]:
    targeted_backend = build_search_backend(
        http,
        os.environ.get("BRAVE_SEARCH_API_KEY"),
    )
    sources: list[JobSource] = [
        RemotiveSource(http),
        ArbeitnowSource(http),
        JobicySource(http),
        HimalayasSource(http),
        RemoteOKSource(http),
        WeWorkRemotelySource(http),
        HackerNewsHiringSource(http),
        TargetedSearchSource(
            targeted_backend,
            generate_search_queries(settings.policy, query_date),
            breaker=search_breaker,
        ),
    ]
    if settings.policy.yc_job_pages:
        sources.append(YCSource(http, settings.policy.yc_job_pages))

    ats = settings.policy.ats or {}
    for board in ats.get("ashby", []) or []:
        sources.append(AshbySource(board, http))
    for site in ats.get("lever", []) or []:
        sources.append(LeverSource(site, http))
    for token in ats.get("greenhouse", []) or []:
        sources.append(GreenhouseSource(token, http))

    return sources
