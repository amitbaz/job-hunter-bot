from __future__ import annotations

from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from job_hunter.circuit_breaker import CircuitBreaker
from job_hunter.models import Job, SearchQuery
from job_hunter.normalize import canonicalize_url

from .base import logger

_URL = "https://duckduckgo.com/html/"


@dataclass(slots=True)
class DuckDuckGoStats:
    planned_by_market: dict[str, int] = field(default_factory=dict)
    attempted_by_market: dict[str, int] = field(default_factory=dict)
    succeeded_by_market: dict[str, int] = field(default_factory=dict)


def _normalize_query(q: str | SearchQuery) -> SearchQuery:
    if isinstance(q, SearchQuery):
        return q
    return SearchQuery(text=q)


class DuckDuckGoSource:
    def __init__(
        self,
        http,
        queries: list[str | SearchQuery],
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._http = http
        self._queries = [_normalize_query(q) for q in queries]
        self._breaker = breaker
        self.stats = DuckDuckGoStats()
        for q in self._queries:
            key = q.market_id or "legacy"
            self.stats.planned_by_market[key] = (
                self.stats.planned_by_market.get(key, 0) + 1
            )

    def discover(self) -> list[Job]:
        jobs: list[Job] = []
        for index, query in enumerate(self._queries):
            market_key = query.market_id or "legacy"
            if self._breaker is not None and self._breaker.is_open:
                logger.warning(
                    "duckduckgo circuit open; skipping %s remaining queries",
                    len(self._queries) - index,
                )
                break

            self.stats.attempted_by_market[market_key] = (
                self.stats.attempted_by_market.get(market_key, 0) + 1
            )

            try:
                response = self._http.get(_URL, params={"q": query.text})
                response.raise_for_status()
            except Exception:
                logger.warning("duckduckgo query failed: %s", query.text, exc_info=True)
                if self._breaker is not None:
                    self._breaker.record_failure()
                continue

            if self._breaker is not None:
                self._breaker.record_success()

            self.stats.succeeded_by_market[market_key] = (
                self.stats.succeeded_by_market.get(market_key, 0) + 1
            )

            soup = BeautifulSoup(response.text, "html.parser")
            for anchor in soup.select("a.result__a"):
                href = anchor.get("href")
                title = anchor.get_text(strip=True)
                if not href or not title or "duckduckgo.com" in href:
                    continue
                jobs.append(
                    Job(
                        source="duckduckgo",
                        title=title,
                        url=canonicalize_url(href),
                        market_hint=query.market_id,
                    )
                )
        return jobs

