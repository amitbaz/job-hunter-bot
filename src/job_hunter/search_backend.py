from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from bs4 import BeautifulSoup

from job_hunter.normalize import canonicalize_url

logger = logging.getLogger(__name__)

_DDG_URL = "https://duckduckgo.com/html/"
_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


@dataclass(frozen=True, slots=True)
class SearchHit:
    title: str
    url: str


@dataclass(frozen=True, slots=True)
class SearchResponse:
    hits: list[SearchHit]
    backend: str


class SearchBackend(Protocol):
    name: str

    def search(self, query: str) -> SearchResponse: ...


class BraveSearchBackend:
    name = "brave"

    def __init__(self, http, api_key: str) -> None:
        if not api_key.strip():
            raise ValueError("Brave Search API key must be non-empty")
        self._http = http
        self._api_key = api_key.strip()

    def search(self, query: str) -> SearchResponse:
        response = self._http.get(
            _BRAVE_URL,
            params={"q": query, "count": 20, "safesearch": "moderate"},
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self._api_key,
            },
            retry=False,
        )
        response.raise_for_status()
        data = response.json()
        web = data.get("web") if isinstance(data, dict) else None
        results = web.get("results", []) if isinstance(web, dict) else []
        hits: list[SearchHit] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = canonicalize_url(str(item.get("url") or ""))
            if title and url:
                hits.append(SearchHit(title=title, url=url))
        return SearchResponse(hits=hits, backend=self.name)


class DuckDuckGoSearchBackend:
    name = "duckduckgo"

    def __init__(self, http) -> None:
        self._http = http

    def search(self, query: str) -> SearchResponse:
        response = self._http.get(_DDG_URL, params={"q": query})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        hits: list[SearchHit] = []
        for anchor in soup.select("a.result__a"):
            href = anchor.get("href")
            title = anchor.get_text(strip=True)
            if not href or not title or "duckduckgo.com" in href:
                continue
            hits.append(SearchHit(title=title, url=canonicalize_url(href)))
        return SearchResponse(hits=hits, backend=self.name)


class FallbackSearchBackend:
    """Use a preferred search backend and fall back per query on failure."""

    name = "fallback"

    def __init__(self, primary: SearchBackend | None, secondary: SearchBackend) -> None:
        self._primary = primary
        self._secondary = secondary

    def search(self, query: str) -> SearchResponse:
        if self._primary is not None:
            try:
                return self._primary.search(query)
            except Exception:
                logger.warning(
                    "targeted search backend failed; falling back: backend=%s",
                    self._primary.name,
                    exc_info=True,
                )
        return self._secondary.search(query)


def build_search_backend(http, brave_api_key: str | None = None) -> SearchBackend:
    secondary = DuckDuckGoSearchBackend(http)
    primary = BraveSearchBackend(http, brave_api_key) if brave_api_key else None
    return FallbackSearchBackend(primary, secondary)
