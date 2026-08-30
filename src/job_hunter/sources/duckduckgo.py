from __future__ import annotations

from bs4 import BeautifulSoup

from job_hunter.models import Job
from job_hunter.normalize import canonicalize_url

from .base import logger

_URL = "https://duckduckgo.com/html/"


class DuckDuckGoSource:
    def __init__(self, http, queries: list[str]) -> None:
        self._http = http
        self._queries = queries

    def discover(self) -> list[Job]:
        jobs: list[Job] = []
        for query in self._queries:
            try:
                response = self._http.get(_URL, params={"q": query})
                response.raise_for_status()
            except Exception:
                logger.warning("duckduckgo query failed: %s", query, exc_info=True)
                continue

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
