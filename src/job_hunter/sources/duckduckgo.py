from __future__ import annotations

from bs4 import BeautifulSoup

from job_hunter.circuit_breaker import CircuitBreaker
from job_hunter.models import Job
from job_hunter.normalize import canonicalize_url

from .base import logger

_URL = "https://duckduckgo.com/html/"


class DuckDuckGoSource:
    def __init__(
        self,
        http,
        queries: list[str],
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._http = http
        self._queries = queries
        self._breaker = breaker

    def discover(self) -> list[Job]:
        jobs: list[Job] = []
        for index, query in enumerate(self._queries):
            if self._breaker is not None and self._breaker.is_open:
                logger.warning(
                    "duckduckgo circuit open; skipping %s remaining queries",
                    len(self._queries) - index,
                )
                break

            try:
                response = self._http.get(_URL, params={"q": query})
                response.raise_for_status()
            except Exception:
                logger.warning("duckduckgo query failed: %s", query, exc_info=True)
                if self._breaker is not None:
                    self._breaker.record_failure()
                continue

            if self._breaker is not None:
                self._breaker.record_success()

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
                    )
                )
        return jobs
