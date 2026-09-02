from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone

from job_hunter.circuit_breaker import CircuitBreaker
from job_hunter.discovery_queries import generate_search_queries
from job_hunter.models import Settings
from job_hunter.search_backend import build_search_backend
from job_hunter.search_budget import (
    SearchUsageLedger,
    brave_queries_available_today,
    split_queries_for_brave,
)
from job_hunter.store import JobStore

from .arbeitnow import ArbeitnowSource
from .ashby import AshbySource
from .base import JobSource
from .company_watch import CompanyWatchSource
from .devjobs import DevJobsSource
from .duckduckgo import DuckDuckGoSource
from .greenhouse import GreenhouseSource
from .gmail_staged import GmailStagedSource
from .hackernews import HackerNewsHiringSource
from .himalayas import HimalayasSource
from .jobicy import JobicySource
from .learned_ats import LearnedAtsSource
from .lever import LeverSource
from .remotive import RemotiveSource
from .remoteok import RemoteOKSource
from .targeted_search import TargetedSearchSource
from .weworkremotely import WeWorkRemotelySource
from .yc import YCSource

logger = logging.getLogger(__name__)
_DEFAULT_BRAVE_MONTHLY_QUERY_LIMIT = 250

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
    "DevJobsSource",
    "RemoteOKSource",
    "WeWorkRemotelySource",
    "HackerNewsHiringSource",
    "YCSource",
    "LearnedAtsSource",
    "build_sources",
]


def _brave_monthly_query_limit() -> int:
    raw = os.environ.get(
        "BRAVE_MONTHLY_QUERY_LIMIT",
        str(_DEFAULT_BRAVE_MONTHLY_QUERY_LIMIT),
    ).strip()
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "invalid BRAVE_MONTHLY_QUERY_LIMIT=%r; disabling Brave to fail safe",
            raw,
        )
        return 0
    if value <= 0:
        logger.warning(
            "non-positive BRAVE_MONTHLY_QUERY_LIMIT=%r; disabling Brave to fail safe",
            raw,
        )
        return 0
    return value


def build_sources(
    settings: Settings,
    http,
    *,
    store: JobStore | None = None,
    search_breaker: CircuitBreaker | None = None,
    query_date: date | None = None,
) -> list[JobSource]:
    queries = generate_search_queries(settings.policy, query_date)
    brave_api_key = os.environ.get("BRAVE_SEARCH_API_KEY")
    targeted_sources: list[JobSource] = []

    if brave_api_key:
        ledger = SearchUsageLedger(settings.db_path)
        now = datetime.now(timezone.utc)
        monthly_limit = _brave_monthly_query_limit()
        brave_limit = brave_queries_available_today(
            ledger,
            monthly_limit=monthly_limit,
            now=now,
        )
        brave_queries, fallback_queries = split_queries_for_brave(
            queries,
            limit=brave_limit,
        )

        logger.info(
            "Brave search budget: monthly_limit=%s available_today=%s selected=%s fallback=%s",
            monthly_limit,
            brave_limit,
            len(brave_queries),
            len(fallback_queries),
        )

        if brave_queries:
            targeted_sources.append(
                TargetedSearchSource(
                    build_search_backend(
                        http,
                        brave_api_key,
                        enable_brave=True,
                        on_brave_attempt=lambda: ledger.record(
                            provider="brave",
                            occurred_at=datetime.now(timezone.utc),
                        ),
                    ),
                    brave_queries,
                    breaker=search_breaker,
                )
            )
        if fallback_queries:
            targeted_sources.append(
                DuckDuckGoSource(
                    http,
                    fallback_queries,
                    breaker=search_breaker,
                )
            )
    else:
        targeted_sources.append(
            DuckDuckGoSource(
                http,
                queries,
                breaker=search_breaker,
            )
        )

    sources: list[JobSource] = [
        RemotiveSource(http),
        ArbeitnowSource(http),
        JobicySource(http),
        HimalayasSource(http),
        RemoteOKSource(http),
        WeWorkRemotelySource(http),
        HackerNewsHiringSource(http),
        *targeted_sources,
    ]
    if settings.policy.yc_job_pages:
        sources.append(YCSource(http, settings.policy.yc_job_pages))

    if any(
        "devjobs" in (market.direct_sources or [])
        for market in settings.policy.markets
        if market.enabled
    ):
        sources.append(DevJobsSource(http))

    ats = settings.policy.ats or {}
    for board in ats.get("ashby", []) or []:
        sources.append(AshbySource(board, http))
    for site in ats.get("lever", []) or []:
        sources.append(LeverSource(site, http))
    for token in ats.get("greenhouse", []) or []:
        sources.append(GreenhouseSource(token, http))

    if store is not None and settings.policy.max_learned_ats_boards_per_run > 0:
        market_order = [
            market.id for market in settings.policy.markets if market.enabled
        ]
        sources.append(
            LearnedAtsSource(
                store,
                http,
                limit=settings.policy.max_learned_ats_boards_per_run,
                market_order=market_order,
            )
        )

    return sources
