from __future__ import annotations

from job_hunter.models import Settings

from .arbeitnow import ArbeitnowSource
from .ashby import AshbySource
from .base import JobSource
from .duckduckgo import DuckDuckGoSource
from .greenhouse import GreenhouseSource
from .lever import LeverSource
from .remotive import RemotiveSource

__all__ = [
    "JobSource",
    "RemotiveSource",
    "ArbeitnowSource",
    "DuckDuckGoSource",
    "AshbySource",
    "LeverSource",
    "GreenhouseSource",
    "build_sources",
]


def build_sources(settings: Settings, http) -> list[JobSource]:
    sources: list[JobSource] = [
        RemotiveSource(http),
        ArbeitnowSource(http),
        DuckDuckGoSource(http, settings.policy.search_queries),
    ]

    ats = settings.policy.ats or {}
    for board in ats.get("ashby", []) or []:
        sources.append(AshbySource(board, http))
    for site in ats.get("lever", []) or []:
        sources.append(LeverSource(site, http))
    for token in ats.get("greenhouse", []) or []:
        sources.append(GreenhouseSource(token, http))

    return sources
