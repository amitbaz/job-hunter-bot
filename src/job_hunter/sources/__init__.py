from __future__ import annotations

from job_hunter.models import Settings

from .arbeitnow import ArbeitnowSource
from .ashby import AshbySource
from .base import JobSource
from .duckduckgo import DuckDuckGoSource
from .greenhouse import GreenhouseSource
from .gmail_staged import GmailStagedSource
from .himalayas import HimalayasSource
from .jobicy import JobicySource
from .lever import LeverSource
from .remotive import RemotiveSource
from .remoteok import RemoteOKSource
from .weworkremotely import WeWorkRemotelySource
from .hackernews import HackerNewsHiringSource
from job_hunter.discovery_queries import generate_search_queries

__all__ = [
    "JobSource",
    "RemotiveSource",
    "ArbeitnowSource",
    "JobicySource",
    "HimalayasSource",
    "GmailStagedSource",
    "DuckDuckGoSource",
    "AshbySource",
    "LeverSource",
    "GreenhouseSource",
    "RemoteOKSource", "WeWorkRemotelySource", "HackerNewsHiringSource",
    "build_sources",
]


def build_sources(settings: Settings, http) -> list[JobSource]:
    sources: list[JobSource] = [
        RemotiveSource(http),
        ArbeitnowSource(http),
        JobicySource(http),
        HimalayasSource(http),
        RemoteOKSource(http),
        WeWorkRemotelySource(http),
        HackerNewsHiringSource(http),
        DuckDuckGoSource(http, generate_search_queries(settings.policy)),
    ]

    ats = settings.policy.ats or {}
    for board in ats.get("ashby", []) or []:
        sources.append(AshbySource(board, http))
    for site in ats.get("lever", []) or []:
        sources.append(LeverSource(site, http))
    for token in ats.get("greenhouse", []) or []:
        sources.append(GreenhouseSource(token, http))

    return sources
